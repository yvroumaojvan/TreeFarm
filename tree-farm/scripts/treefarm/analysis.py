# -*- coding: utf-8 -*-
"""树场机制 —— 代码分析层（v3.6 拆分自单文件 tree_farm.py）。

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
    """检测安全漏洞：SQL注入、XSS、命令注入、路径遍历、硬编码密码、不安全反序列化、弱哈希"""
    issues = []
    severity_count = {"critical": 0, "high": 0, "medium": 0, "low": 0}

    for fpath in tree_files:
        if not fpath.endswith(".py"):
            continue
        try:
            with open(fpath, "r", encoding="utf-8", errors="ignore") as fh:
                lines = fh.readlines()
        except Exception:
            continue

        rel = os.path.relpath(fpath, root) if root else fpath
        full_text = "".join(lines)

        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith("#"):
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

            # 6. 弱哈希检测
            hash_patterns = [
                (r'''hashlib\.md5\s*\(''', "弱哈希：MD5用于密码"),
                (r'''hashlib\.sha1\s*\(''', "弱哈希：SHA1用于密码"),
                (r'''hashlib\.md5\s*\(.*?\.hexdigest''', "弱哈希：MD5密码存储"),
            ]
            for pattern, desc in hash_patterns:
                if re.search(pattern, line):
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


            # 6.10 路径遍历增强检测（v4.2新增）
            path_traversal_patterns = [
                (r'''os\.path\.join\s*\([^)]*\{[^}]+\}''', "路径遍历：os.path.join拼接用户输入，绝对路径会忽略前面路径"),
                (r'''open\s*\([^)]*\+[^)]*\)''', "路径遍历：open拼接用户输入路径"),
                (r'''send_file\s*\([^)]*\+''', "路径遍历：send_file拼接用户输入路径"),
            ]
            for pattern, desc in path_traversal_patterns:
                if re.search(pattern, line):
                    issues.append({"file": rel, "line": i, "type": "路径遍历",
                                   "severity": "high", "desc": desc, "code": stripped[:100]})
                    severity_count["high"] += 1
                    break

            # 6.10.2 pathlib路径遍历（v4.3新增，YesWeHack研究案例）
            if re.search(r'Path\s*\(', line) and re.search(r'\s*/\s*', line):
                if re.search(r'(username|user_id|filename|file_path|path|name|input|request|args|get)', line, re.IGNORECASE):
                    issues.append({"file": rel, "line": i, "type": "路径遍历",
                                   "severity": "high", "desc": "pathlib.Path拼接用户输入，若为绝对路径会忽略base目录(Path traversal)", "code": stripped[:100]})
                    severity_count["high"] += 1
            # 6.10.3 Zip Slip漏洞检测（v4.3新增）
            if "zipfile" in line or ".extract(" in line or ".extractall(" in line:
                if re.search(r'\.extract(?:all)?\s*\(', line):
                    issues.append({"file": rel, "line": i, "type": "Zip Slip",
                                   "severity": "high", "desc": "zipfile.extract未校验压缩包内文件名，存在Zip Slip路径遍历漏洞", "code": stripped[:100]})
                    severity_count["high"] += 1

            # 6.10.1 路径遍历宽泛检测（v4.2.1新增）
            if "os.path.join" in line:
                if re.search(r'(username|user_id|filename|file_path|path|name|input|request|args)', line, re.IGNORECASE):
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

            # 6.12 ReDoS增强检测（v4.2新增）
            if re.search(r'''re\.(compile|match|search|findall|fullmatch)''', line):
                # 检查是否有嵌套量词或重叠模式
                if re.search(r'''(\([^)]*[+*]\)[+*]|\.[+*].*?\.[+*]|\w\s*\[\s*[+*]\s*\].*?[+*])''', line):
                    issues.append({"file": rel, "line": i, "type": "ReDoS",
                                   "severity": "high", "desc": "正则表达式包含嵌套量词/重叠量词，可能导致灾难性回溯", "code": stripped[:100]})
                    severity_count["high"] += 1

            # 6.12.1 ReDoS宽泛检测（v4.2.1新增）
            if re.search(r're\.(compile|match|search|findall)\s*\(', line):
                if re.search(r'[*+].*?[*+]', line):
                    issues.append({"file": rel, "line": i, "type": "ReDoS",
                                   "severity": "high", "desc": "正则表达式包含重复量词，可能导致灾难性回溯(ReDoS)", "code": stripped[:100]})
                    severity_count["high"] += 1

            # 6.13 SQL注入增强检测（v4.2新增）
            sql_injection_patterns = [
                (r'''f["'].*?(SELECT|INSERT|UPDATE|DELETE|DROP).*?\{''', "SQL注入：f-string拼接SQL查询"),
                (r'''(execute|query|raw)\s*\([^)]*%\s*\(''', "SQL注入：%格式化拼接SQL"),
                (r'''(execute|query)\s*\([^)]*\+[^)]*\)''', "SQL注入：字符串拼接SQL查询"),
            ]
            for pattern, desc in sql_injection_patterns:
                if re.search(pattern, line, re.IGNORECASE):
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

    total = len(issues)
    risk_score = min(100, severity_count["critical"] * 15 + severity_count["high"] * 8 +
                      severity_count["medium"] * 3 + severity_count["low"] * 1)
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
            "severity": severity_count, "issues": issues,
            "suggestions": _security_suggestions(severity_count, total)}


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

def detect_performance_issues(tree_files: List[str], root: Optional[str] = None) -> Dict[str, Any]:
    """检测性能问题：O(n²)算法、内存泄漏、递归爆炸、资源泄漏、字符串拼接循环、N+1查询"""
    issues = []
    severity_count = {"high": 0, "medium": 0, "low": 0}

    for fpath in tree_files:
        if not fpath.endswith(".py"):
            continue
        try:
            with open(fpath, "r", encoding="utf-8", errors="ignore") as fh:
                lines = fh.readlines()
        except Exception:
            continue

        rel = os.path.relpath(fpath, root) if root else fpath
        full_text = "".join(lines)

        # 1. O(n²)嵌套循环检测
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            # 检测for嵌套for（同一缩进层级内的嵌套）
            if re.match(r'^\s*for\s+', line):
                # 检查后续10行内是否有另一个for
                indent = len(line) - len(line.lstrip())
                for j in range(i, min(i + 15, len(lines))):
                    next_indent = len(lines[j]) - len(lines[j].lstrip())
                    if next_indent > indent and re.match(r'^\s*for\s+', lines[j]):
                        # 检查内层循环是否有列表操作（append/in/索引）
                        inner_block = "".join(lines[j:min(j+5, len(lines))])
                        if re.search(r'\.append\(|\.extend\(|in\s+\w+|\[\w+\]', inner_block):
                            issues.append({"file": rel, "line": i, "type": "O(n²)算法",
                                           "severity": "high",
                                           "desc": "嵌套循环中操作列表，可能为O(n²)复杂度",
                                           "code": stripped[:100]})
                            severity_count["high"] += 1
                        break

        # 2. 内存泄漏检测：全局列表/字典无限追加
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            # 检测self.xxx.append / self.xxx[key] = 在没有清理机制的类中
            if re.search(r'self\.\w+\.append\(', line) or re.search(r'self\.\w+\[.*\]\s*=', line):
                # 检查类中是否有清理方法
                class_start = -1
                for j in range(i, 0, -1):
                    if re.match(r'^\s*class\s+', lines[j-1]):
                        class_start = j - 1
                        break
                if class_start >= 0:
                    # 找类结束位置
                    class_end = len(lines)
                    for j in range(class_start + 1, len(lines)):
                        if re.match(r'^\S', lines[j]) and not lines[j].strip().startswith("#"):
                            class_end = j
                            break
                    class_text = "".join(lines[class_start:class_end])
                    # 检查是否有清理机制（clear/pop/del/定期清理）
                    has_cleanup = bool(re.search(r'\.clear\(|\.pop\(|del\s+self\.|def\s+clean', class_text))
                    if not has_cleanup:
                        issues.append({"file": rel, "line": i, "type": "内存泄漏",
                                       "severity": "medium",
                                       "desc": "类成员集合只增不减，无清理机制，可能内存泄漏",
                                       "code": stripped[:100]})
                        severity_count["medium"] += 1

        # 3. 递归爆炸检测
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if re.match(r'^\s*def\s+(\w+)\s*\(', line):
                func_name = re.match(r'^\s*def\s+(\w+)\s*\(', line).group(1)
                # 找函数结束位置
                func_indent = len(line) - len(line.lstrip())
                func_end = len(lines)
                for j in range(i, len(lines)):
                    if j > i and lines[j].strip() and (len(lines[j]) - len(lines[j].lstrip()) <= func_indent):
                        func_end = j
                        break
                func_text = "".join(lines[i:func_end])
                # 检测递归调用
                if re.search(rf'\b{func_name}\s*\(', func_text):
                    # 检查是否有终止条件
                    has_base = bool(re.search(r'if\s+.*?:\s*return|if\s+.*?<=|if\s+.*?>=|if\s+.*?==', func_text))
                    has_cache = bool(re.search(r'@lru_cache|@cache|memo', func_text))
                    if not has_base and not has_cache:
                        issues.append({"file": rel, "line": i, "type": "递归爆炸",
                                       "severity": "high",
                                       "desc": f"递归函数{func_name}无明显终止条件或缓存",
                                       "code": stripped[:100]})
                        severity_count["high"] += 1
                    elif not has_cache and func_name in ("fibonacci", "fib", "factorial"):
                        issues.append({"file": rel, "line": i, "type": "递归性能",
                                       "severity": "medium",
                                       "desc": f"递归函数{func_name}无缓存，指数级复杂度",
                                       "code": stripped[:100]})
                        severity_count["medium"] += 1

        # 4. 资源泄漏检测：open没有with
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            # 检测 open( 不在 with 语句中
            if re.search(r'(?<!with\s)(?<!as\s)\bopen\s*\(', line) and 'with' not in line:
                # 检查后续是否有close
                has_close = False
                for j in range(i, min(i + 20, len(lines))):
                    if re.search(r'\.close\(\)', lines[j]):
                        has_close = True
                        break
                    if lines[j].strip() and (len(lines[j]) - len(lines[j].lstrip()) <= len(line) - len(line.lstrip())) and j > i:
                        break
                if not has_close:
                    issues.append({"file": rel, "line": i, "type": "资源泄漏",
                                   "severity": "medium",
                                   "desc": "open()未使用with语句且未找到close()，文件句柄泄漏",
                                   "code": stripped[:100]})
                    severity_count["medium"] += 1

        # 5. 字符串拼接循环检测
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if re.search(r'\w+\s*\+=\s*["\']', line) or re.search(r'\w+\s*=\s*\w+\s*\+\s*["\']', line):
                # 检查是否在循环中
                in_loop = False
                for j in range(i, 0, -1):
                    if re.match(r'^\s*(for|while)\s+', lines[j-1]):
                        in_loop = True
                        break
                    if lines[j-1].strip() and (len(lines[j-1]) - len(lines[j-1].lstrip()) < len(line) - len(line.lstrip())):
                        break
                if in_loop:
                    issues.append({"file": rel, "line": i, "type": "字符串拼接",
                                   "severity": "low",
                                   "desc": "循环中使用+=拼接字符串，O(n²)性能，建议用join()",
                                   "code": stripped[:100]})
                    severity_count["low"] += 1


        # 6.5 内存泄漏增强检测（v4.2新增）
        # 检查全局/类变量只增不减
        if re.search(r'''(self\.\w+|\b[a-z_]+)\s*(append|extend|add|update|\[\w+\]\s*=)''', line):
            var_name = re.search(r'''(self\.\w+|\b[a-z_]+)\s*(append|extend|add)''', line)
            if var_name:
                vname = var_name.group(1)
                # 检查是否有清理机制
                has_cleanup = False
                for j in range(max(0, i-20), min(i+20, len(lines))):
                    if vname in lines[j] and re.search(r'''(clear|pop|del\s|remove|=\s*\[\s*\]|=\s*\{\s*\})''', lines[j]):
                        has_cleanup = True
                        break
                if not has_cleanup and i > 5:
                    issues.append({"file": rel, "line": i, "type": "内存泄漏",
                                       "severity": "medium", "desc": f"变量{vname}只增不减，无清理机制，可能内存泄漏", "code": stripped[:100]})
                    severity_count["medium"] += 1

        # 6.6 字符串拼接循环增强检测（v4.2新增）
        if re.search(r'''\+\s*=\s*.*?(str|\w+\s*\+)''', line) or re.search(r'''result\s*\+=\s*''', line):
            # 检查是否在循环中
            for j in range(max(0, i-10), i):
                if re.search(r'''\b(for|while)\b''', lines[j]):
                    issues.append({"file": rel, "line": i, "type": "字符串拼接",
                                       "severity": "medium", "desc": "循环中使用+=拼接字符串，O(n²)复杂度，应用join()", "code": stripped[:100]})
                    severity_count["medium"] += 1
                    break

        # 6.7 全局变量内存泄漏 + 字符串拼接循环检测（v4.2.1修复版）
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            # 全局变量内存泄漏检测
            if re.search(r'^[a-z_]+\s*=\s*\[\s*\]', line) or re.search(r'^[a-z_]+\s*=\s*\{\s*\}', line):
                var_name = re.search(r'^([a-z_]+)\s*=', line)
                if var_name:
                    vname = var_name.group(1)
                    has_growth = False
                    has_cleanup = False
                    for j in range(i, min(i+50, len(lines))):
                        if vname in lines[j]:
                            if re.search(r'(append|extend|add|update)', lines[j]):
                                has_growth = True
                            if re.search(r'(clear|pop|del\s+' + vname + '|remove)', lines[j]):
                                has_cleanup = True
                    if has_growth and not has_cleanup:
                        issues.append({"file": rel, "line": i, "type": "内存泄漏",
                                           "severity": "medium", "desc": f"全局变量{vname}只增不减，无清理机制，可能内存泄漏", "code": stripped[:100]})
                        severity_count["medium"] += 1
            # 字符串拼接循环检测
            if '+=' in line and ('str(' in line or '+' in line):
                for j in range(max(0, i-15), i):
                    if re.match(r'^\s*(for|while)\b', lines[j]):
                        issues.append({"file": rel, "line": i, "type": "字符串拼接",
                                           "severity": "medium", "desc": "循环中使用+=拼接字符串，O(n²)复杂度，应用join()", "code": stripped[:100]})
                        severity_count["medium"] += 1
                        break

        # 6. N+1查询检测
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if re.search(r'\.execute\(|\.query\(|\.filter\(|\.get\(', line):
                in_loop = False
                for j in range(i, 0, -1):
                    if re.match(r'^\s*(for|while)\s+', lines[j-1]):
                        in_loop = True
                        break
                    if lines[j-1].strip() and (len(lines[j-1]) - len(lines[j-1].lstrip()) < len(line) - len(line.lstrip())):
                        break
                if in_loop:
                    issues.append({"file": rel, "line": i, "type": "N+1查询",
                                   "severity": "high",
                                   "desc": "循环中执行数据库查询，可能导致N+1查询问题",
                                   "code": stripped[:100]})
                    severity_count["high"] += 1

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
            "suggestions": _perf_suggestions(severity_count, total)}


def _perf_suggestions(sev: Dict[str, int], total: int) -> List[str]:
    s = []
    if total == 0:
        s.append("未发现明显性能问题，保持优化习惯 🎉")
        return s
    if sev["high"] > 0:
        s.append(f"发现 {sev['high']} 个高性能问题（O(n²)/N+1查询/递归爆炸），建议优先优化")
    if sev["medium"] > 0:
        s.append(f"发现 {sev['medium']} 个中性能问题（内存泄漏/资源泄漏），建议排查")
    s.append("O(n²)嵌套循环可考虑用字典/集合优化查找，N+1查询改用批量查询")
    s.append("文件操作使用with语句自动释放资源，递归函数添加@lru_cache缓存")
    return s


# ============================================================
# 逻辑错误检测模块 (v4.0 新增)
# ============================================================

def detect_logic_issues(tree_files: List[str], root: Optional[str] = None) -> Dict[str, Any]:
    """检测逻辑错误：比较运算符错误、边界条件、可变默认参数、除零风险、未初始化变量、竞态条件"""
    issues = []
    severity_count = {"high": 0, "medium": 0, "low": 0}

    for fpath in tree_files:
        if not fpath.endswith(".py"):
            continue
        try:
            with open(fpath, "r", encoding="utf-8", errors="ignore") as fh:
                lines = fh.readlines()
        except Exception:
            continue

        rel = os.path.relpath(fpath, root) if root else fpath

        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue

            # 1. 可变默认参数检测
            if re.search(r'def\s+\w+\s*\([^)]*=\s*(\[\]|\{\}|\(\))', line):
                issues.append({"file": rel, "line": i, "type": "可变默认参数",
                               "severity": "medium",
                               "desc": "函数默认参数使用可变对象（[]/{}/()），所有调用共享同一对象",
                               "code": stripped[:100]})
                severity_count["medium"] += 1

            # 2. 比较运算符错误：if x = y（赋值而非比较）
            if re.search(r'if\s+\w+\s*=\s*[^=]', line) and not re.search(r'==', line):
                # 排除 := 海象运算符
                if ':=' not in line:
                    issues.append({"file": rel, "line": i, "type": "比较运算符错误",
                                   "severity": "high",
                                   "desc": "if语句中使用=而非==，可能是赋值而非比较",
                                   "code": stripped[:100]})
                    severity_count["high"] += 1

            # 3. 除零风险检测（排除字符串中的/，如路径/api）
            # 先移除字符串内容，避免误报路径中的/
            clean_line = re.sub(r'["\'][^"\']*["\']', '""', line)
            if re.search(r'/\s*(\w+)', clean_line) and not re.search(r'#.*除零|#.*zero', line, re.IGNORECASE):
                divisor = re.search(r'/\s*(\w+)', clean_line)
                if divisor:
                    var = divisor.group(1)
                    # 检查前面是否有除零检查
                    has_check = False
                    for j in range(max(0, i-10), i):
                        if re.search(rf'if\s+{var}\s*[!=]=\s*0|if\s+{var}\s*[<>]', lines[j]):
                            has_check = True
                            break
                    if not has_check and var not in ('len', '2', '100', '1024', '60', '24', '365',
                                                       'tmp', 'var', 'api', 'dev', 'usr', 'home', 'etc',
                                                       'opt', 'var', 'log', 'uploads', 'static'):
                        # 简单变量除法且无检查
                        if re.match(r'^[a-z_][a-z0-9_]*$', var) and len(var) > 1:
                            issues.append({"file": rel, "line": i, "type": "除零风险",
                                           "severity": "low",
                                           "desc": f"除以变量{var}，未检查是否为0",
                                           "code": stripped[:100]})
                            severity_count["low"] += 1

            # 4. 边界条件错误：range(len(x)) off-by-one
            if re.search(r'range\s*\(\s*len\s*\(', line):
                # 检查是否有 -1 或 +1 错误
                if re.search(r'range\s*\(\s*len\s*\([^)]*\)\s*-\s*1\s*\)', line):
                    issues.append({"file": rel, "line": i, "type": "边界条件",
                                   "severity": "medium",
                                   "desc": "range(len(x)-1)可能导致off-by-one，漏掉最后一个元素",
                                   "code": stripped[:100]})
                    severity_count["medium"] += 1

            # 5. 比较运算符混淆：>= 用于应该用 == 的场景
            if re.search(r'is_admin\s*>=\s*["\']admin["\']', line) or re.search(r'role\s*>=\s*["\']', line):
                issues.append({"file": rel, "line": i, "type": "比较运算符错误",
                               "severity": "high",
                               "desc": "字符串比较使用>=，应使用==，字符串比较无大小意义",
                               "code": stripped[:100]})
                severity_count["high"] += 1

            # 6. 逻辑运算符错误：or 用于应该用 and 的密码验证
            if re.search(r'if\s+.*password.*or.*', line, re.IGNORECASE) and 'and' not in line:
                if re.search(r'not\s+\w+\s+or\s+not', line):
                    issues.append({"file": rel, "line": i, "type": "逻辑运算符错误",
                                   "severity": "high",
                                   "desc": "密码验证使用or而非and，任一条件满足即通过",
                                   "code": stripped[:100]})
                    severity_count["high"] += 1


            # 6.5 TOCTOU竞争条件检测（v4.2新增）
            if re.search(r'''os\.path\.(exists|isfile|isdir|islink)''', line):
                for j in range(i, min(i+20, len(lines))):
                    if re.search(r'''(open|os\.remove|os\.rename|shutil\.)''', lines[j]):
                        issues.append({"file": rel, "line": i+1, "type": "TOCTOU竞争",
                                       "severity": "medium", "desc": "检查文件存在后再操作，存在时间窗口竞争(TOCTOU)", "code": line.strip()[:100]})
                        severity_count["medium"] += 1
                        break

            # 6.6 类型混淆检测（v4.2新增）
            if re.search(r'''json\.loads''', line):
                for j in range(i+1, min(i+10, len(lines))):
                    if re.search(r'''(parsed|result|data)\.(get|\[)''', lines[j]) and "json.loads" not in lines[j]:
                        issues.append({"file": rel, "line": j+1, "type": "类型混淆",
                                       "severity": "medium", "desc": "json.loads返回值可能不是dict，直接调用.get()可能抛AttributeError", "code": lines[j].strip()[:100]})
                        severity_count["medium"] += 1
                        break

            # 6.7 竞态条件增强检测（v4.4修复：仅在多线程环境下检测）
            # 先判断当前文件是否包含多线程/多进程代码
            has_multithreading = any(re.search(r'(threading|Thread|multiprocessing|concurrent\.futures|asyncio)', l) for l in lines)
            if has_multithreading and re.search(r'(Thread|threading)\.', line):
                # 检查函数内是否有共享变量修改无锁
                for j in range(max(0, i-15), min(i+15, len(lines))):
                    if re.search(r'(failed_attempts|counter|count|total|cache|requests|sessions)\s*[+*]?=', lines[j]):
                        has_lock = False
                        for k in range(max(0, j-5), min(j+5, len(lines))):
                            if re.search(r'(lock|Lock|mutex|acquire|release|with.*lock)', lines[k], re.IGNORECASE):
                                has_lock = True
                                break
                        if not has_lock:
                            issues.append({"file": rel, "line": j+1, "type": "竞态条件",
                                           "severity": "high", "desc": "多线程环境下共享变量修改无锁，可能导致竞态条件/数据丢失", "code": lines[j].strip()[:100]})
                            severity_count["high"] += 1
                            break

            # 7. 竞态条件检测：多线程共享变量无锁
            if re.search(r'threading\.Thread|Thread\s*\(', line):
                # 检查后续代码中是否有共享变量操作无锁
                pass  # 简化检测，在类级别检测

        # 8. 类级别竞态条件检测
        full_text = "".join(lines)
        if 'threading' in full_text or 'Thread' in full_text:
            for i, line in enumerate(lines, 1):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if re.search(r'self\.\w+\s*=\s*self\.\w+\s*[+\-*/]\s*', line):
                    # 检查是否有锁
                    has_lock = False
                    for j in range(max(0, i-5), i):
                        if re.search(r'with\s+.*lock|acquire\(\)|Lock\(\)', lines[j]):
                            has_lock = True
                            break
                    if not has_lock and 'threading' in full_text:
                        issues.append({"file": rel, "line": i, "type": "竞态条件",
                                       "severity": "high",
                                       "desc": "多线程环境下共享变量自增操作无锁保护，可能竞态条件",
                                       "code": stripped[:100]})
                        severity_count["high"] += 1

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
            "suggestions": _logic_suggestions(severity_count, total)}


def _logic_suggestions(sev: Dict[str, int], total: int) -> List[str]:
    s = []
    if total == 0:
        s.append("未发现明显逻辑错误，代码逻辑清晰 🎉")
        return s
    if sev["high"] > 0:
        s.append(f"发现 {sev['high']} 个高风险逻辑错误（比较运算符/竞态条件），必须修复")
    if sev["medium"] > 0:
        s.append(f"发现 {sev['medium']} 个中风险问题（可变默认参数/边界条件），建议修复")
    s.append("可变默认参数改用None作为默认值，函数内部初始化")
    s.append("多线程共享变量使用threading.Lock保护，除法操作前检查除数非零")
    return s
