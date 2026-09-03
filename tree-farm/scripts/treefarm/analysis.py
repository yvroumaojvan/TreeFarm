# -*- coding: utf-8 -*-
"""树场机制 —— 代码分析层（v4.7；v3.6 拆分自单文件 tree_farm.py）。

包含：死代码检测（4 语言）/ 循环依赖检测 / 影响分析 / 代码复杂度（Python AST +
JS/Java/Go 轻量启发式）/ 架构分层识别。全部基于基因库 + 轻量解析器，零依赖。
"""

import ast
import os
import re
from collections import deque
from typing import Any, Deque, Dict, List, Optional, Set, Tuple

from .common import _gram_hashes, read_text
from .parser import (_func_body_end, _go_defs, _java_defs, _js_defs, _rust_defs,
                     _count_complexity, _lineno_of,
                     _strip_go_noise, _strip_java_noise,
                     _strip_js_noise, _strip_rust_noise,
                     extract_go_call_graph, extract_java_call_graph,
                     extract_js_call_graph, extract_rust_call_graph)


def detect_dead_code(tree_files: List[str]) -> Dict[str, Any]:
    """死代码检测：找出从未被调用的函数和类（v3.5：扩展到 JS/TS/Java/Go）。

    基于函数调用图：定义了但从未被调用的符号 = 死代码。
    Python 走 AST 精确分析；JS/Java/Go 走轻量解析器（启发式，可能误判，结果需人工确认）。
    注意：入口函数（main/run/start 等）、魔术方法、被字符串/反射调用的符号可能被误判。

    返回: {"dead_functions": [(file, name, lineno)], "dead_classes": [(file, name, lineno)],
           "total_functions": int, "total_classes": int, "dead_ratio": float}
    """
    all_functions: Dict[str, List[Tuple[str, int]]] = {}  # 函数名 → [(文件, 行号)]
    all_classes: Dict[str, List[Tuple[str, int]]] = {}    # 类名 → [(文件, 行号)]
    called: Set[str] = set()                              # 被调用名（含类实例化）
    inherited: Set[str] = set()                           # 被继承的类名
    ENTRY_NAMES = {"main", "run", "start", "init", "setup"}

    for f in tree_files:
        ext = os.path.splitext(f)[1].lower()
        text = read_text(f)
        if not text:
            continue
        if ext == ".py":
            try:
                tree = ast.parse(text)
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    all_functions.setdefault(node.name, []).append((f, node.lineno))
                elif isinstance(node, ast.ClassDef):
                    all_classes.setdefault(node.name, []).append((f, node.lineno))
                    for base in node.bases:
                        base_name = base.id if isinstance(base, ast.Name) else (
                            base.attr if isinstance(base, ast.Attribute) else None)
                        if base_name:
                            inherited.add(base_name)
                elif isinstance(node, ast.Call):
                    if isinstance(node.func, ast.Name):
                        called.add(node.func.id)
                    elif isinstance(node.func, ast.Attribute):
                        called.add(node.func.attr)
        elif ext in (".js", ".ts"):
            clean = _strip_js_noise(text)
            for name, _, ln, kind in _js_defs(clean):
                target = all_classes if kind == "class" else all_functions
                target.setdefault(name, []).append((f, ln))
            _, js_calls = extract_js_call_graph(f)
            called.update(c.split(".")[-1] for c in js_calls)
            called.update(re.findall(r"\bnew\s+([A-Za-z_$][\w$]*)\s*\(", clean))  # 类实例化
        elif ext == ".java":
            clean = _strip_java_noise(text)
            for name, _, ln, kind in _java_defs(clean):
                target = all_classes if kind == "class" else all_functions
                target.setdefault(name, []).append((f, ln))
            java_calls, java_inherits = extract_java_call_graph(f)
            called.update(c.split(".")[-1] for c in java_calls)
            called.update(re.findall(r"\bnew\s+([A-Za-z_$][\w$]*)\s*\(", clean))  # 类实例化
            inherited.update(java_inherits)
        elif ext == ".go":
            clean = _strip_go_noise(text)
            for name, _, ln, kind in _go_defs(clean):
                target = all_classes if kind == "class" else all_functions
                target.setdefault(name, []).append((f, ln))
            go_calls, go_inherits = extract_go_call_graph(f)
            called.update(c.split(".")[-1] for c in go_calls)
            # Go 无 new 关键字实例化；结构体字面量 &T{} / new(T) / 变量类型 T 均视为使用
            called.update(re.findall(r"&\s*([A-Za-z_]\w*)\s*\{", clean))
            called.update(re.findall(r"\bnew\s*\(\s*([A-Za-z_]\w*)", clean))
            inherited.update(go_inherits)
        elif ext == ".rs":
            clean = _strip_rust_noise(text)
            for name, _, ln, kind in _rust_defs(clean):
                target = all_classes if kind == "class" else all_functions
                target.setdefault(name, []).append((f, ln))
            rust_calls, rust_inherits = extract_rust_call_graph(f)
            called.update(c.split(".")[-1] for c in rust_calls)
            # Rust 类型使用（降低死类误报）：
            # 结构体字面量 Greeter { } / impl Trait for Greeter { → used（排除定义行）
            for m in re.finditer(r"\b([A-Z][A-Za-z_]\w*)\s*\{", clean):
                pre = clean[max(0, m.start() - 40):m.start()]
                if not re.search(r"\b(?:struct|enum|trait|impl|mod|use)\s*$", pre):
                    called.add(m.group(1))
            called.update(re.findall(r"\bimpl\s+([A-Za-z_]\w*)\s+for\b", clean))   # impl Trait for X → trait 算使用
            called.update(re.findall(r":[^;=\n]*?\b([A-Z][A-Za-z_]\w*)\b", clean))  # let x: Type / fn f(p: Type) / 泛型约束
            inherited.update(rust_inherits)

    # v4.0 改进：降低误报率
    # 1. 收集所有导出符号（__all__）
    exported_names = set()
    # 2. 收集装饰器注册的函数（@app.route, @register, @callback等）
    registered_names = set()
    # 3. 收集字符串形式的调用（getattr, "func_name"等）
    string_calls = set()

    for f in tree_files:
        ext = os.path.splitext(f)[1].lower()
        if ext != ".py":
            continue
        try:
            text = read_text(f)
        except Exception:
            continue
        # 收集 __all__ 导出
        all_match = re.search(r'__all__\s*=\s*\[([^\]]*)\]', text, re.DOTALL)
        if all_match:
            for name in re.findall(r'["\'](\w+)["\']', all_match.group(1)):
                exported_names.add(name)
        # 收集装饰器注册的函数
        for m in re.finditer(r'@(\w+(?:\.\w+)*)\s*(?:\([^)]*\))?\s*\n\s*(?:async\s+)?def\s+(\w+)', text):
            decorator = m.group(1)
            func_name = m.group(2)
            # 常见的注册型装饰器
            if any(k in decorator.lower() for k in ('route', 'register', 'callback', 'handler',
                                                       'listener', 'command', 'event', 'hook',
                                                       'middleware', 'before_', 'after_', 'app.',
                                                       'blueprint', 'api.', 'task', 'job')):
                registered_names.add(func_name)
        # 收集字符串形式的函数引用
        for m in re.finditer(r'["\'](\w{2,})["\']', text):
            name = m.group(1)
            if name in all_functions:
                string_calls.add(name)
        # getattr 动态调用
        for m in re.finditer(r'getattr\s*\([^,]+,\s*["\'](\w+)["\']', text):
            string_calls.add(m.group(1))

    # 合并所有"可能被使用"的名称
    called.update(exported_names)
    called.update(registered_names)
    called.update(string_calls)

    # v4.0 改进：死代码判定添加置信度
    def _is_test_file(path: str) -> bool:
        """判断是否为测试文件"""
        basename = os.path.basename(path)
        return (basename.startswith('test_') or basename.endswith('_test.py')
                or '/tests/' in path or '/test/' in path)

    def _is_public_api(name: str, path: str) -> bool:
        """判断是否为公共API（不以_开头，且不在测试文件中）"""
        return not name.startswith('_') and not _is_test_file(path)

    dead_functions = []
    for name, locs in all_functions.items():
        # 排除魔术方法
        if name.startswith("__") and name.endswith("__"):
            continue
        # 排除入口函数
        if name in ENTRY_NAMES:
            continue
        # 排除被调用的
        if name in called:
            continue
        for loc in locs:
            fpath, lineno = loc
            # v4.0：计算置信度
            confidence = "high"
            reasons = []
            # 测试文件中的函数置信度低（可能被测试框架调用）
            if _is_test_file(fpath):
                confidence = "low"
                reasons.append("测试文件，可能被测试框架调用")
            # 公共API置信度低（可能被外部模块调用）
            elif _is_public_api(name, fpath):
                confidence = "medium"
                reasons.append("公共API，可能被外部调用")
            # 以_开头的私有函数置信度高
            elif name.startswith('_'):
                confidence = "high"
                reasons.append("私有函数，仅内部使用")

            dead_functions.append({
                "file": fpath, "name": name, "line": lineno,
                "confidence": confidence, "reasons": reasons
            })

    dead_classes = []
    for name, locs in all_classes.items():
        if name in called or name in inherited:
            continue
        for loc in locs:
            fpath, lineno = loc
            confidence = "high"
            reasons = []
            if _is_test_file(fpath):
                confidence = "low"
                reasons.append("测试文件中的类")
            elif name[0].isupper():
                confidence = "medium"
                reasons.append("公共类，可能被外部实例化")
            dead_classes.append({
                "file": fpath, "name": name, "line": lineno,
                "confidence": confidence, "reasons": reasons
            })

    total_all = sum(len(v) for v in all_functions.values()) \
        + sum(len(v) for v in all_classes.values())
    # v4.0：只统计高置信度的死代码比例
    high_conf_dead = sum(1 for d in dead_functions if d["confidence"] == "high") + \
                     sum(1 for d in dead_classes if d["confidence"] == "high")
    dead_ratio = high_conf_dead / total_all if total_all else 0.0

    return {"dead_functions": dead_functions, "dead_classes": dead_classes,
            "total_functions": sum(len(v) for v in all_functions.values()),
            "total_classes": sum(len(v) for v in all_classes.values()),
            "dead_ratio": dead_ratio,
            "high_confidence_count": high_conf_dead,
            "exported_count": len(exported_names),
            "registered_count": len(registered_names)}


def detect_circular_dependencies(bank: Any, module_map: Dict[str, str]) -> Dict[str, Any]:
    """循环依赖检测：基于基因库 import 关系，DFS 三色标记找环。

    返回: {"cycles": [[mod1, mod2, ...], ...], "total_modules": int, "has_cycle": bool}
    环按模块名列表展示（a → b → a）；同一组模块的环只报一次。
    """
    graph: Dict[str, Set[str]] = {}
    all_modules: Set[str] = set()
    path_of = {path: mod for mod, path in module_map.items()}
    for g in bank.all_genes():
        if g["relation"] != "import":
            continue
        source_mod = path_of.get(g["source"])
        target = g["target"]
        if not source_mod or not target or target.startswith("."):
            continue  # 找不到源模块 / 相对导入（. / ..pkg）不参与环检测
        graph.setdefault(source_mod, set()).add(target)
        all_modules.add(source_mod)
        all_modules.add(target)

    WHITE, GRAY, BLACK = 0, 1, 2
    color = {mod: WHITE for mod in all_modules}
    cycles: List[List[str]] = []
    path: List[str] = []
    cycle_keys: Set[Tuple[str, ...]] = set()

    def dfs(node: str) -> None:
        color[node] = GRAY
        path.append(node)
        for nxt in graph.get(node, set()):
            if nxt not in color:
                continue
            if color[nxt] == GRAY:
                start = path.index(nxt)
                cycle = path[start:] + [nxt]
                key = tuple(sorted(cycle[:-1]))
                if key not in cycle_keys:
                    cycle_keys.add(key)
                    cycles.append(cycle)
            elif color[nxt] == WHITE:
                dfs(nxt)
        path.pop()
        color[node] = BLACK

    for mod in all_modules:
        if color[mod] == WHITE:
            dfs(mod)

    cycles.sort(key=lambda c: (len(c), c))
    return {"cycles": cycles, "total_modules": len(all_modules), "has_cycle": len(cycles) > 0}


def impact_analysis(bank: Any, module_map: Dict[str, str], target_file: str,
                    target_symbol: Optional[str] = None, max_depth: int = 3) -> Dict[str, Any]:
    """影响分析：修改某个文件/函数会影响哪些地方（BFS 反向遍历调用图）。

    基因格式（v3）：import 基因 target=模块名；call/inherit 基因 target=文件路径、symbol=符号名。
    语言无关（v3.6）：Python/JS/Java/Go 的函数级 call 基因都会入库，因此对任意语言文件
    都能给出受影响清单——修改 JS/Java/Go 文件同样生效。
    返回: {"target": str, "symbol": Optional[str], "impacted_files": [(file, depth)],
           "impacted_functions": [(file, func, depth)], "total_impacted": int,
           "max_depth_reached": int}
    """
    # 反向图：谁依赖我（key 是模块名或文件路径）
    reverse: Dict[str, Set[Any]] = {}
    for g in bank.all_genes():
        source, target = g["source"], g["target"]
        if g["relation"] == "import":
            reverse.setdefault(target, set()).add(source)          # 模块名 → 依赖它的文件
        elif g["relation"] in ("call", "inherit"):
            reverse.setdefault(target, set()).add((source, g.get("symbol", "")))  # 文件 → (调用方, 符号)

    # 文件路径 ↔ 模块名 双向桥（BFS 里两类节点要能互相走通）
    path_of = {path: mod for mod, path in module_map.items()}

    target = os.path.abspath(target_file)
    visited_files: Set[str] = set()
    impacted_files: Dict[str, int] = {}
    impacted_functions: Dict[Tuple[str, str], int] = {}
    queue: Deque[Tuple[Any, int]] = deque()
    target_mod = path_of.get(target)
    if target_mod:
        queue.append((target_mod, 0))
    queue.append((target, 0))

    max_depth_reached = 0
    while queue:
        current, depth = queue.popleft()
        if depth >= max_depth:
            continue
        max_depth_reached = max(max_depth_reached, depth)
        nxt_depth = depth + 1
        for caller in reverse.get(current, set()):
            if isinstance(caller, tuple):
                caller_file, sym = caller
                if caller_file not in visited_files:
                    visited_files.add(caller_file)
                    impacted_files[caller_file] = nxt_depth
                    if sym:
                        impacted_functions[(caller_file, sym)] = nxt_depth
                    queue.append((caller_file, nxt_depth))
            else:
                if caller not in visited_files:
                    visited_files.add(caller)
                    impacted_files[caller] = nxt_depth
                    queue.append((caller, nxt_depth))
        # 文件节点 → 它的模块名也要入队（别人 import 它也算影响）
        if isinstance(current, str) and os.path.isfile(current):
            mod = path_of.get(current)
            if mod and mod != current:
                queue.append((mod, depth))

    impacted_files_list = sorted(impacted_files.items(), key=lambda x: (x[1], x[0]))
    impacted_functions_list = sorted(
        ((f, func, d) for (f, func), d in impacted_functions.items()),
        key=lambda x: (x[2], x[0], x[1]))
    return {"target": target, "symbol": target_symbol,
            "impacted_files": impacted_files_list,
            "impacted_functions": impacted_functions_list,
            "total_impacted": len(impacted_files) + len(impacted_functions),
            "max_depth_reached": max_depth_reached}


def calculate_complexity(py_file: str) -> Optional[Dict[str, Any]]:
    """代码复杂度：圈复杂度（1 + 每个 if/for/while/except/and/or/with/try 加 1）+ 行数 + 嵌套。

    返回: {"file": str, "total_lines": int, "total_functions": int,
           "avg_complexity": float, "max_complexity": int,
           "functions": [(name, complexity, lines, lineno)]}（按复杂度降序）
    非 Python 文件 / 解析失败返回 None。
    """
    if not py_file.endswith(".py"):
        return None
    text = read_text(py_file)
    if not text:
        return None
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return None

    total_lines = text.count("\n") + 1
    functions: List[Tuple[str, int, int, int]] = []

    def get_complexity(node: ast.AST) -> int:
        complexity = 1
        for child in ast.walk(node):
            if isinstance(child, (ast.If, ast.For, ast.While, ast.With, ast.Try, ast.ExceptHandler)):
                complexity += 1
            elif isinstance(child, ast.BoolOp):
                complexity += len(child.values) - 1
        return complexity

    def get_lines(node: ast.AST) -> int:
        end = getattr(node, "end_lineno", None)
        return end - node.lineno + 1 if end else 0

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.append((node.name, get_complexity(node), get_lines(node), node.lineno))
        elif isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    functions.append((f"{node.name}.{item.name}", get_complexity(item),
                                      get_lines(item), item.lineno))

    if not functions:
        return {"file": py_file, "total_lines": total_lines, "total_functions": 0,
                "avg_complexity": 0.0, "max_complexity": 0, "functions": []}
    complexities = [c for _, c, _, _ in functions]
    functions.sort(key=lambda x: -x[1])
    return {"file": py_file, "total_lines": total_lines, "total_functions": len(functions),
            "avg_complexity": round(sum(complexities) / len(complexities), 2),
            "max_complexity": max(complexities), "functions": functions}


def calculate_complexity_any(path: str) -> Optional[Dict[str, Any]]:
    """代码复杂度（v3.5：扩展到 JS/TS/Java/Go，Python 仍走 AST）：
    圈复杂度 + 行数 + 嵌套。返回格式与 calculate_complexity 一致。
    Python 走 AST 精确分析；JS/Java/Go 走轻量启发式（函数体括号配对 + 关键词计数）。
    非支持文件 / 解析失败返回 None。"""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".py":
        return calculate_complexity(path)
    if ext in (".js", ".ts"):
        lang, defs_fn, strip_fn = "js", _js_defs, _strip_js_noise
    elif ext == ".java":
        lang, defs_fn, strip_fn = "java", _java_defs, _strip_java_noise
    elif ext == ".go":
        lang, defs_fn, strip_fn = "go", _go_defs, _strip_go_noise
    elif ext == ".rs":
        lang, defs_fn, strip_fn = "rust", _rust_defs, _strip_rust_noise
    else:
        return None
    text = read_text(path)
    if not text:
        return None
    clean = strip_fn(text)
    total_lines = text.count("\n") + 1
    functions: List[Tuple[str, int, int, int]] = []
    for name, off, ln, kind in defs_fn(clean):
        if kind != "func":
            continue
        end = _func_body_end(clean, off)
        if end is not None:
            body = clean[off:end + 1]
            func_lines = clean[:end].count("\n") - clean[:off].count("\n") + 1
        else:
            body = clean[off:off + 200]      # 无函数体（接口方法/箭头表达式）→ 只统计声明区
            func_lines = 1
        functions.append((name, _count_complexity(body, lang), func_lines, ln))

    if not functions:
        return {"file": path, "total_lines": total_lines, "total_functions": 0,
                "avg_complexity": 0.0, "max_complexity": 0, "functions": []}
    complexities = [c for _, c, _, _ in functions]
    functions.sort(key=lambda x: -x[1])
    return {"file": path, "total_lines": total_lines, "total_functions": len(functions),
            "avg_complexity": round(sum(complexities) / len(complexities), 2),
            "max_complexity": max(complexities), "functions": functions}


def detect_architecture_layers(tree_files: List[str], bank: Any,
                               module_map: Dict[str, str],
                               root: Optional[str] = None) -> Dict[str, Any]:
    """架构分层识别：按文件/目录命名关键词归类表现层/业务层/数据层/工具层。
    语言无关（v3.6）：匹配对象是文件路径，Python/JS/Java/Go 一视同仁。

    修复 v2.2 bug：关键词匹配用【相对路径】而非绝对路径——
    绝对路径里的 storage/tmp 等词会污染分层结果（如 /storage/emulated/0 误命中数据层）。
    root 传入项目根目录时按相对路径匹配；不传则回退绝对路径（兼容旧调用）。
    bank / module_map 参数保留（与其它分析函数签名一致），当前实现仅基于命名启发式。
    返回: {"layers": {presentation/business/data/util/unknown: [files]},
           "layer_count": int, "has_clear_layers": bool}
    """
    LAYER_KEYWORDS = {
        "presentation": {"ui", "view", "controller", "handler", "page", "screen",
                         "widget", "component", "frontend", "client", "web", "api"},
        "business": {"service", "manager", "business", "logic", "core", "domain",
                     "usecase", "use_case", "processor"},
        "data": {"model", "repository", "dao", "database", "db", "storage",
                 "entity", "schema", "mapper", "persistence"},
        "util": {"util", "utils", "helper", "helpers", "common", "base",
                 "tool", "tools", "lib", "library"},
    }
    layers = {"presentation": [], "business": [], "data": [], "util": [], "unknown": []}

    for f in tree_files:
        match_path = os.path.relpath(f, root) if root else f
        basename = os.path.basename(match_path).lower()
        dirname = os.path.dirname(match_path).lower()
        name_no_ext = os.path.splitext(basename)[0]
        layer = None
        for key, keywords in LAYER_KEYWORDS.items():
            if any(kw in dirname or kw in name_no_ext for kw in keywords):
                layer = key
                break
        layers[layer if layer else "unknown"].append(f)

    for key in layers:
        layers[key].sort()
    layer_count = sum(1 for files in layers.values() if files)
    has_clear_layers = layer_count >= 3 and len(layers["unknown"]) < len(tree_files) * 0.5
    return {"layers": layers, "layer_count": layer_count, "has_clear_layers": has_clear_layers}


# ========== 代码异味检测（v3.7 新增，零依赖启发式） ==========
# 魔法数字排除表：0/1/2/-1 等"常见无害数字"不报（v3.7 报告 P0-3）
_MAGIC_EXCLUDE = {"0", "1", "2", "3", "-1", "0.0", "1.0", "0.5", "100", "1000"}


def _magic_numbers(clean: str, limit: int = 5) -> List[Tuple[str, int]]:
    """提取魔法数字（未命名常量）。排除常见数字与 0x/0b/0o 字面量。
    返回 [(数字, offset)]，最多 limit 个（防刷屏）。"""
    text = re.sub(r"\b0[xXbBoO][0-9a-fA-F_]+", " ", clean)
    found: List[Tuple[str, int]] = []
    for m in re.finditer(r"(?<![\w.])(-?\d+(?:\.\d+)?)(?![\w.])", text):
        num = m.group(1)
        if num in _MAGIC_EXCLUDE:
            continue
        found.append((num, m.start()))
        if len(found) >= limit:
            break
    return found


def _dup_conditions(clean: str, lang: str) -> List[Tuple[str, int]]:
    """重复条件分支检测：if / else if 链里出现过的条件表达式（规范化后）再次出现。
    Python 用 if 条件正则（else: if 链），其余语言用 if (...) 括号形式。"""
    if lang == "py":
        conds = [(re.sub(r"\s+", "", m.group(1)), m.start(1))
                 for m in re.finditer(r"(?m)^\s*(?:elif|if)\s+(.+?):", clean)]
    else:
        conds = [(re.sub(r"\s+", "", m.group(1)), m.start(1))
                 for m in re.finditer(r"\b(?:else\s+)?if\s*\(([^)]*)\)", clean)]
    seen: Dict[str, int] = {}
    dup: List[Tuple[str, int]] = []
    for cond, off in conds:
        if not cond:
            continue
        if cond in seen:
            dup.append((cond, off))
        else:
            seen[cond] = off
    return dup


def _is_test_file(path: str) -> bool:
    """测试文件判断（魔法数字/重复条件等易误报项跳过测试文件）。"""
    base = os.path.basename(path)
    return ("test_" in base or base.startswith("test") or "_test" in base
            or base.endswith("_test.go") or ".test." in base or "/tests/" in path)


def _py_smells(text: str, path: str, root: Optional[str],
               long_lines: int, max_params: int, max_nesting: int,
               big_lines: int) -> List[Dict[str, Any]]:
    """Python 代码异味（AST 精确）：长函数/长参数/嵌套过深/大类/重复条件/魔法数字。
    重复条件与魔法数字走 AST（字符串/注释里的文本不会误报）。"""
    smells: List[Dict[str, Any]] = []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return smells
    rel = os.path.relpath(path, root) if root else path
    is_test = _is_test_file(path)

    def _nesting(node: ast.AST, depth: int) -> int:
        d = depth
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.If, ast.For, ast.While, ast.With, ast.Try)):
                d = max(d, _nesting(child, depth + 1))
            else:
                d = max(d, _nesting(child, depth))
        return d

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            lineno = node.lineno
            lines = (getattr(node, "end_lineno", lineno) or lineno) - lineno + 1
            if lines > long_lines:
                smells.append({
                    "type": "long_function", "name": node.name, "line": lineno,
                    "severity": "high" if lines > long_lines * 2 else "medium",
                    "file": rel, "detail": f"{lines} 行（阈值 {long_lines}）",
                    "suggestion": "拆分为多个职责单一的小函数"})
            n_args = len(node.args.args) + len(node.args.kwonlyargs)
            if n_args > max_params:
                smells.append({
                    "type": "long_parameter_list", "name": node.name, "line": lineno,
                    "severity": "high" if n_args > max_params * 2 else "medium",
                    "file": rel, "detail": f"{n_args} 个参数（阈值 {max_params}）",
                    "suggestion": "参数收拢为配置对象 / 数据类"})
            nest = _nesting(node, 0)
            if nest > max_nesting:
                smells.append({
                    "type": "deep_nesting", "name": node.name, "line": lineno,
                    "severity": "high" if nest > max_nesting + 2 else "medium",
                    "file": rel, "detail": f"最大嵌套 {nest} 层（阈值 {max_nesting}）",
                    "suggestion": "提前 return 消除分支 / 抽出守卫子句"})
        elif isinstance(node, ast.ClassDef):
            lineno = node.lineno
            lines = (getattr(node, "end_lineno", lineno) or lineno) - lineno + 1
            n_methods = sum(1 for n in ast.walk(node)
                            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)))
            if lines > big_lines or n_methods > 20:
                smells.append({
                    "type": "god_class", "name": node.name, "line": lineno,
                    "severity": "high" if lines > big_lines * 2 or n_methods > 30 else "medium",
                    "file": rel, "detail": f"{lines} 行 / {n_methods} 个方法（阈值 {big_lines} 行或 20 方法）",
                    "suggestion": "按职责拆分为多个类"})

    # 重复条件（AST 级，if/elif 链里相同条件）
    if_conds: Dict[str, int] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.If):
            seg = (ast.get_source_segment(text, node.test) or "").strip()
            key = re.sub(r"\s+", "", seg)
            if key:
                if key in if_conds:
                    smells.append({
                        "type": "duplicate_condition", "name": "", "line": node.lineno,
                        "severity": "medium", "file": rel, "detail": f"条件重复出现: {seg}",
                        "suggestion": "合并分支 / 提取条件为命名变量"})
                else:
                    if_conds[key] = node.lineno

    # 魔法数字（AST 级；排除 range/xrange 参数与测试文件）
    if not is_test:
        range_args = {id(a) for n in ast.walk(tree)
                      if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                      and n.func.id in ("range", "xrange") for a in n.args}
        for node in ast.walk(tree):
            if (isinstance(node, ast.Constant)
                    and isinstance(node.value, (int, float))
                    and not isinstance(node.value, bool)
                    and id(node) not in range_args):
                num = str(node.value)
                if num not in _MAGIC_EXCLUDE:
                    smells.append({
                        "type": "magic_number", "name": "", "line": node.lineno,
                        "severity": "low", "file": rel, "detail": f"魔法数字: {num}",
                        "suggestion": "提取为命名常量"})
    return smells


def _heuristic_smells(text: str, path: str, root: Optional[str], ext: str,
                      long_lines: int, max_params: int, max_nesting: int,
                      big_lines: int) -> List[Dict[str, Any]]:
    """JS/Java/Go/Rust 代码异味（轻量启发式，复用定义提取 + 函数体括号配对）。
    嵌套用最大缩进估算（保守阈值），魔法数字/重复条件用共享文本检测。"""
    smells: List[Dict[str, Any]] = []
    if ext in (".js", ".ts"):
        lang, defs_fn, strip_fn = "js", _js_defs, _strip_js_noise
    elif ext == ".java":
        lang, defs_fn, strip_fn = "java", _java_defs, _strip_java_noise
    elif ext == ".go":
        lang, defs_fn, strip_fn = "go", _go_defs, _strip_go_noise
    elif ext == ".rs":
        lang, defs_fn, strip_fn = "rust", _rust_defs, _strip_rust_noise
    else:
        return smells
    clean = strip_fn(text)
    rel = os.path.relpath(path, root) if root else path
    is_test = _is_test_file(path)

    def _params_of(off: int) -> int:
        start = clean.find("(", off)
        if start == -1:
            return 0
        depth = 0
        for i in range(start, min(len(clean), start + 500)):
            c = clean[i]
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    params = clean[start + 1:i]
                    if not params.strip():
                        return 0
                    d2, n = 0, 0
                    for p in params:
                        if p in "([{<":
                            d2 += 1
                        elif p in ")]}>":
                            d2 -= 1
                        elif p == "," and d2 == 0:
                            n += 1
                    return n + 1
        return 0

    for name, off, ln, kind in defs_fn(clean):
        if kind != "func":
            continue
        end = _func_body_end(clean, off)
        if end is not None:
            func_lines = clean[:end].count("\n") - clean[:off].count("\n") + 1
            body = clean[off:end + 1]
        else:
            func_lines = 1
            body = clean[off:off + 200]
        if func_lines > long_lines:
            smells.append({
                "type": "long_function", "name": name, "line": ln,
                "severity": "high" if func_lines > long_lines * 2 else "medium",
                "file": rel, "detail": f"{func_lines} 行（阈值 {long_lines}）",
                "suggestion": "拆分为多个职责单一的小函数"})
        n_params = _params_of(off)
        if n_params > max_params:
            smells.append({
                "type": "long_parameter_list", "name": name, "line": ln,
                "severity": "high" if n_params > max_params * 2 else "medium",
                "file": rel, "detail": f"{n_params} 个参数（阈值 {max_params}）",
                "suggestion": "参数收拢为配置对象 / 数据类"})
        # 嵌套：函数体最大缩进级数（4 空格一级；保守阈值，减少对齐代码误报）
        indent_max = 0
        for line in body.splitlines():
            if line.strip() and not line.strip().startswith(("//", "*")):
                indent = len(line) - len(line.lstrip())
                indent_max = max(indent_max, indent)
        if indent_max // 4 > max_nesting + 2:      # 只报 ≥ 7 级缩进，降低误报
            smells.append({
                "type": "deep_nesting", "name": name, "line": ln,
                "severity": "medium",
                "file": rel, "detail": f"最大缩进 {indent_max} 空格（约 {indent_max // 4} 层）",
                "suggestion": "提前 return 消除分支 / 抽出守卫子句"})

    # 大类（JS/Java/Rust：class/struct/enum/trait 定义块行数或方法数）
    for m in re.finditer(r"\b(?:class|struct|enum|trait)\s+([A-Za-z_]\w*)", clean):
        brace = clean.find("{", m.end())
        if brace == -1:
            continue
        end = _func_body_end(clean, brace - 1)
        if end is None:
            continue
        cls_lines = clean[:end].count("\n") - clean[:m.start()].count("\n") + 1
        n_methods = sum(1 for _n, off2, _l, kind2 in defs_fn(clean)
                        if kind2 == "func" and m.start() <= off2 <= end)
        if cls_lines > big_lines or n_methods > 20:
            smells.append({
                "type": "god_class", "name": m.group(1), "line": _lineno_of(clean, m.start(1)),
                "severity": "high" if cls_lines > big_lines * 2 or n_methods > 30 else "medium",
                "file": rel, "detail": f"{cls_lines} 行 / {n_methods} 个方法（阈值 {big_lines} 行或 20 方法）",
                "suggestion": "按职责拆分为多个类"})

    # 重复条件（共享）
    for cond, off in _dup_conditions(clean, lang):
        smells.append({
            "type": "duplicate_condition", "name": "", "line": _lineno_of(clean, off),
            "severity": "medium", "file": rel, "detail": f"条件重复出现: {cond}",
            "suggestion": "合并分支 / 提取条件为命名变量"})

    # 魔法数字（共享，每文件限 5 个；测试文件跳过）
    if not is_test:
        for num, off in _magic_numbers(clean):
            smells.append({
                "type": "magic_number", "name": "", "line": _lineno_of(clean, off),
                "severity": "low", "file": rel, "detail": f"魔法数字: {num}",
                "suggestion": "提取为命名常量"})
    return smells


def detect_code_smells(tree_files: List[str], root: Optional[str] = None,
                       long_func_lines: int = 50, max_params: int = 5,
                       max_nesting: int = 4, big_class_lines: int = 300) -> Dict[str, Any]:
    """代码异味检测（v3.7 新增，--smells）：
    长函数 / 长参数列表 / 嵌套过深 / 重复条件分支 / 魔法数字 / 大类。
    Python 走 AST 精确分析；JS/TS/Java/Go/Rust 走轻量启发式（可能误报，需人工确认）。
    阈值均可配置（函数参数）。

    返回: {"smells": [{"type","severity","file","line","name","detail","suggestion"}],
           "counts": {类型: 数量}, "total": int}
    smells 按严重程度排序（high > medium > low），同级别按文件路径排序。"""
    severity_rank = {"high": 0, "medium": 1, "low": 2}
    smells: List[Dict[str, Any]] = []
    for f in tree_files:
        ext = os.path.splitext(f)[1].lower()
        text = read_text(f)
        if not text:
            continue
        if ext == ".py":
            smells.extend(_py_smells(text, f, root, long_func_lines, max_params,
                                     max_nesting, big_class_lines))
        elif ext in (".js", ".ts", ".java", ".go", ".rs"):
            smells.extend(_heuristic_smells(text, f, root, ext, long_func_lines,
                                            max_params, max_nesting, big_class_lines))

    smells.sort(key=lambda s: (severity_rank[s["severity"]], s["file"], s["line"]))
    counts: Dict[str, int] = {}
    for s in smells:
        counts[s["type"]] = counts.get(s["type"], 0) + 1
    return {"smells": smells, "counts": counts, "total": len(smells)}


# ========== 调用图 / 重复代码 / 技术债务（v3.7 从 core.py 迁入，core 保留薄包装） ==========
def _mermaid_safe(name: str) -> str:
    """Mermaid 标签转义（引号）"""
    return name.replace("\\", "\\\\").replace('"', '\\"')


def _mermaid_node(disp: str) -> str:
    """Mermaid 节点：id 用路径安全化，标签显示原路径"""
    node_id = re.sub(r"[^A-Za-z0-9_]", "_", disp)
    return f'{node_id}["{_mermaid_safe(disp)}"]'


def generate_call_graph_data(bank: Any, module_map: Dict[str, str], root: str,
                             small_tree: Any = None,
                             target_file: Optional[str] = None) -> Dict[str, Any]:
    """调用图结构化数据（v3.5：供 --json 输出）：
    返回 {"nodes": [{"id","label"}], "edges": [{"from","to","relation","symbol"}]}
    small_tree 仅文件模式需要（v3.7 从 TreeFarm 迁出为独立函数）。"""
    nodes: Dict[str, str] = {}
    edges: List[Dict[str, Any]] = []
    if target_file:
        tf = os.path.abspath(target_file)
        if not os.path.isfile(tf):
            return {"nodes": [], "edges": [], "error": f"文件不存在: {target_file}"}
        if small_tree is None:
            return {"nodes": [], "edges": [], "error": "文件模式需要先建库（plant）"}
        out, inn = small_tree.related(tf)
        rel = os.path.relpath(tf, root)
        nodes[rel] = rel
        for g in out:
            tgt = g["target"]
            tgt_disp = os.path.relpath(tgt, root) if os.path.isfile(tgt) else tgt
            nodes[tgt_disp] = tgt_disp
            edges.append({"from": rel, "to": tgt_disp,
                          "relation": g["relation"], "symbol": g.get("symbol", "")})
        for g in inn:
            src_disp = os.path.relpath(g["source"], root)
            nodes[src_disp] = src_disp
            edges.append({"from": src_disp, "to": rel,
                          "relation": g["relation"], "symbol": g.get("symbol", "")})
    else:
        for g in bank.all_genes():
            if g["relation"] not in ("import", "call", "inherit"):
                continue
            tgt = g["target"]
            if os.path.isabs(tgt) and os.path.isfile(tgt):
                tgt_disp = os.path.relpath(tgt, root)
            elif tgt in module_map:
                tgt_disp = os.path.relpath(module_map[tgt], root)
            else:
                continue
            src_disp = os.path.relpath(g["source"], root)
            nodes[src_disp] = src_disp
            nodes[tgt_disp] = tgt_disp
            edges.append({"from": src_disp, "to": tgt_disp,
                          "relation": g["relation"], "symbol": g.get("symbol", "")})
    seen_edges: Set[Tuple[str, str, str]] = set()
    uniq_edges: List[Dict[str, Any]] = []
    for e in edges:
        key = (e["from"], e["to"], e["relation"])
        if key not in seen_edges:
            seen_edges.add(key)
            uniq_edges.append(e)
    return {"nodes": [{"id": n, "label": l} for n, l in sorted(nodes.items())],
            "edges": uniq_edges}


def generate_call_graph(bank: Any, module_map: Dict[str, str], root: str,
                        small_tree: Any = None,
                        target_file: Optional[str] = None) -> str:
    """调用图（v3.3，Mermaid flowchart 输出）：
    无参数 = 全项目文件级调用图；带文件 = 该文件的符号级调用图。"""
    lines = ["graph LR"]
    if target_file:
        tf = os.path.abspath(target_file)
        if not os.path.isfile(tf):
            return f"✖ 文件不存在: {target_file}"
        if small_tree is None:
            return "✖ 文件模式需要先建库（plant）"
        out, inn = small_tree.related(tf)
        rel = os.path.relpath(tf, root)
        src_node = _mermaid_node(rel)
        if not out and not inn:
            lines.append(f'    {src_node}')
        for g in out:
            tgt = g["target"]
            tgt_disp = os.path.relpath(tgt, root) if os.path.isfile(tgt) else tgt
            label = g.get("symbol") or g["target"]
            lines.append(f'    {src_node} -->|"{_mermaid_safe(str(label))}"| '
                         f'{_mermaid_node(tgt_disp)}')
        for g in inn:
            src_disp = os.path.relpath(g["source"], root)
            label = g.get("symbol") or g["target"]
            lines.append(f'    {_mermaid_node(src_disp)} -->|"{_mermaid_safe(str(label))}"| '
                         f'{src_node}')
    else:
        edges: Set[Tuple[str, str, str]] = set()
        for g in bank.all_genes():
            if g["relation"] not in ("import", "call", "inherit"):
                continue
            tgt = g["target"]
            if os.path.isabs(tgt) and os.path.isfile(tgt):
                tgt_disp = os.path.relpath(tgt, root)
            elif tgt in module_map:
                tgt_disp = os.path.relpath(module_map[tgt], root)
            else:
                continue
            src_disp = os.path.relpath(g["source"], root)
            edges.add((src_disp, tgt_disp, g["relation"]))
        if not edges:
            lines.append('    A["（没有可画的依赖关系）"]')
        for src_disp, tgt_disp, rel in sorted(edges):
            lines.append(f'    {_mermaid_node(src_disp)} -->|"{rel}"| '
                         f'{_mermaid_node(tgt_disp)}')
    header = ["=" * 52,
              "🕸 调用图（Mermaid）—— 复制到 mermaid.live 或支持 Mermaid 的编辑器渲染",
              "=" * 52]
    return "\n".join(header + lines)


def _function_grams_for_file(text: str, ext: str) -> List[Tuple[str, int, Set[str], int]]:
    """提取函数级 n-gram（v3.5 函数级查重用，v3.7 从 TreeFarm 迁出）：
    返回 [(func_name, lineno, gram_set, body_size)]；无函数体的文件返回 []。"""
    if ext == ".py":
        try:
            tree = ast.parse(text)
        except SyntaxError:
            return []
        out: List[Tuple[str, int, Set[str], int]] = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                body = ast.get_source_segment(text, node) or ""
                if len(body) < 40:
                    continue
                out.append((node.name, node.lineno, set(_gram_hashes(body)), len(body)))
        return out
    clean = _strip_js_noise(text) if ext in (".js", ".ts") else (
        _strip_java_noise(text) if ext == ".java" else (
            _strip_go_noise(text) if ext == ".go" else _strip_rust_noise(text)))
    defs_fn = {"js": _js_defs, "java": _java_defs, "go": _go_defs,
               "rust": _rust_defs}.get(
        "js" if ext in (".js", ".ts") else ext, None)
    if not defs_fn:
        return []
    out = []
    for name, off, ln, kind in defs_fn(clean):
        if kind != "func":
            continue
        end = _func_body_end(clean, off)
        body = clean[off:end + 1] if end is not None else clean[off:off + 80]
        if len(body) < 40:
            continue
        out.append((name, ln, set(_gram_hashes(body)), len(body)))
    return out


def collect_function_grams(tree_files: List[str]) -> List[Tuple[str, str, int, Set[str], int]]:
    """全项目函数级 gram 汇总（v3.5：函数级查重 / 技术债务共用）。"""
    funcs: List[Tuple[str, str, int, Set[str], int]] = []
    for f in tree_files:
        ext = os.path.splitext(f)[1].lower()
        funcs.extend((f, name, ln, grams, size)
                     for name, ln, grams, size in _function_grams_for_file(read_text(f), ext))
    return funcs


def _bucket_by_size(funcs: List[Tuple[str, str, int, Set[str], int]], bucket_w: int = 64) \
        -> Dict[int, List[Tuple[str, str, int, Set[str], int]]]:
    """按函数体大小分桶（v3.5：把函数级对比从 O(n²) 降到 ~O(n·k)）。"""
    buckets: Dict[int, List[Tuple[str, str, int, Set[str], int]]] = {}
    for fn in funcs:
        buckets.setdefault(fn[4] // bucket_w, []).append(fn)
    return buckets


def detect_duplicate_func_pairs(tree_files: List[str], threshold: float = 0.6) \
        -> List[Tuple[str, int, str, int, float]]:
    """全项目函数级重复对（分桶优化，v3.5）。返回 [(fa, fa_line, fb, fb_line, score)]。"""
    funcs = collect_function_grams(tree_files)
    buckets = _bucket_by_size(funcs)
    pairs: List[Tuple[str, int, str, int, float]] = []
    for bi, bucket in buckets.items():
        others = list(bucket)
        if bi + 1 in buckets:
            others += buckets[bi + 1]
        for i in range(len(bucket)):
            for j in range(i + 1, len(others)):
                a, b = bucket[i], others[j]
                if a[0] == b[0]:
                    continue
                if min(a[4], b[4]) * 1.3 < max(a[4], b[4]):
                    continue
                inter = len(a[3] & b[3])
                if not inter:
                    continue
                score = inter / len(a[3] | b[3])
                if score >= threshold:
                    pairs.append((a[0], a[1], b[0], b[1], round(score, 2)))
    pairs.sort(key=lambda x: -x[4])
    return pairs


def detect_duplicates(tree_files: List[str], threshold: float = 0.6) \
        -> Tuple[List[Tuple[str, str, float]], int]:
    """重复代码检测（v3.3，内容 n-gram Jaccard 相似度，零依赖）：
    大小差 >30% 的文件直接跳过。返回 ([(fa, fb, score)] 按相似度降序, 参与比较的文件数)。"""
    grams: Dict[str, Set[str]] = {}
    sizes: Dict[str, int] = {}
    for f in tree_files:
        text = read_text(f)
        sizes[f] = len(text)
        g = set(_gram_hashes(text))
        if g:
            grams[f] = g
    files = list(grams.keys())
    pairs: List[Tuple[str, str, float]] = []
    for i in range(len(files)):
        for j in range(i + 1, len(files)):
            a, b = files[i], files[j]
            if min(sizes[a], sizes[b]) * 1.3 < max(sizes[a], sizes[b]):
                continue
            inter = len(grams[a] & grams[b])
            if not inter:
                continue
            score = inter / len(grams[a] | grams[b])
            if score >= threshold:
                pairs.append((a, b, round(score, 2)))
    pairs.sort(key=lambda x: -x[2])
    return pairs, len(files)


def calculate_debt(tree_files: List[str], bank: Any, module_map: Dict[str, str],
                   root: Optional[str] = None) -> Dict[str, Any]:
    """技术债务评估（v3.5 四维打分，v3.7 从 TreeFarm 迁出为独立函数）：
    死代码比例 / 高复杂度函数 / 高度重复函数 / 循环依赖 四维，0~100（越低越好）。

    返回: {"total": int, "grade": str, "emoji": str,
           "dimensions": [{"label": str, "score": int}],
           "suggestions": [str]}"""
    dead = detect_dead_code(tree_files)
    dead_ratio = dead["dead_ratio"]
    d1 = min(25, int(dead_ratio * 100 * 1.25))
    l1 = (f"死代码: {dead_ratio * 100:.1f}%"
          f"（{len(dead['dead_functions'])} 函数 / {len(dead['dead_classes'])} 类）")

    comp_funcs = []
    for f in tree_files:
        r = calculate_complexity_any(f)
        if r:
            comp_funcs.extend((os.path.relpath(r["file"], root) if root else r["file"],
                               name, c) for name, c, ln, _ in r["functions"]
                              if c > 15 and ln > 30)
    high_complex = len(comp_funcs)
    d2 = min(25, high_complex * 3)
    l2 = f"高复杂度函数(>15): {high_complex} 个"

    dup_count = 0
    try:
        dup_count = len(detect_duplicate_func_pairs(tree_files, threshold=0.8))
    except Exception:
        pass
    d3 = min(25, dup_count * 2)
    l3 = f"高度重复函数对(≥80%): {dup_count} 对"

    cyc = detect_circular_dependencies(bank, module_map)
    d4 = min(25, len(cyc["cycles"]) * 8)
    l4 = f"循环依赖: {len(cyc['cycles'])} 个环"

    total = d1 + d2 + d3 + d4
    grade = "A" if total <= 10 else "B" if total <= 25 else "C" if total <= 45 else "D"
    emoji = {"A": "🟢", "B": "🟡", "C": "🟠", "D": "🔴"}[grade]

    suggestions = []
    if dead_ratio > 0.2:
        suggestions.append(f"死代码比例 {dead_ratio * 100:.1f}% 偏高，建议先清死代码"
                           f"（--dead-code 看清单，需人工确认）")
    if high_complex:
        suggestions.append(f"{high_complex} 个函数圈复杂度超标，建议拆函数"
                           f"（--complexity 看排行）")
    if dup_count:
        suggestions.append(f"{dup_count} 对函数高度重复，建议合并"
                           f"（--duplicates-func 看清单）")
    if cyc["has_cycle"]:
        suggestions.append("存在循环依赖，建议先破环（--circular-deps 看路径）")
    if not suggestions:
        suggestions.append("各项指标都很健康，保持现状即可 🎉")

    return {"total": total, "grade": grade, "emoji": emoji,
            "dimensions": [{"label": l1, "score": d1},
                           {"label": l2, "score": d2},
                           {"label": l3, "score": d3},
                           {"label": l4, "score": d4}],
            "suggestions": suggestions}


# ============================================================
# 安全漏洞检测模块 (v4.0 新增)
# ============================================================

def detect_security_issues(tree_files: List[str], root: Optional[str] = None) -> Dict[str, Any]:
    """检测安全漏洞：SQL注入、XSS、命令注入、路径遍历、硬编码密码、不安全反序列化、弱哈希、
    SSRF、JWT、XXE、开放重定向、认证绕过"""
    _SECURITY_EXTS = {".py", ".js", ".jsx", ".ts", ".tsx", ".html", ".htm", ".vue"}

    issues = []
    severity_count = {"critical": 0, "high": 0, "medium": 0, "low": 0}

    for fpath in tree_files:
        if os.path.splitext(fpath)[1].lower() not in _SECURITY_EXTS:
            continue
        try:
            with open(fpath, "r", encoding="utf-8", errors="ignore") as fh:
                lines = fh.readlines()
        except Exception:
            continue

        rel = os.path.relpath(fpath, root) if root else fpath
        full_text = "".join(lines)

        # v4.5 新增：污点变量收集（轻量级跨行数据流跟踪）
        # 识别「用户输入源 → 变量赋值」的传播链，解决单行正则检测不到跨行数据流的问题
        tainted_vars = set()
        # 函数参数视为潜在不可信输入（外部调用者传入的值可能来自用户）
        for ln in lines:
            fm = re.search(r'''^\s*def\s+\w+\s*\(([^)]*)\)''', ln)
            if fm:
                for param in fm.group(1).split(","):
                    param = param.strip().split("=")[0].strip().lstrip("*")
                    if param and param != "self" and param != "cls":
                        tainted_vars.add(param)
        for ln in lines:
            ln_strip = ln.strip()
            if ln_strip.startswith("#"):
                continue
            # 用户输入源直接赋值：x = request.args.get("..") / x = input(..) / x = request.form[".."]
            m = re.search(r'''(\w+)\s*=\s*request\.(?:args|form|values|json|data)\.get\s*\(''', ln)
            if not m:
                m = re.search(r'''(\w+)\s*=\s*request\.(?:args|form|values|json|files)\s*\[['"]''', ln)
            if not m:
                m = re.search(r'''(\w+)\s*=\s*input\s*\(''', ln)
            if not m:
                m = re.search(r'''(\w+)\s*=\s*sys\.argv\[''', ln)
            if not m:
                m = re.search(r'''(\w+)\s*=\s*(?:params|request\.query|self\.request)\s*\.get\s*\(''', ln)
            if m:
                tainted_vars.add(m.group(1))
            # 污点传播：y = x（x 是污点变量）
            m2 = re.search(r'''(\w+)\s*=\s*(\w+)\s*(?:\.strip\(\)|\.lower\(\))?\s*$''', ln)
            if m2 and m2.group(2) in tainted_vars:
                tainted_vars.add(m2.group(1))
            # 污点传播（拼接赋值）：x = "..." + y  或  x = f"...{y}..."（右边表达式含污点变量 y）
            m3 = re.search(r'''(\w+)\s*=\s*(.+)$''', ln)
            if m3:
                lhs, rhs = m3.group(1), m3.group(2)
                if ("+" in rhs or ("{" in rhs and ("f\"" in ln or "f'" in ln))):
                    if any(v in rhs for v in tainted_vars):
                        tainted_vars.add(lhs)

        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            # v4.5 改进：跳过安全规则库定义行（如 pattern_matcher.py 的 "pattern": "os.system($CMD)"，
            # 这类字符串是用于检测别人代码的"漏洞模式"，不是插件自身的漏洞）
            if '"pattern"' in line or "'pattern'" in line:
                if re.search(r'''["']pattern["']\s*:\s*["']''', line):
                    continue

            # 1. SQL注入检测
            sql_patterns = [
                (r'''execute\s*\(\s*["'].*?["']\s*\+''', "SQL注入：字符串拼接查询"),
                (r'''execute\s*\(\s*f["']''', "SQL注入：f-string拼接查询"),
                (r'''execute\s*\(\s*["'].*?%s.*?["']\s*%''', "SQL注入：%格式化拼接查询"),
                (r'''execute\s*\(\s*["'].*?\{.*?\}.*?["']\.format''', "SQL注入：format拼接查询"),
                (r'''cursor\.execute\s*\(\s*["'].*?\+''', "SQL注入：cursor拼接查询"),
            ]
            for pattern, desc in sql_patterns:
                if re.search(pattern, line):
                    issues.append({"file": rel, "line": i, "type": "SQL注入",
                                   "severity": "critical", "desc": desc, "code": stripped[:100]})
                    severity_count["critical"] += 1
                    break

            # 2. 命令注入检测
            cmd_patterns = [
                (r'''os\.system\s*\(\s*[^"']''', "命令注入：os.system接收变量"),
                (r'''os\.popen\s*\(\s*[^"']''', "命令注入：os.popen接收变量"),
                (r'''subprocess\.(call|run|Popen|check_output|check_call)\s*\([^)]*shell\s*=\s*True''',
                 "命令注入：subprocess shell=True"),
                (r'''subprocess\.(call|run|Popen)\s*\(\s*[^"'].*?\+''', "命令注入：subprocess拼接命令"),
            ]
            for pattern, desc in cmd_patterns:
                if re.search(pattern, line):
                    issues.append({"file": rel, "line": i, "type": "命令注入",
                                   "severity": "critical", "desc": desc, "code": stripped[:100]})
                    severity_count["critical"] += 1
                    break

            # 3. 路径遍历检测
            path_patterns = [
                (r'''open\s*\(\s*[^"'].*?\+''', "路径遍历：open接收拼接路径"),
                (r'''open\s*\(\s*request\.''', "路径遍历：open接收用户输入"),
                (r'''os\.path\.join\s*\([^)]*request\.''', "路径遍历：路径拼接用户输入"),
                (r'''send_file\s*\(\s*[^"']''', "路径遍历：send_file接收变量"),
            ]
            for pattern, desc in path_patterns:
                if re.search(pattern, line):
                    issues.append({"file": rel, "line": i, "type": "路径遍历",
                                   "severity": "high", "desc": desc, "code": stripped[:100]})
                    severity_count["high"] += 1
                    break

            # 4. 不安全反序列化
            deser_patterns = [
                (r'''pickle\.loads\s*\(''', "不安全反序列化：pickle.loads"),
                (r'''pickle\.load\s*\(''', "不安全反序列化：pickle.load"),
                (r'''yaml\.load\s*\([^)]*Loader\s*=\s*yaml\.FullLoader''', "不安全反序列化：yaml.load FullLoader"),
                (r'''yaml\.load\s*\([^)]*\)''', "不安全反序列化：yaml.load（默认不安全）"),
                (r'''marshal\.loads?\s*\(''', "不安全反序列化：marshal.loads"),
            ]
            for pattern, desc in deser_patterns:
                if re.search(pattern, line):
                    issues.append({"file": rel, "line": i, "type": "不安全反序列化",
                                   "severity": "critical", "desc": desc, "code": stripped[:100]})
                    severity_count["critical"] += 1
                    break

            # 5. 硬编码密码/密钥检测
            secret_patterns = [
                (r'''(password|passwd|pwd|db_password)\s*=\s*["'][^"']{3,}["']''', "硬编码密码"),
                (r'''(api_key|apikey|api_token|access_key|secret_key|secret_token)\s*=\s*["'][^"']{8,}["']''',
                 "硬编码API密钥"),
                (r'''(jwt_secret|jwt_key|session_secret|cookie_secret)\s*=\s*["'][^"']{8,}["']''',
                 "硬编码会话密钥"),
                (r'''(stripe_secret|payment_key|pay_key|smtp_password|redis_password)\s*=\s*["'][^"']{3,}["']''',
                 "硬编码服务密码"),
                (r'''(private_key|PRIVATE_KEY)\s*=\s*["']-----BEGIN''', "硬编码私钥"),
            ]
            for pattern, desc in secret_patterns:
                if re.search(pattern, line, re.IGNORECASE):
                    issues.append({"file": rel, "line": i, "type": "硬编码凭据",
                                   "severity": "high", "desc": desc, "code": stripped[:100]})
                    severity_count["high"] += 1
                    break

            # 6. 弱哈希检测（v4.5收紧：只在密码/口令/凭据语境下报，指纹/校验和场景不算）
            hash_patterns = [
                (r'''hashlib\.md5\s*\(''', "弱哈希：MD5用于密码"),
                (r'''hashlib\.sha1\s*\(''', "弱哈希：SHA1用于密码"),
                (r'''hashlib\.md5\s*\(.*?\.hexdigest''', "弱哈希：MD5密码存储"),
            ]
            for pattern, desc in hash_patterns:
                if re.search(pattern, line):
                    # 只有该行或相邻行出现密码/凭据语境才报，纯指纹/基因库（如 sha1(raw).hexdigest()[:12]）不算
                    ctx = line + "".join(lines[max(0, i-1):min(len(lines), i+2)])
                    if re.search(r'(password|passwd|pwd|credential|secret|auth|登录|口令|密码)', ctx, re.IGNORECASE):
                        issues.append({"file": rel, "line": i, "type": "弱哈希",
                                       "severity": "medium", "desc": desc, "code": stripped[:100]})
                        severity_count["medium"] += 1
                        break

            # 6.5 时序攻击检测（v4.1新增）
            timing_patterns = [
                (r'''(password|passwd|pwd|secret|token|hash)\s*==\s*''', "时序攻击：用==比较密码/哈希，应使用hmac.compare_digest"),
            ]
            for pattern, desc in timing_patterns:
                if re.search(pattern, line, re.IGNORECASE):
                    issues.append({"file": rel, "line": i, "type": "时序攻击",
                                   "severity": "medium", "desc": desc, "code": stripped[:100]})
                    severity_count["medium"] += 1
                    break

            # 6.6 不安全随机数检测（v4.1新增）
            insecure_random_patterns = [
                (r'''random\.(randint|random|choice|uniform|sample)\s*\(.*?(password|token|secret|code|key|nonce|salt)''', "不安全随机数：random模块用于安全场景，应使用secrets模块"),
            ]
            for pattern, desc in insecure_random_patterns:
                if re.search(pattern, line, re.IGNORECASE):
                    issues.append({"file": rel, "line": i, "type": "不安全随机数",
                                   "severity": "high", "desc": desc, "code": stripped[:100]})
                    severity_count["high"] += 1
                    break

            # 6.7 ReDoS检测（v4.1新增）
            redos_patterns = [
                (r'''re\.(compile|match|search|findall)\s*\([^)]*(\([^)]*[+*]\)[+*])''', "ReDoS：嵌套量词正则表达式可能导致灾难性回溯"),
            ]
            for pattern, desc in redos_patterns:
                if re.search(pattern, line):
                    issues.append({"file": rel, "line": i, "type": "ReDoS",
                                   "severity": "high", "desc": desc, "code": stripped[:100]})
                    severity_count["high"] += 1
                    break

            # 6.8.1 CRLF注入增强检测（v4.3新增）
            if re.search(r'headers\s*\[', line) or "set_cookie" in line or "add_header" in line:
                if re.search(r'(f["\']|%|\.format|\+)', line):
                    if re.search(r'(username|user_id|name|input|request|args|get|data)', line, re.IGNORECASE):
                        issues.append({"file": rel, "line": i, "type": "CRLF注入",
                                       "severity": "medium", "desc": "用户输入拼接到HTTP头，可通过\\r\\n注入任意HTTP头(CRLF Injection)", "code": stripped[:100]})
                        severity_count["medium"] += 1

            # 6.8 CRLF注入检测（v4.1新增）
            crlf_patterns = [
                (r'''f["'].*?(Location|Set-Cookie).*?\{''', "CRLF注入：f-string拼接HTTP头，可能注入\\r\\n"),
            ]
            for pattern, desc in crlf_patterns:
                if re.search(pattern, line, re.IGNORECASE):
                    issues.append({"file": rel, "line": i, "type": "CRLF注入",
                                   "severity": "medium", "desc": desc, "code": stripped[:100]})
                    severity_count["medium"] += 1
                    break

            # 6.9.1 临时文件竞争增强检测（v4.2.1新增）
            # 检测直接open /tmp/路径
            if re.search(r'open\s*\([^)]*["\']/tmp/', line):
                issues.append({"file": rel, "line": i, "type": "临时文件竞争",
                               "severity": "medium", "desc": "使用固定/tmp路径，可能被符号链接攻击，应使用tempfile.mkstemp", "code": stripped[:100]})
                severity_count["medium"] += 1
            # 检测/tmp/路径变量赋值（后续可能被open使用）
            if re.search(r'["\']/tmp/[a-zA-Z_]+', line):
                issues.append({"file": rel, "line": i, "type": "临时文件竞争",
                               "severity": "medium", "desc": "固定临时文件路径，存在竞争条件风险，应使用tempfile.mkstemp", "code": stripped[:100]})
                severity_count["medium"] += 1

            # 6.9 临时文件竞争检测（v4.1新增）
            tempfile_patterns = [
                (r'''open\s*\(\s*["']/tmp/''', "临时文件竞争：使用固定/tmp路径，应使用tempfile.mkstemp"),
            ]
            for pattern, desc in tempfile_patterns:
                if re.search(pattern, line):
                    issues.append({"file": rel, "line": i, "type": "临时文件竞争",
                                   "severity": "medium", "desc": desc, "code": stripped[:100]})
                    severity_count["medium"] += 1
                    break


            # 6.10 路径遍历增强检测（v4.2新增，v4.5收紧：拼接 + 明确用户输入源）
            path_traversal_patterns = [
                (r'''os\.path\.join\s*\([^)]*(request\.(args|form|values|json|get|files)|request\[|params\[|args\[|form\[|input\(|sys\.argv)''',
                 "路径遍历：os.path.join拼接用户输入，绝对路径会忽略前面路径"),
                (r'''open\s*\([^)]*\+[^)]*(request\.|params\[|args\[|form\[|input\()''',
                 "路径遍历：open拼接用户输入路径"),
                (r'''send_file\s*\([^)]*(request\.|params\[|args\[|form\[|input\()''',
                 "路径遍历：send_file接收用户输入路径"),
            ]
            for pattern, desc in path_traversal_patterns:
                if re.search(pattern, line, re.IGNORECASE):
                    issues.append({"file": rel, "line": i, "type": "路径遍历",
                                   "severity": "high", "desc": desc, "code": stripped[:100]})
                    severity_count["high"] += 1
                    break

            # 6.10.2 pathlib路径遍历（v4.3新增，v4.5收紧：明确用户输入源）
            if re.search(r'Path\s*\(', line):
                if re.search(r'(request\.(args|form|values|json|get|files)|request\[|params\[|args\[|form\[|input\(|sys\.argv|query\[|body\[)', line, re.IGNORECASE):
                    issues.append({"file": rel, "line": i, "type": "路径遍历",
                                   "severity": "high", "desc": "pathlib.Path拼接用户输入，若为绝对路径会忽略base目录(Path traversal)", "code": stripped[:100]})
                    severity_count["high"] += 1
            # 6.10.3 Zip Slip漏洞检测（v4.3新增，v4.5收紧：只认真实调用）
            # 真实 Zip Slip：zipfile.ZipFile(...) 或 .extract(...) 代码调用
            # 排除：检测规则自身（含 "in line" 判断特征）、正则定义行（re.search/compile 里写的 \.extract）
            is_rule_definition = ('" in line' in line or "' in line" in line)
            is_regex_line = re.search(r'''re\.(search|match|compile|findall|fullmatch)\s*\(''', line)
            if not is_rule_definition and not is_regex_line:
                if re.search(r'''zipfile\.ZipFile''', line) or re.search(r'''\.extract(?:all)?\s*\([^"'\s]''', line):
                    issues.append({"file": rel, "line": i, "type": "Zip Slip",
                                   "severity": "high", "desc": "zipfile.extract未校验压缩包内文件名，存在Zip Slip路径遍历漏洞", "code": stripped[:100]})
                    severity_count["high"] += 1

            # 6.10.1 路径遍历宽泛检测（v4.2.1新增，v4.5收紧：只认明确用户输入源）
            if "os.path.join" in line:
                # 只有明确的 Web 用户输入源才算路径遍历，变量名叫 path/name 不等于用户输入
                if re.search(r'(request\.(args|form|values|json|get|files)|request\[|params\[|args\[|form\[|input\(|sys\.argv|query\[|body\[|headers\[)', line, re.IGNORECASE):
                    issues.append({"file": rel, "line": i, "type": "路径遍历",
                                   "severity": "high", "desc": "os.path.join拼接用户输入，若为绝对路径会忽略前面路径", "code": stripped[:100]})
                    severity_count["high"] += 1

            # 6.11 不安全随机数增强检测（v4.2新增）
            if re.search(r'''random\.(randint|random|choice|uniform|sample|randrange)''', line):
                # 检查上下文是否用于安全场景
                for j in range(max(0, i-3), min(i+3, len(lines))):
                    if re.search(r'''(password|token|secret|code|key|nonce|salt|otp|captcha|verify)''', lines[j], re.IGNORECASE):
                        issues.append({"file": rel, "line": i+1, "type": "不安全随机数",
                                       "severity": "high", "desc": "random模块用于安全场景（密码/token/验证码），应使用secrets模块", "code": line.strip()[:100]})
                        severity_count["high"] += 1
                        break

            # 6.12 ReDoS增强检测（v4.2新增，v4.5收紧：只认真正的嵌套量词）
            if re.search(r'''re\.(compile|match|search|findall|fullmatch)''', line):
                # 真正的灾难性回溯：捕获组内已含量词，组外再跟量词，如 (a+)+ (a*)* (a+)*
                # 排除非捕获组 (?:...)* —— 内部是固定字符+量词，属有界回溯
                if re.search(r'''\((?![?:?=!<])[^()]*?[+*]\s*\)\s*[+*]''', line):
                    issues.append({"file": rel, "line": i, "type": "ReDoS",
                                   "severity": "high", "desc": "正则表达式包含嵌套量词（如(a+)+），可能导致灾难性回溯", "code": stripped[:100]})
                    severity_count["high"] += 1

            # 6.13 SQL注入增强检测（v4.2新增）
            sql_injection_patterns = [
                (r'''f["'].*?(SELECT|INSERT|UPDATE|DELETE|DROP).*?\{''', "SQL注入：f-string拼接SQL查询"),
                (r'''(execute|query|raw)\s*\([^)]*%\s*\(''', "SQL注入：%格式化拼接SQL"),
                (r'''(execute|query)\s*\([^)]*\+[^)]*\)''', "SQL注入：字符串拼接SQL查询"),
            ]
            for pattern, desc in sql_injection_patterns:
                if re.search(pattern, line, re.IGNORECASE):
                    # v4.5 改进：排除参数化查询——含 ? 占位符的 f-string 中，
                    # {} 只是生成占位符数量（如 {marks} = "?,?,?"），是安全的
                    if pattern.startswith("f[\"']") and "?" in line:
                        continue
                    issues.append({"file": rel, "line": i, "type": "SQL注入",
                                   "severity": "critical", "desc": desc, "code": stripped[:100]})
                    severity_count["critical"] += 1
                    break

            # 7. XSS检测
            xss_patterns = [
                (r'''render_template_string\s*\(''', "XSS：render_template_string模板注入"),
                (r'''Markup\s*\(''', "XSS：Markup标记为安全（可能绕过转义）"),
                (r'''escape\s*=\s*False''', "XSS：关闭转义"),
                (r'''autoescape\s*=\s*False''', "XSS：关闭自动转义"),
                (r'''response\.write\s*\(\s*request\.''', "XSS：直接输出用户输入"),
                # v4.6 补充：前端 DOM 与框架侧 XSS（配合扩展名扩展覆盖 JS/HTML/Vue/React）
                (r'''\.innerHTML\s*=\s*''', "XSS：innerHTML 直接写入（若内容含用户输入则存储型/反射型XSS）"),
                (r'''document\.write\s*\(\s*''', "XSS：document.write 输出 HTML（用户输入需转义）"),
                (r'''\.outerHTML\s*=\s*''', "XSS：outerHTML 直接写入"),
                (r'''insertAdjacentHTML\s*\(\s*''', "XSS：insertAdjacentHTML 插入 HTML"),
                (r'''v-html\s*=\s*["']''', "XSS：Vue v-html 渲染（仅可用于可信内容）"),
                (r'''dangerouslySetInnerHTML\s*=\s*''', "XSS：React dangerouslySetInnerHTML（仅可用于可信内容）"),
                (r'''\|[\s]*safe\b''', "XSS：Jinja2 |safe 过滤器输出未转义内容"),
                (r'''style=["']text/html["'][^>]*srcdoc''', "XSS：iframe srcdoc 注入 HTML"),
            ]
            for pattern, desc in xss_patterns:
                if re.search(pattern, line):
                    issues.append({"file": rel, "line": i, "type": "XSS跨站脚本",
                                   "severity": "high", "desc": desc, "code": stripped[:100]})
                    severity_count["high"] += 1
                    break

            # 8. 调试模式生产环境
            debug_patterns = [
                (r'''DEBUG\s*=\s*True''', "生产环境调试模式开启"),
                (r'''app\.run\s*\([^)]*debug\s*=\s*True''', "Flask调试模式开启"),
            ]
            for pattern, desc in debug_patterns:
                if re.search(pattern, line):
                    issues.append({"file": rel, "line": i, "type": "调试模式",
                                   "severity": "medium", "desc": desc, "code": stripped[:100]})
                    severity_count["medium"] += 1
                    break

            # 9. SSRF检测（v4.5新增：完全缺失项）
            # 核心判定：URL 必须来自「用户输入源」（request/input/args/form/params/data/query 等），
            # 否则如 urlopen(req)（固定 Request 对象）是合法场景，不算 SSRF
            ssrf_patterns = [
                (r'''requests\.(get|post|put|delete|head|patch)\s*\(\s*f["'][^"']*\{[^}]*''',
                 "SSRF：f-string拼接URL请求"),
                (r'''requests\.(get|post|put|delete|head|patch)\s*\([^)]*\+[^)]*(request\.|input\(|params\[|args\[|form\[|data\[)''',
                 "SSRF：字符串拼接用户输入URL"),
                (r'''urllib\.(request\.urlopen|urlopen)\s*\([^)]*(request\.|input\(|params\[|args\[|form\[|data\[)''',
                 "SSRF：urllib请求用户可控URL"),
                (r'''urllib\.request\.urlopen\s*\([^)]*\{[^}]*''',
                 "SSRF：urllib拼接用户输入URL"),
                (r'''httpx\.(get|post|put|delete)\s*\([^)]*(request\.|input\(|params\[|args\[|form\[|data\[)''',
                 "SSRF：httpx请求用户可控URL"),
                (r'''aiohttp\.(ClientSession|request)\s*\([^)]*(request\.|input\(|params\[|args\[|form\[|data\[)''',
                 "SSRF：aiohttp请求用户可控URL"),
                (r'''(requests|urllib|httpx)[^\n]*url\s*=\s*(request|input|args|params|data|form)''',
                 "SSRF：请求URL来自用户输入"),
                (r'''(requests|httpx)\.(get|post|put|delete)\s*\([^)]*(request\.|input\(|params\[|args\[|form\[)''',
                 "SSRF：请求目标来自用户请求参数"),
            ]
            for pattern, desc in ssrf_patterns:
                if re.search(pattern, line, re.IGNORECASE):
                    issues.append({"file": rel, "line": i, "type": "SSRF",
                                   "severity": "high", "desc": desc, "code": stripped[:100]})
                    severity_count["high"] += 1
                    break

            # 9.5 XXE检测（v4.6新增：补足XML外部实体注入盲区）
            xxe_patterns = [
                (r'''(?:etree|ET|ElementTree|xml\.etree\.ElementTree)\.(parse|XML|fromstring)\s*\(''', "XXE：ElementTree/lxml 解析XML未禁用外部实体（entity 注入风险）"),
                (r'''minidom\.parse\s*\(''', "XXE：minidom 解析XML可能允许外部实体注入"),
                (r'''sax\.parse\s*\(''', "XXE：SAX 解析XML可能允许外部实体注入"),
                (r'''expat\.ParserCreate\s*\(''', "XXE：expat 解析XML默认允许实体展开"),
                (r'''XMLParser\s*\(\s*[^)]*resolve_entities\s*=\s*True''', "XXE：XMLParser 显式开启实体解析"),
                (r'''(no_network|load_dtd|resolve_entities)\s*=\s*False''', "XXE：XML 解析显式禁用安全防护（no_network/load_dtd=False）"),
            ]
            for pattern, desc in xxe_patterns:
                if re.search(pattern, line):
                    issues.append({"file": rel, "line": i, "type": "XXE",
                                   "severity": "high", "desc": desc, "code": stripped[:100]})
                    severity_count["high"] += 1
                    break

            # 9.6 开放重定向检测（v4.6新增：跳转目标来自用户输入）
            open_redirect_patterns = [
                (r'''redirect\s*\(\s*(request\.|params\[|args\[|form\[|data\.|next\b|url\b)''',
                 "开放重定向：redirect 目标来自用户可控输入"),
                (r'''redirect\s*\([^)]*(request\.args|request\.values|request\.form|request\.get)''',
                 "开放重定向：redirect 拼接用户提交参数"),
                (r'''(Location|location)\s*=\s*(request\.|params\[|args\[|form\[)''',
                 "开放重定向：响应头 Location 来自用户输入"),
                (r'''headers\s*\[\s*["'](?:Location|location)["']\s*\]\s*=\s*(request\.|params\[|args\[|form\[)''',
                 "开放重定向：响应头 headers['Location'] 来自用户输入"),
                (r'''return\s+(redirect|Response)\s*\(\s*next\b''',
                 "开放重定向：next 参数直接用于跳转（应校验白名单）"),
            ]
            for pattern, desc in open_redirect_patterns:
                if re.search(pattern, line):
                    issues.append({"file": rel, "line": i, "type": "开放重定向",
                                   "severity": "medium", "desc": desc, "code": stripped[:100]})
                    severity_count["medium"] += 1
                    break

            # 10. JWT完整漏洞检测（v4.5新增：原仅有硬编码secret）
            jwt_patterns = [
                (r'''jwt\.decode\s*\([^)]*verify\s*=\s*False''',
                 "JWT：关闭签名验证（verify=False），攻击者可伪造任意token"),
                (r'''jwt\.decode\s*\([^)]*options\s*=\s*\{[^}]*verify_signature[^}]*False''',
                 "JWT：关闭签名验证（verify_signature=False）"),
                (r'''jwt\.decode\s*\([^)]*algorithms\s*=\s*\[?\s*["']?none["']?''',
                 "JWT：允许alg=none算法，可无密钥伪造签名"),
                (r'''jwt\.(encode|decode)\s*\([^)]*algorithm\s*=\s*["']none["']''',
                 "JWT：使用alg=none（不签名），可被伪造"),
                (r'''jwt\.encode\s*\([^)]*algorithm\s*=\s*["']HS256["']''',
                 "JWT：使用HS256弱密钥（若密钥为硬编码/弱密钥则易被暴力破解）"),
                (r'''decode\s*\(\s*jwt\s*[,)]''',
                 "JWT：可疑的JWT解码调用，需确认签名验证是否开启"),
            ]
            for pattern, desc in jwt_patterns:
                if re.search(pattern, line):
                    issues.append({"file": rel, "line": i, "type": "JWT漏洞",
                                   "severity": "high", "desc": desc, "code": stripped[:100]})
                    severity_count["high"] += 1
                    break

        # v4.6 新增：认证绕过检测（文件级扫描，只报一次避免刷屏）
        # 判定：文件含路由装饰器 + 敏感操作/敏感路径（删除/修改/后台/支付等），
        # 但整个文件未出现任何认证装饰器（login_required / auth / jwt_required 等）→ 未授权访问风险
        sensitive_route_line = 0
        auth_decorator_found = False
        for i, line in enumerate(lines, 1):
            strip_l = line.strip()
            if strip_l.startswith("#"):
                continue
            if re.search(r'''@\w*(login_required|auth\w*|permission\w*|jwt_required|token_required|admin_required|require_admin|ensure_admin|is_authenticated|requires_auth)''', line):
                auth_decorator_found = True
            if re.search(r'''@(app\.route|bp\.route|router\.\w+|\.route)\s*\(''', strip_l):
                if re.search(r'''(delete|remove|drop|admin|reset|支付|secret|token|password|密码|权限|grant|upload|export)''', strip_l, re.IGNORECASE):
                    if sensitive_route_line == 0:
                        sensitive_route_line = i
        if sensitive_route_line > 0 and not auth_decorator_found:
            issues.append({"file": rel, "line": sensitive_route_line, "type": "认证绕过",
                           "severity": "high",
                           "desc": "含敏感操作/后台/支付等路径的路由，整个文件未发现认证装饰器（login_required/auth/jwt_required等），存在未授权访问风险",
                           "code": lines[sensitive_route_line - 1].strip()[:100]})
            severity_count["high"] += 1

        # v4.5 新增：跨行污点跟踪检测（污点变量流入危险 sink 才报）
        # 解决「用户输入先赋给变量，变量再拼进危险函数」跨行数据流漏报问题
        if tainted_vars:
            for i, line in enumerate(lines, 1):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                # 危险 sink：SQL、命令执行、SSRF 请求、文件打开
                tainted_used = any(v in line for v in tainted_vars)
                if not tainted_used:
                    continue
                # SQL 注入：execute/query/raw 的参数含污点变量
                # 排除 def execute(...) 方法定义行、self.execute(...) 自定义方法调用，
                # 以及 executor/runner/engine 等「代码执行器/任务执行器」（非 SQL 语义）
                is_def = re.match(r'^\s*(async\s+)?def\s+', line)
                executor_hit = re.search(r'''(?<![\w.])executor|runner|execute_code|run_code''', line)
                if not is_def and not executor_hit and (
                        re.search(r'''(?<!self)\.(execute|query|raw)\s*\(''', line)
                        or re.search(r'''(?<![\w.])execute\s*\(\s*[a-zA-Z_]''', line)):
                    if any(v in line for v in tainted_vars):
                        # 排除纯占位符参数化（参数是 ? 或 (sql, params) 的 params 部分）
                        if "?" not in line.split("(")[-1] or "=" in line:
                            issues.append({"file": rel, "line": i, "type": "SQL注入",
                                           "severity": "critical", "desc": "污点变量流入SQL查询（跨行数据流）", "code": stripped[:100]})
                            severity_count["critical"] += 1
                            continue
                # SSRF：requests/httpx/urllib 请求含污点变量
                if re.search(r'''(requests|httpx)\.(get|post|put|delete|head|patch)\s*\(''', line) or re.search(r'''urllib\.(request\.)?urlopen\s*\(''', line):
                    issues.append({"file": rel, "line": i, "type": "SSRF",
                                   "severity": "high", "desc": "污点变量流入URL请求（跨行数据流）", "code": stripped[:100]})
                    severity_count["high"] += 1
                    continue
                # 路径遍历：open/send_file 含污点变量
                if re.search(r'''(?<!with\s)(?<!as\s)\bopen\s*\(''', line) or re.search(r'''send_file\s*\(''', line):
                    issues.append({"file": rel, "line": i, "type": "路径遍历",
                                   "severity": "high", "desc": "污点变量流入文件路径（跨行数据流）", "code": stripped[:100]})
                    severity_count["high"] += 1
                    continue

    # v4.7 测试目录降级：测试代码里的「问题」多为测试用例本身（故意构造的脏数据/权限用例），
    # 不算核心库风险。标记 scope=test 并计入独立统计，不拉低核心 risk_score。
    # （severity_count 在循环里已按全部 issues 累计；这里从 issues 过滤出核心库部分重新评分）
    core_sev = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    test_sev = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    for iss in issues:
        if _is_test_file(iss["file"]):
            iss["scope"] = "test"
            test_sev[iss["severity"]] += 1
        else:
            core_sev[iss["severity"]] += 1

    total = len(issues)
    risk_score = min(100, core_sev["critical"] * 15 + core_sev["high"] * 8 +
                      core_sev["medium"] * 3 + core_sev["low"] * 1)
    if risk_score >= 70:
        grade, emoji = "F", "🔴"
    elif risk_score >= 50:
        grade, emoji = "D", "🟠"
    elif risk_score >= 30:
        grade, emoji = "C", "🟡"
    elif risk_score >= 10:
        grade, emoji = "B", "🟢"
    else:
        grade, emoji = "A", "✅"

    return {"total": total, "grade": grade, "emoji": emoji, "risk_score": risk_score,
            "severity": core_sev, "issues": issues,
            "test_issues": sum(test_sev.values()),
            "suggestions": _security_suggestions(core_sev, sum(core_sev.values()))}


def _security_suggestions(sev: Dict[str, int], total: int) -> List[str]:
    s = []
    if total == 0:
        s.append("未发现明显安全漏洞，保持安全编码习惯 🎉")
        return s
    if sev["critical"] > 0:
        s.append(f"发现 {sev['critical']} 个严重漏洞（SQL注入/命令注入/不安全反序列化），必须立即修复！")
    if sev["high"] > 0:
        s.append(f"发现 {sev['high']} 个高危漏洞（XSS/路径遍历/硬编码凭据），建议尽快修复")
    if sev["medium"] > 0:
        s.append(f"发现 {sev['medium']} 个中危问题（弱哈希/调试模式），建议优化")
    s.append("使用参数化查询防止SQL注入，使用白名单验证防止路径遍历")
    s.append("敏感凭据应通过环境变量或密钥管理服务注入，不要硬编码")
    return s


# ============================================================
# 性能问题检测模块 (v4.0 新增)
# ============================================================

# ============================================================
# 性能问题检测模块（v4.5 AST 重写：替代 v4.0-4.5 的正则堆砌）
# ============================================================

_LOOP_TYPES = (ast.For, ast.While, ast.AsyncFor)

# v4.7 AST 解析缓存：同一文件多次检测（--all-checks / --grade 等）只 parse 一次。
# 键 = (绝对路径, mtime, size)；文件未变则复用 AST，改动后自动失效。
_AST_CACHE: Dict[Tuple[str, float, int], ast.AST] = {}
_AST_CACHE_MAX = 512


def _cached_ast(fpath: str) -> Optional[ast.AST]:
    """带失效检查的 AST 缓存：读文件、按 (mtime, size) 命中则复用解析结果。"""
    try:
        st = os.stat(fpath)
        key = (os.path.abspath(fpath), st.st_mtime, st.st_size)
    except OSError:
        return None
    hit = _AST_CACHE.get(key)
    if hit is not None:
        return hit
    try:
        with open(fpath, "r", encoding="utf-8", errors="ignore") as fh:
            text = fh.read()
        tree = ast.parse(text)
    except (SyntaxError, OSError, UnicodeDecodeError):
        return None
    if len(_AST_CACHE) >= _AST_CACHE_MAX:
        _AST_CACHE.clear()
    _AST_CACHE[key] = tree
    return tree


def _iter_py_ast(tree_files, root):
    """逐个解析 Python 文件为 AST，产出 (fpath, rel, lines, tree)。
    v4.7 起走 _cached_ast 复用解析结果（性能优化，--all-checks 提速）。"""
    for fpath in tree_files:
        if not fpath.endswith(".py"):
            continue
        tree = _cached_ast(fpath)
        if tree is None:
            continue
        try:
            with open(fpath, "r", encoding="utf-8", errors="ignore") as fh:
                text = fh.read()
        except Exception:
            continue
        rel = os.path.relpath(fpath, root) if root else fpath
        yield fpath, rel, text.splitlines(), tree


class _PerfVisitor(ast.NodeVisitor):
    """性能问题检测器：单遍 AST 遍历，只在“真循环体内”报。

    规则：循环内字符串拼接 / 循环内线性查找(O(n²)) / 循环内 re.compile / N+1 查询 /
    递归无终止条件或缓存。
    """

    _DB_CALLS = {"execute", "query", "filter", "fetchall", "fetchone",
                 "fetchmany", "save", "commit"}

    def __init__(self, lines, rel):
        self.lines = lines
        self.rel = rel
        self.issues = []
        self._anc = []  # 祖先节点栈

    def _src(self, node):
        if not node.lineno:
            return ""
        return self.lines[node.lineno - 1].strip()[:100]

    def _add(self, lineno, itype, sev, desc):
        code = self.lines[lineno - 1].strip()[:100] if 0 < lineno <= len(self.lines) else ""
        self.issues.append({"file": self.rel, "line": lineno, "type": itype,
                            "severity": sev, "desc": desc, "code": code})

    def generic_visit(self, node):
        self._anc.append(node)
        super().generic_visit(node)
        self._anc.pop()

    def _in_loop(self):
        return any(isinstance(p, _LOOP_TYPES) for p in self._anc)

    # ---- 循环内字符串拼接：s += "x" / s = s + "x" / s += str(i) ----
    @staticmethod
    def _is_str_expr(node):
        """判断表达式是否“看起来是字符串”：字面量 / f-string / str() 调用。"""
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return True
        if isinstance(node, ast.JoinedStr):
            return True
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "str"):
            return True
        return False

    def visit_AugAssign(self, node):
        if isinstance(node.op, ast.Add) and self._in_loop() and self._is_str_expr(node.value):
            self._add(node.lineno, "循环内字符串拼接", "medium",
                      "循环内使用 += 拼接字符串，O(n²) 复杂度，建议收集后用 ''.join()")
        self.generic_visit(node)

    def visit_Assign(self, node):
        if (self._in_loop() and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and isinstance(node.value, ast.BinOp)
                and isinstance(node.value.op, ast.Add)
                and isinstance(node.value.left, ast.Name)
                and node.value.left.id == node.targets[0].id
                and self._is_str_expr(node.value.right)):
            self._add(node.lineno, "循环内字符串拼接", "medium",
                      "循环内 s = s + '...' 拼接字符串，O(n²) 复杂度，建议 ''.join()")
        self.generic_visit(node)

    # ---- 循环内线性查找：x in 列表 / .index() / .count() ----
    def visit_Compare(self, node):
        if self._in_loop():
            for op, right in zip(node.ops, node.comparators):
                if isinstance(op, (ast.In, ast.NotIn)) and self._is_linear_container(right):
                    self._add(node.lineno, "循环内线性查找", "medium",
                              "循环内使用 in 在列表上线性查找，O(n²)，建议提前转为 set/dict")
        self.generic_visit(node)

    @staticmethod
    def _is_linear_container(node):
        """右操作数是否为“可能为列表”的线性容器（排除 set/dict/range/字符串/字面量元组）。"""
        if isinstance(node, (ast.List, ast.ListComp, ast.Subscript)):
            return True
        if isinstance(node, (ast.Name, ast.Attribute)):
            return True
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            return node.func.id not in ("set", "frozenset", "dict", "range",
                                        "enumerate", "zip", "tuple", "str", "keys", "values")
        return False

    # ---- 循环内 re.compile / N+1 查询 ----
    def visit_Call(self, node):
        if self._in_loop() and isinstance(node.func, ast.Attribute):
            fname = node.func.attr
            if fname in ("index", "count"):
                self._add(node.lineno, "循环内线性查找", "medium",
                          f"循环内调用 .{fname}() 线性查找，O(n²)，建议转 set/dict")
            elif fname in self._DB_CALLS:
                self._add(node.lineno, "N+1 查询", "high",
                          f"循环内执行数据库查询 .{fname}()，可能导致 N+1 查询，建议改为批量查询")
            elif (fname == "compile" and isinstance(node.func.value, ast.Name)
                  and node.func.value.id == "re"):
                self._add(node.lineno, "循环内正则编译", "medium",
                          "循环内重复调用 re.compile()，正则每次编译，建议提到循环外复用")
        self.generic_visit(node)

    # ---- v4.6：资源泄漏检测（open/socket 未用 with 且函数内无 close()） ----
    def _check_resource_leaks(self, node):
        """函数体内的资源泄漏：open()/socket.socket() 绑定到变量后，
        既不在 with 语句中，函数内也没有该变量的 .close() 调用。
        文件句柄/连接长期不释放，最终导致句柄耗尽（EmittedError: too many open files）。
        """
        try:
            parent: Dict[ast.AST, ast.AST] = {}
            for s in ast.walk(node):
                for child in ast.iter_child_nodes(s):
                    parent[child] = s

            # 1. 收集函数体内所有 X.close(...) 调用涉及的变量名
            closed: Set[str] = set()
            for s in ast.walk(node):
                if (isinstance(s, ast.Call) and isinstance(s.func, ast.Attribute)
                        and s.func.attr == "close"
                        and isinstance(s.func.value, ast.Name)):
                    closed.add(s.func.value.id)

            # 2. 遍历 open(...)/socket.socket(...) 调用，定位绑定变量与上下文
            for s in ast.walk(node):
                if not isinstance(s, ast.Call):
                    continue
                fun = s.func
                is_open = isinstance(fun, ast.Name) and fun.id == "open"
                is_sock = (isinstance(fun, ast.Attribute) and fun.attr == "socket"
                           and isinstance(fun.value, ast.Name) and fun.value.id == "socket")
                if not (is_open or is_sock):
                    continue

                p = parent.get(s)
                # 3. 绑定变量：f = open(...) / f: Type = open(...)
                var = None
                if isinstance(p, ast.Assign) and len(p.targets) == 1 and isinstance(p.targets[0], ast.Name):
                    var = p.targets[0].id
                elif isinstance(p, ast.AnnAssign) and isinstance(p.target, ast.Name):
                    var = p.target.id
                if var is None:
                    continue  # 无绑定（如 open(x).read() 链式写法）不使用句柄，跳过

                # 4. 排除 with 语句中的 open：with open(...) as f:
                in_with_item = False
                q = p
                while q is not None:
                    if isinstance(q, ast.With):
                        for item in q.items:
                            if item.context_expr is s:
                                in_with_item = True
                                break
                        break
                    q = parent.get(q)
                if in_with_item:
                    continue

                # 5. 函数内有 var.close() 调用（含 try/finally 模式）→ 安全
                if var in closed:
                    continue

                self._add(s.lineno, "资源泄漏", "medium",
                          f"资源 {var} 由 {'open()' if is_open else 'socket.socket()'} 创建，未使用 with 且函数内无 {var}.close()，可能泄漏文件句柄/连接，建议改用 with 或补充 close()")
        except Exception:
            pass

    # ---- 递归无终止条件 / 无缓存 ----
    def visit_FunctionDef(self, node):
        self._check_resource_leaks(node)
        name = node.name
        has_self_call = any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                            and n.func.id == name for n in ast.walk(node))

        def _has_base(fn):
            for n in ast.walk(fn):
                if isinstance(n, ast.If):
                    for branch in (n.body, n.orelse):
                        if any(isinstance(s, (ast.Return, ast.Raise)) for s in branch):
                            return True
            return False

        if has_self_call:
            decos = ""
            try:
                decos = " ".join(ast.unparse(d) for d in node.decorator_list)
            except Exception:
                pass
            has_cache = ("lru_cache" in decos or "cache" in decos or "memo" in decos
                         or "@cache" in decos or "memo" in decos)
            # 自调用分支数 >= 2（如 f(n-1)+f(n-2)）＝指数级重复计算，需缓存
            self_branch_count = sum(
                1 for n in ast.walk(node)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                and n.func.id == name)
            if not _has_base(node):
                self._add(node.lineno, "递归无终止", "medium",
                          f"递归函数 {name} 无明显终止条件（无 if/return 基础情形）")
            elif not has_cache and self_branch_count >= 2:
                self._add(node.lineno, "递归无缓存", "medium",
                          f"递归函数 {name} 分支自调用 {self_branch_count} 次且无缓存，可能指数级重复计算，建议 @lru_cache")
        self.generic_visit(node)

    visit_AsyncFunctionDef = visit_FunctionDef


def _perf_suggestions(sev: Dict[str, int], total: int) -> List[str]:
    s = []
    if total == 0:
        s.append("未发现明显性能问题，保持优化习惯 🎉")
        return s
    if sev["high"] > 0:
        s.append(f"发现 {sev['high']} 个高性能问题（N+1 查询），建议优先优化")
    if sev["medium"] > 0:
        s.append(f"发现 {sev['medium']} 个中性能问题（循环内 O(n²) 操作/字符串拼接/资源泄漏），建议排查")
    s.append("循环内 in/index/count 线性查找可先转 set/dict 再判断，循环内 re.compile 提到循环外")
    s.append("循环内字符串拼接改用 ''.join()，数据库查询移出循环改为批量查询")
    s.append("文件/连接资源统一用 with 语句管理，避免句柄泄漏")
    return s


def detect_performance_issues(tree_files: List[str], root: Optional[str] = None) -> Dict[str, Any]:
    """检测性能问题（v4.5 AST 重写，v4.6 加资源泄漏）：循环内字符串拼接 / 循环内线性查找(O(n²)) /
    循环内 re.compile / N+1 查询 / 递归无终止 / open/socket 资源泄漏。

    基于 ast 模块单遍遍历，只在“真循环体内”判定，注释、字符串、方法定义不再误报。
    返回契约不变：{total, grade, emoji, perf_score, severity, issues, suggestions}。
    """
    issues: List[Dict[str, Any]] = []
    for _fpath, rel, lines, tree in _iter_py_ast(tree_files, root):
        vis = _PerfVisitor(lines, rel)
        vis.visit(tree)
        issues.extend(vis.issues)

    severity_count = {"high": 0, "medium": 0, "low": 0}
    for i in issues:
        severity_count[i["severity"]] += 1
    total = len(issues)
    perf_score = min(100, severity_count["high"] * 12 + severity_count["medium"] * 5 + severity_count["low"] * 2)
    if perf_score >= 60:
        grade, emoji = "D", "🔴"
    elif perf_score >= 35:
        grade, emoji = "C", "🟡"
    elif perf_score >= 15:
        grade, emoji = "B", "🟢"
    else:
        grade, emoji = "A", "✅"

    return {"total": total, "grade": grade, "emoji": emoji, "perf_score": perf_score,
            "severity": severity_count, "issues": issues,
            "test_issues": sum(1 for i in issues if _is_test_file(i["file"])),
            "suggestions": _perf_suggestions(severity_count, total)}


# ============================================================
# 逻辑错误检测模块（v4.5 AST 重写：替代 v4.0-4.4 的正则堆砌）
# ============================================================

class _LogicVisitor(ast.NodeVisitor):
    """逻辑错误检测器：单遍 AST 遍历，精确判定。

    规则：可变默认参数 / 除零风险 / 边界条件越界 / 竞态条件（仅真实用线程）/
    TOCTOU / is 与字面量比较 / 字符串大小比较。
    """

    _SAFE_DIVISORS = {"len", "abs", "max", "min", "sum"}
    _LOOP_INDEX_VARS = {"i", "j", "k", "n", "x", "y", "z", "t", "idx"}

    def __init__(self, lines, rel, has_threads):
        self.lines = lines
        self.rel = rel
        self.has_threads = has_threads
        self.issues = []
        self._anc = []

    def _add(self, lineno, itype, sev, desc):
        code = self.lines[lineno - 1].strip()[:100] if 0 < lineno <= len(self.lines) else ""
        self.issues.append({"file": self.rel, "line": lineno, "type": itype,
                            "severity": sev, "desc": desc, "code": code})

    def generic_visit(self, node):
        self._anc.append(node)
        super().generic_visit(node)
        self._anc.pop()

    def _enclosing_func(self, node):
        for p in reversed(self._anc):
            if isinstance(p, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return p
        return None

    # ---- 可变默认参数 ----
    def visit_FunctionDef(self, node):
        for a in node.args.defaults + [d for d in node.args.kw_defaults if d is not None]:
            if isinstance(a, (ast.List, ast.Dict, ast.Set)):
                self._add(node.lineno, "可变默认参数", "medium",
                          "函数默认参数使用可变对象（[]/{}/set()），所有调用共享同一实例，建议改为 None")
        self.generic_visit(node)

    visit_AsyncFunctionDef = visit_FunctionDef

    # ---- 除零风险 ----
    def _has_zero_guard(self, node, var):
        fn = self._enclosing_func(node)
        if fn is None:
            return False
        for n in ast.walk(fn):
            if isinstance(n, (ast.If, ast.While, ast.Assert)) and hasattr(n, "test"):
                t = n.test
                if isinstance(t, ast.Compare):
                    for op, c in zip(t.ops, t.comparators):
                        if (isinstance(c, ast.Constant) and isinstance(c.value, int)
                                and c.value == 0 and isinstance(t.left, ast.Name)
                                and t.left.id == var):
                            return True
                    if (isinstance(t.left, ast.Constant) and isinstance(t.left.value, int)
                            and t.left.value == 0
                            and any(isinstance(c, ast.Name) and c.id == var for c in t.comparators)):
                        return True
        return False

    def visit_BinOp(self, node):
        if (isinstance(node.op, (ast.Div, ast.FloorDiv, ast.Mod))
                and isinstance(node.right, ast.Name)
                and node.right.id not in self._SAFE_DIVISORS
                and node.right.id not in self._LOOP_INDEX_VARS
                and not self._has_zero_guard(node, node.right.id)):
            self._add(node.lineno, "除零风险", "low",
                      f"除以变量 {node.right.id}，未发现前置非零检查，若为 0 将抛 ZeroDivisionError")
        self.generic_visit(node)

    # ---- 边界条件：for i in range(len(x)) 内访问 x[i±1] ----
    def visit_For(self, node):
        tgt = node.target
        it = node.iter
        if (isinstance(tgt, ast.Name) and isinstance(it, ast.Call)
                and isinstance(it.func, ast.Name) and it.func.id == "range"
                and len(it.args) == 1):
            larg = it.args[0]
            if (isinstance(larg, ast.Call) and isinstance(larg.func, ast.Name)
                    and larg.func.id == "len" and len(larg.args) == 1):
                coll = self._as_name(larg.args[0])
                if coll:
                    for n in ast.walk(node):
                        if isinstance(n, ast.Subscript) and self._as_name(n.value) == coll:
                            sl = n.slice
                            if (isinstance(sl, ast.BinOp) and isinstance(sl.left, ast.Name)
                                    and sl.left.id == tgt.id
                                    and isinstance(sl.op, (ast.Add, ast.Sub))
                                    and isinstance(sl.right, ast.Constant)
                                    and sl.right.value == 1):
                                self._add(n.lineno, "边界条件", "medium",
                                          f"for i in range(len({coll})) 内访问 {coll}[i±1]，末位可能越界（IndexError）")
                                break
        self.generic_visit(node)

    @staticmethod
    def _as_name(node):
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            try:
                return ast.unparse(node)
            except Exception:
                return None
        return None

    # ---- 竞态条件：仅当文件真实使用线程 + 共享属性自增无锁 ----
    def _has_lock_in_scope(self, node):
        scope = self._enclosing_func(node)
        if scope is None:
            return False
        for n in ast.walk(scope):
            if isinstance(n, ast.With):
                for item in n.items:
                    try:
                        if "lock" in ast.unparse(item.context_expr).lower() \
                                or (item.optional_vars and "lock" in ast.unparse(item.optional_vars).lower()):
                            return True
                    except Exception:
                        pass
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and n.func.attr == "acquire"):
                return True
        return False

    def _check_race(self, tree):
        if not self.has_threads:
            return
        for n in ast.walk(tree):
            target = None
            if (isinstance(n, ast.AugAssign) and isinstance(n.target, ast.Attribute)
                    and isinstance(n.target.value, ast.Name) and n.target.value.id == "self"):
                target, op = n.target, n.op
            elif (isinstance(n, ast.AugAssign) and isinstance(n.target, ast.Subscript)
                    and isinstance(n.target.value, ast.Name)):
                target, op = n.target, n.op
            elif (isinstance(n, ast.Assign) and isinstance(n.value, ast.BinOp)
                    and isinstance(n.targets[0], ast.Attribute)
                    and isinstance(n.targets[0].value, ast.Name)
                    and n.targets[0].value.id == "self"
                    and isinstance(n.value.left, ast.Attribute)
                    and ast.unparse(n.value.left) == ast.unparse(n.targets[0])):
                target, op = n.targets[0], n.value.op
            elif (isinstance(n, ast.Assign) and isinstance(n.value, ast.BinOp)
                    and isinstance(n.targets[0], ast.Subscript)
                    and isinstance(n.targets[0].value, ast.Name)
                    and isinstance(n.value.left, ast.Subscript)
                    and ast.unparse(n.value.left) == ast.unparse(n.targets[0])):
                target, op = n.targets[0], n.value.op
            if target is not None and isinstance(op, (ast.Add, ast.Sub)):
                if isinstance(target, ast.Attribute):
                    what = f"self.{target.attr}"
                    if target.attr in ("lock", "mutex"):
                        continue
                else:
                    what = f"{ast.unparse(target.value)}[{ast.unparse(target.slice)}]"
                    if "lock" in what.lower() or "mutex" in what.lower():
                        continue
                if not self._has_lock_in_scope(target):
                    self._add(target.lineno, "竞态条件", "high",
                              f"多线程环境下共享变量 {what} 自增/自减无锁，可能竞态导致数据丢失")

    # ---- TOCTOU：os.path.exists 后 open/remove/rename ----
    def _is_exists_check(self, call):
        return (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                and isinstance(call.func.value, ast.Attribute)
                and isinstance(call.func.value.value, ast.Name)
                and call.func.value.value.id == "os" and call.func.value.attr == "path"
                and call.func.attr in ("exists", "isfile", "isdir", "islink"))

    def _toctou_after(self, node, body):
        for stmt in body:
            if self._is_unsafe_after_exists(stmt):
                self._add(node.lineno, "TOCTOU 竞争", "medium",
                          "先检查文件存在再操作，存在时间窗口竞争（TOCTOU），建议直接操作并捕获异常")
                return True
        return False

    def visit_Expr(self, node):
        v = node.value
        if self._is_exists_check(v):
            self._toctou_after(node, self._body_after(node))
        self.generic_visit(node)

    def visit_If(self, node):
        # 常见模式：if os.path.exists(x): 直接 open/remove —— TOCTOU
        if self._is_exists_check(node.test) and not self._toctou_after(node, node.body):
            # 若 if 体只是 return True，检查 else/后续也会用到（保持简单，仅报一次）
            pass
        self.generic_visit(node)

    def _body_after(self, node):
        for p in reversed(self._anc):
            for attr in ("body", "orelse", "finalbody"):
                lst = getattr(p, attr, None)
                if isinstance(lst, list) and node in lst:
                    idx = lst.index(node)
                    return lst[idx + 1: idx + 11]
        return []

    @staticmethod
    def _is_unsafe_after_exists(stmt):
        for n in ast.walk(stmt):
            if isinstance(n, ast.Call):
                f = n.func
                if isinstance(f, ast.Name) and f.id == "open":
                    return True
                if (isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name)
                        and f.value.id in ("os", "shutil")
                        and f.attr in ("remove", "rename", "rmtree", "unlink", "replace")):
                    return True
        return False

    # ---- is 与字面量比较 / 字符串大小比较 ----
    @staticmethod
    def _is_literal(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, str, float)):
            return True
        if isinstance(node, (ast.List, ast.Dict, ast.Set, ast.Tuple)):
            return True
        return False

    @staticmethod
    def _is_str_expr(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return True
        if isinstance(node, ast.JoinedStr):
            return True
        return False

    def visit_Compare(self, node):
        for op, right in zip(node.ops, node.comparators):
            if isinstance(op, (ast.Is, ast.IsNot)) and self._is_literal(right):
                self._add(node.lineno, "比较运算符错误", "medium",
                          "使用 is/is not 比较字面量，应使用 ==/!=（is 比较对象身份而非值）")
            elif isinstance(op, (ast.GtE, ast.LtE, ast.Gt, ast.Lt)) and \
                    (self._is_str_expr(node.left) or self._is_str_expr(right)):
                self._add(node.lineno, "比较运算符错误", "low",
                          "字符串使用大小比较（>/</>=/<=），字符串比较无大小含义，应使用 ==/!=")
        self.generic_visit(node)


def _logic_suggestions(sev: Dict[str, int], total: int) -> List[str]:
    s = []
    if total == 0:
        s.append("未发现明显逻辑错误，代码逻辑清晰 🎉")
        return s
    if sev["high"] > 0:
        s.append(f"发现 {sev['high']} 个高风险逻辑错误（竞态条件），必须修复")
    if sev["medium"] > 0:
        s.append(f"发现 {sev['medium']} 个中风险问题（可变默认参数/边界条件），建议修复")
    s.append("可变默认参数改用 None 作为默认值，函数内部初始化")
    s.append("多线程共享变量使用 threading.Lock 保护，除法操作前检查除数非零")
    return s


# ============================================================
# API 契约检测（v4.7 新增，--logic 能力增强）
# ============================================================

class _ContractVisitor(ast.NodeVisitor):
    """API 契约一致性检测（v4.7 新增）。

    目标：抓 tornado #1 那类「同一家族公开方法委托对象不一致」的 API 契约 bug。
    规则 1 —— 委托对象一致性：一个类里若 ≥3 个公开方法都调用 `self.<A>.<meth>()`
    （A 不是 stream），却恰好有 1 个公开方法调用 `self.stream.<meth>()`，
    且 stream 在 __init__ 里被初始化为 None → 疑似错误委托。
    规则 2 —— 抽象方法完整性：抽象类（基类含 abc.ABC 或带模块级 ABC）声明了
    @abstractmethod；继承它的子类若缺失任一抽象方法且未再标 abstract → 报。

    返回契约：{"issues": [...]}，单文件级调用。
    """

    def __init__(self, lines, rel):
        self.lines = lines
        self.rel = rel
        self.issues = []

    def visit_ClassDef(self, node):
        self._check_delegate_consistency(node)
        self._check_abstract_completeness(node)

    def _add(self, lineno, itype, sev, desc, fix=""):
        code = self.lines[lineno - 1].strip()[:100] if 0 < lineno <= len(self.lines) else ""
        issue = {"file": self.rel, "line": lineno, "type": itype,
                 "severity": sev, "desc": desc, "code": code}
        # 增强：修 bug 时附带建议（v4.7 grader 决策支撑）
        issue["fix"] = fix
        self.issues.append(issue)

    # ---- 规则 1：委托对象一致性 ----
    def _check_delegate_consistency(self, cls):
        # 收集本类所有方法(self, ...)体内用到的 self.<attr>.<meth>() 委托
        # 以及 __init__ 里 self.stream = None 之类
        method_delegates = {}    # attr -> [ (method_name, lineno) ]
        stream_none = False
        for stmt in cls.body:
            if not isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            mname = stmt.name
            collected = _collect_self_attr_calls(stmt)
            for attr in collected:
                method_delegates.setdefault(attr, []).append((mname, stmt.lineno))
            if mname == "__init__":
                for substmt in ast.walk(stmt):
                    if (isinstance(substmt, ast.Assign)
                            and isinstance(substmt.targets[0], ast.Attribute)
                            and isinstance(substmt.targets[0].value, ast.Name)
                            and substmt.targets[0].value.id == "self"
                            and substmt.targets[0].attr == "stream"
                            and isinstance(substmt.value, ast.Constant)
                            and substmt.value.value is None):
                        stream_none = True

        if not stream_none:
            return
        # 找主流委托对象（被 ≥3 个不同方法使用、最常见的 attr，排除 stream/self 本身）
        best_attr, best_count = None, 0
        for attr, uses in method_delegates.items():
            if attr == "stream":
                continue
            if len(uses) >= 3 and len(uses) > best_count:
                best_attr, best_count = attr, len(uses)
        if best_attr is None:
            return
        # 若 stream 也被某方法用（且不是主流），报
        stream_uses = method_delegates.get("stream", [])
        if stream_uses and len(stream_uses) < best_count:
            for mname, lineno in stream_uses:
                self._add(
                    lineno, "API契约",
                    "high",
                    (f"方法 {mname}() 委托给 self.stream，但同类{best_count}个方法都委托 "
                     f"self.{best_attr}，疑似委托对象不一致（stream 在 __init__ 初值为 None，"
                     f"调用会 AttributeError）。参考: write_message/close 等"
                     f"用 self.{best_attr}，此方法应改用 self.{best_attr}"),
                    fix=f"把 self.stream 改为 self.{best_attr}"
                )

    # ---- 规则 2：抽象方法完整性 ----
    def _check_abstract_completeness(self, cls):
        # 判定是否为抽象类：继承 abc.ABC / abc 的子类
        abstract_bases = set()
        for base in cls.bases:
            bname = ""
            if isinstance(base, ast.Name):
                bname = base.id
            elif isinstance(base, ast.Attribute):
                try:
                    bname = ast.unparse(base)
                except Exception:
                    continue
            if bname.endswith("ABC") or bname == "ABC":
                abstract_bases.add(bname)
        if not abstract_bases:
            return
        # 收集本类 @abstractmethod 方法名
        abstract_methods = set()
        all_methods = set()
        for stmt in cls.body:
            if not isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            all_methods.add(stmt.name)
            for dec in stmt.decorator_list:
                dname = ""
                if isinstance(dec, ast.Name):
                    dname = dec.id
                elif isinstance(dec, ast.Attribute):
                    dname = dec.attr
                if dname in ("abstractmethod", "abstractproperty", "abcmethod"):
                    abstract_methods.add(stmt.name)
        if not abstract_methods:
            return
        # 子类缺失检测需要跨类，这里做单文件的：找继承本类的子类并核对
        tree = ast.parse("\n".join(self.lines))
        for subclass in ast.walk(tree):
            if not isinstance(subclass, ast.ClassDef):
                continue
            if subclass is cls:
                continue
            subclass_of_cls = False
            for base in subclass.bases:
                if isinstance(base, ast.Name) and base.id == cls.name:
                    subclass_of_cls = True
                elif isinstance(base, ast.Attribute) and ast.unparse(base) == cls.name:
                    subclass_of_cls = True
            if not subclass_of_cls:
                continue
            impl = {s.name for s in subclass.body
                    if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef))}
            missing = abstract_methods - impl
            if missing and _is_concrete_subclass(subclass, cls.name, tree):
                for m in sorted(missing):
                    self._add(
                        subclass.lineno, "抽象方法未实现", "medium",
                        f"子类 {subclass.name} 未实现抽象类 {cls.name} 的抽象方法 {m}()，"
                        f"实例化会报 TypeError（abstract method）",
                        fix=f"在 {subclass.name} 中实现 {m}()，或把 {subclass.name} 也标为抽象类"
                    )


def _collect_self_attr_calls(func):
    """收集函数体内所有 self.<attr>.<method>() 调用形式里的 attr 集合。"""
    attrs = set()
    for n in ast.walk(func):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and isinstance(n.func.value, ast.Attribute)
                and isinstance(n.func.value.value, ast.Name)
                and n.func.value.value.id == "self"):
            attrs.add(n.func.value.attr)
    return attrs


def _is_concrete_subclass(subclass, base_name, tree):
    """判断子类是否仍抽象（自己还带 abstractmethod 的方法 → 仍是抽象类）。"""
    for stmt in subclass.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in stmt.decorator_list:
                dn = dec.id if isinstance(dec, ast.Name) else (dec.attr if isinstance(dec, ast.Attribute) else "")
                if dn in ("abstractmethod", "abstractproperty"):
                    return False
    return True


def _tree_starts_threads(tree) -> bool:
    """AST 判定：是否真实「创建」了线程（threading.Thread(...)/ThreadPoolExecutor/裸 Thread(...)）。
    只认 Call 节点，字符串/规则库字样不算。"""
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call):
            continue
        f = n.func
        name = ""
        if isinstance(f, ast.Name):
            name = f.id
        elif isinstance(f, ast.Attribute):
            try:
                name = ast.unparse(f)
            except Exception:
                continue
        if name in ("Thread", "threading.Thread", "concurrent.futures.ThreadPoolExecutor",
                    "ThreadPoolExecutor", "multiprocessing.pool.ThreadPool", "threading.ThreadPool"):
            return True
    return False


def _tree_uses_threading(tree) -> bool:
    """AST 判定：代码里出现 threading. 属性访问（锁/本地/事件等线程原语）但未必起线程。
    仍排除字符串字面量（Attribute 节点才算）。"""
    for n in ast.walk(tree):
        if (isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
                and n.value.id in ("threading", "_thread")):
            return True
        if (isinstance(n, ast.Name) and n.id == "Thread"):
            return True
    return False


def detect_logic_issues(tree_files: List[str], root: Optional[str] = None) -> Dict[str, Any]:
    """检测逻辑错误（v4.5 AST 重写；v4.7 新增契约检测 + 异步降误报）：
    可变默认参数 / 除零风险 / 边界条件越界 / 竞态条件（仅真实使用线程，异步框架降误报）/
    TOCTOU / is 与字面量比较 / 字符串大小比较 / API契约一致性 / 抽象方法完整性。

    基于 ast 精确判定，消除正则时代的误报。返回契约不变：
    {total, grade, emoji, logic_score, severity, issues, suggestions}。
    """
    issues: List[Dict[str, Any]] = []
    for _fpath, rel, lines, tree in _iter_py_ast(tree_files, root):
        text = "\n".join(lines)
        # v4.7 异步降误报 + AST 线程判定：文件以 asyncio/协程为主且无真线程 → 竞态不算 high。
        # 用 AST 找「真实线程创建调用」（threading.Thread/ThreadPoolExecutor/Thread(），
        # 避免把字符串/规则库里的 "threading.Thread" 字样误当线程（自检误报根源）。
        is_async_file = bool(re.search(r"\basync\s+def\b|\bawait\b", text))
        has_real_threads = _tree_starts_threads(tree)
        has_threads = has_real_threads or (
            not is_async_file and _tree_uses_threading(tree))
        vis = _LogicVisitor(lines, rel, has_threads)
        vis.visit(tree)
        vis._check_race(tree)
        issues.extend(vis.issues)
        # v4.7 新增：API 契约一致性 + 抽象方法完整性
        cv = _ContractVisitor(lines, rel)
        cv.visit(tree)
        issues.extend(cv.issues)

    severity_count = {"high": 0, "medium": 0, "low": 0}
    for i in issues:
        severity_count[i["severity"]] += 1
    total = len(issues)
    logic_score = min(100, severity_count["high"] * 12 + severity_count["medium"] * 5 + severity_count["low"] * 2)
    if logic_score >= 50:
        grade, emoji = "D", "🔴"
    elif logic_score >= 30:
        grade, emoji = "C", "🟡"
    elif logic_score >= 10:
        grade, emoji = "B", "🟢"
    else:
        grade, emoji = "A", "✅"

    return {"total": total, "grade": grade, "emoji": emoji, "logic_score": logic_score,
            "severity": severity_count, "issues": issues,
            "test_issues": sum(1 for i in issues if _is_test_file(i["file"])),
            "suggestions": _logic_suggestions(severity_count, total)}
