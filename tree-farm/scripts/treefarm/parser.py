# -*- coding: utf-8 -*-
"""树场机制 —— 多语言基因/符号提取层（v3.6 拆分自单文件 tree_farm.py）。

包含：import 级基因提取（Python AST / 其余语言正则）、函数级调用分析
（Python AST 精确；JS/TS、Java、Go 零依赖轻量解析器）、多语言定义提取
（死代码/复杂度共用）、小虫子候选验证、符号提取。
"""

import ast
import os
import re
from typing import List, Optional, Set, Tuple

from .common import (GO_KEYWORDS, JAVA_KEYWORDS, JS_KEYWORDS, RUST_KEYWORDS,
                     _JAVA_DEF_PREFIX, _cache, read_text)


# ========== 基因提取（多语言） ==========
IMPORT_PATTERNS = {
    ".py": None,  # 走 AST
    ".js": [r"import\s+[^'\"\n]*?from\s*['\"]([^'\"]+)['\"]",
            r"require\s*\(\s*['\"]([^'\"]+)['\"]\s*\)",
            r"import\(\s*['\"]([^'\"]+)['\"]\s*\)"],  # 动态 import()（v3 新增）
    ".ts": [r"import\s+[^'\"\n]*?from\s*['\"]([^'\"]+)['\"]",
            r"import\(\s*['\"]([^'\"]+)['\"]\s*\)"],
    ".java": [r"import\s+(?:static\s+)?([\w.]+)"],
    ".c": [r"#\s*include\s*[<\"]([^>\"]+)[>\"]"],
    ".h": [r"#\s*include\s*[<\"]([^>\"]+)[>\"]"],
    ".cpp": [r"#\s*include\s*[<\"]([^>\"]+)[>\"]"],
    ".go": [r"import\s*\(([^)]*)\)", r"import\s+\"([^\"]+)\""],
    ".rs": [r"^\s*use\s+([^;]+);", r"^\s*use\s+([^;]+)::"],  # use 语句（模块/路径导入）
}


def extract_genes(py_file: str) -> List[Tuple[str, str]]:
    """提取强耦合基因（多语言 import 级）。返回 [(target, relation)]"""
    ext = os.path.splitext(py_file)[1].lower()
    found: List[Tuple[str, str]] = []

    if ext == ".py":
        tree = _cache.get_ast(py_file)
        if tree is None:
            return found
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    found.append((alias.name, "import"))
            elif isinstance(node, ast.ImportFrom):
                # 相对导入修复：from . import x / from ..pkg import y
                if node.level:
                    prefix = "." * node.level + (node.module or "")
                    found.append((prefix, "import"))
                elif node.module:
                    for alias in node.names:
                        found.append((node.module, "import"))
        return found

    text = read_text(py_file)
    if not text:
        return []
    patterns = IMPORT_PATTERNS.get(ext, [])
    for pat in patterns:
        for m in re.finditer(pat, text):
            raw = m.group(1).strip()
            if not raw:
                continue
            if ext == ".go":
                for line in raw.splitlines():
                    line = line.strip().strip('"')
                    if line and not line.startswith("//"):
                        found.append((line.split("/")[0], "import"))
            elif ext == ".rs":
                # use a::{b, c} → 取路径前缀 a；use a as b; → 取 a
                raw = re.split(r"\bas\b", raw)[0].strip().rstrip("::")
                raw = raw.split("{")[0].strip().rstrip("::")
                if raw and not raw.startswith("crate") and not raw.startswith("self"):
                    found.append((raw, "import"))
            else:
                found.append((raw, "import"))
    seen: Set[str] = set()
    result = []
    for t, r in found:
        if t not in seen:
            seen.add(t)
            result.append((t, r))
    return result


def extract_call_graph(py_file: str) -> Tuple[List[str], List[str]]:
    """Python AST：函数级调用分析（v3 新增）。
    返回 (calls, inherits)：
      calls    = 文件里出现过的调用全名列表（'fmt' / 'utils.helpers.fmt' / 'obj.method'）
      inherits = 类继承的基类名列表（'Base' / 'pkg.Base'）
    """
    tree = _cache.get_ast(py_file)
    if tree is None:
        return [], []

    def dotted(node: ast.AST) -> Optional[str]:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            base = dotted(node.value)
            if base:
                return base + "." + node.attr
        return None

    calls: List[str] = []
    inherits: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = dotted(node.func)
            if name:
                calls.append(name)
        elif isinstance(node, ast.ClassDef):
            for base in node.bases:
                name = dotted(base)
                if name and name != "object":
                    inherits.append(name)
    return calls, inherits


# ========== JS/TS 函数级分析（v3.3 新增，零依赖轻量解析器） ==========
def _strip_js_noise(text: str) -> str:
    """去掉 JS 字符串字面量与注释（替换为空格），避免正则误匹配（v3.3）。"""
    text = re.sub(r"//[^\n]*", " ", text)
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    text = re.sub(r"`(?:[^`\\]|\\.)*`", " ", text)          # 模板字符串
    text = re.sub(r"'(?:[^'\\]|\\.)*'", " ", text)
    text = re.sub(r'"(?:[^"\\]|\\.)*"', " ", text)
    return text


def extract_js_call_graph(js_file: str) -> Tuple[List[str], List[str]]:
    """轻量 JS/TS 函数级分析（v3.3 新增，零依赖启发式，非完整 AST）：
    返回 (defs, calls)：
      defs  = 文件里定义的函数 / 类 / 箭头函数 / 对象方法名
      calls = 文件里出现过的被调用名或属性链（'fmt' / 'obj.method'）
    覆盖 function / class / 箭头函数 / 对象方法定义与调用，已剔除字符串/注释噪音。
    精确度靠 _build_genes 的跨文件符号验证兜底，不追求 100% AST 级。"""
    text = _strip_js_noise(read_text(js_file))
    if not text:
        return [], []

    defs: List[str] = []
    def_offsets: Set[int] = set()        # 对象/方法定义位置（字符偏移，调用检测排除用）

    defs += re.findall(r"\bfunction\s+([A-Za-z_$][\w$]*)", text)
    defs += re.findall(r"\bclass\s+([A-Za-z_$][\w$]*)", text)
    # 箭头函数：const/let/var name = (...) => 或 name = x =>（v3.4 支持单参数无括号）
    # （name 后是 '='，天然不会被当成调用）
    defs += re.findall(r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?"
                       r"(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>", text)
    # 对象/类方法：行首缩进 NAME(...) { 或 字面量内 { NAME(...) {（单行 class 也能识别）
    # v3.4：支持 static/async/get/set 修饰符（static make() { 等）
    for m in re.finditer(r"(?m)^\s*(?:(?:static|async|get|set)\s+)?([A-Za-z_$][\w$]*)\s*\([^)]*\)\s*\{",
                         text):
        name = m.group(1)
        if name in JS_KEYWORDS:
            continue
        defs.append(name)
        def_offsets.add(m.start(1))          # 捕获组起点 = 方法名位置
    for m in re.finditer(r"(?:[{,;]\s*)(?:(?:static|async|get|set)\s+)?([A-Za-z_$][\w$]*)\s*\([^)]*\)\s*\{",
                         text):
        name = m.group(1)
        if name in JS_KEYWORDS:
            continue
        defs.append(name)
        def_offsets.add(m.start(1))          # 捕获组起点 = 方法名位置

    seen = set(defs)
    calls: List[str] = []
    for m in re.finditer(r"\b([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)\s*\(", text):
        name = m.group(1)
        root = name.split(".")[0]
        if root in JS_KEYWORDS:
            continue
        pre = text[max(0, m.start() - 40):m.start()]
        if re.search(r"\bfunction\s*$", pre):
            continue                     # function NAME( 定义，非调用
        if name in seen and m.start() in def_offsets:
            continue                     # 对象/方法定义行，非调用
        calls.append(name)
    # 去重保序
    seen_calls: Set[str] = set()
    uniq_calls = [c for c in calls if not (c in seen_calls or seen_calls.add(c))]
    return sorted(set(defs)), uniq_calls


# ========== Java 函数级分析（v3.4 新增，零依赖轻量解析器） ==========
def _strip_java_noise(text: str) -> str:
    """去掉 Java 注释与字符串字面量（含文本块），替换为空格（v3.4）。"""
    text = re.sub(r"//[^\n]*", " ", text)
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    text = re.sub(r'"""(?:[^"\\]|\\.)*?"""', " ", text, flags=re.S)   # 文本块
    text = re.sub(r"'(?:[^'\\]|\\.)*'", " ", text)                     # char 字面量
    text = re.sub(r'"(?:[^"\\]|\\.)*"', " ", text)
    return text


def _java_def_context(pre: str) -> bool:
    """判断 '(' 前文本是否为方法定义上下文（修饰符/返回类型 + 名字），
    而非调用/控制流（v3.4）。启发式：'(' 前【同一行】内紧邻的最后一个 token：
    是修饰符/返回类型 → 定义；是调用上下文符号（. @ = ( ) , ; { }）→ 调用；
    是控制流关键字 → 调用。泛型返回（Map<String> get）保留为定义，
    lambda（x -> foo(）排除。
    v3.5：只看 '(' 前同一行（跨行语句不干扰——'private int x;' 后接构造器 Color(）；
    行首无前置 token = 构造器定义（Java 语句级裸调用不合法，行首 '(' 必是定义）。"""
    # 取 '(' 前同一行：跨行内容（上一行的 ; } 等）不影响本行判定
    line_pre = pre.split("\n")[-1]
    toks = re.findall(r"[A-Za-z_$][\w$]*|[^\s]", line_pre)
    if not toks:
        return True                      # 行首 = 构造器定义（Color(int x)）
    last = toks[-1]
    if last == ">" and len(toks) >= 2 and toks[-2] == "-":
        return False                     # x -> foo( lambda 调用
    if last in ".@=(,;{})":
        return False                     # obj. / @Ann( / foo; / { x( / (T) x(
    if last in _JAVA_DEF_PREFIX:
        return True                      # public/static/String/void/MyType get( → 定义
    if last in JAVA_KEYWORDS:
        return False                     # if( / return( / new( 等控制流
    return True                          # 自定义类型返回（MyType get(）→ 定义


def extract_java_call_graph(java_file: str) -> Tuple[List[str], List[str]]:
    """轻量 Java 函数级分析（v3.4 新增，零依赖启发式，非完整 AST）：
    返回 (calls, inherits)：
      calls    = 文件里出现过的被调用名或属性链（'helper.run' / 'Arrays.asList'）
      inherits = 类继承/实现的基类名（extends / implements，已剥泛型）
    已剔除注释、字符串、文本块噪音；方法定义行、注解、new 实例化不视为调用。
    精确度靠 _build_genes 的跨文件符号验证兜底，不追求 100% AST 级。"""
    text = _strip_java_noise(read_text(java_file))
    if not text:
        return [], []

    inherits: List[str] = []
    # 基类/接口名模式：包名点链 + 可选泛型参数（Map<String> / Base<T>）
    base_pat = r"[A-Za-z_$][\w$.]*(?:\s*<[^>]*>)?(?:\s*,\s*[A-Za-z_$][\w$.]*(?:\s*<[^>]*>)?)*"
    # 类名后可带泛型或 record 参数列表：class A<T> / record Point(int x)
    head_pat = r"[A-Za-z_$][\w$]*(?:\s*<[^>]*>|\s*\([^)]*\))?"
    # 单继承：class/interface/enum/record X [<T>|(...)] extends A[, B...]
    for m in re.finditer(r"\b(?:class|interface|enum|record)\s+" + head_pat + r"\s+extends\s+("
                         + base_pat + r")", text):
        for base in re.sub(r"<.*>", "", m.group(1)).split(","):
            base = base.strip()
            if base and base != "Object":
                inherits.append(base)
    # 实现：class/enum X [<T>] [extends A] implements B[, C...]
    for m in re.finditer(r"\b(?:class|enum|record)\s+" + head_pat + r"\s+"
                         r"(?:extends\s+" + base_pat + r"\s+)?implements\s+("
                         + base_pat + r")", text):
        for base in re.sub(r"<.*>", "", m.group(1)).split(","):
            base = base.strip()
            if base:
                inherits.append(base)

    def_offsets: Set[int] = set()        # 方法/构造器定义位置（调用检测排除用）
    for m in re.finditer(r"\b([A-Za-z_$][\w$]*)\s*\([^)]*\)\s*(?:\s+implements\b[^{;]*)?"
                         r"(?:throws\s+[\w.,\s]+)?[;{]", text):
        name = m.group(1)
        if name in JAVA_KEYWORDS:
            continue
        pre = text[max(0, m.start(1) - 100):m.start(1)]
        if not _java_def_context(pre):
            continue
        # 行首裸调用排除：helper(); 是调用不是定义；构造器必有函数体（后跟 {）
        if not re.search(r"[A-Za-z_$]", pre.split("\n")[-1]) \
                and not m.group(0).rstrip().endswith("{"):
            continue
        def_offsets.add(m.start(1))      # 捕获组起点 = 方法名位置

    calls: List[str] = []
    for m in re.finditer(r"\b([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)\s*\(", text):
        name = m.group(1)
        root = name.split(".")[0]
        if root in JAVA_KEYWORDS:
            continue
        pre = text[max(0, m.start() - 60):m.start()]
        if re.search(r"@\s*$", pre):
            continue                     # 注解 @Override(...) / @SuppressWarnings(...)
        if re.search(r"\bnew\s*$", pre):
            continue                     # new Foo(...) 实例化，非方法调用
        if m.start() in def_offsets:
            continue                     # 方法定义行，非调用
        calls.append(name)
    # 去重保序
    seen_calls: Set[str] = set()
    uniq_calls = [c for c in calls if not (c in seen_calls or seen_calls.add(c))]
    return uniq_calls, sorted(set(inherits))


# ========== Go 函数级分析（v3.5 新增，零依赖轻量解析器） ==========
def _strip_go_noise(text: str) -> str:
    """去掉 Go 注释与字符串字面量（双引号 / 反引号 raw string / 单引号 rune），
    替换为空格，避免正则误匹配（v3.5）。"""
    text = re.sub(r"//[^\n]*", " ", text)
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    text = re.sub(r"`(?:[^`\\]|\\.)*`", " ", text)       # raw string
    text = re.sub(r"'(?:[^'\\]|\\.)*'", " ", text)       # rune
    text = re.sub(r'"(?:[^"\\]|\\.)*"', " ", text)
    return text


def extract_go_call_graph(go_file: str) -> Tuple[List[str], List[str]]:
    """轻量 Go 函数级分析（v3.5 新增，零依赖启发式，非完整 AST）：
    返回 (calls, inherits)：
      calls    = 文件里出现过的被调用名或属性链（'fmt.Println' / 'obj.Method'）
      inherits = struct 嵌入（embedding）的基类型名（类似继承：type A struct { Base }）
    已剔除注释、字符串噪音；方法定义行、接口方法声明不视为调用。
    Go 无显式继承，struct 嵌入（匿名嵌入）是最接近继承的形态，故提取为 inherit 基因。
    精确度靠 _build_genes 的跨文件符号验证兜底，不追求 100% AST 级。"""
    text = _strip_go_noise(read_text(go_file))
    if not text:
        return [], []

    def_offsets: Set[int] = set()        # 函数/方法定义位置（调用检测排除用）

    # 函数定义：func Name( 或泛型 func Name[T any](
    for m in re.finditer(r"\bfunc\s+([A-Za-z_]\w*)\s*(?:\[[^\]]*\])?\s*\(", text):
        if m.group(1) not in GO_KEYWORDS:
            def_offsets.add(m.start(1))
    # 方法定义：func (r *T) Name( / func (r T) Name[T any]( —— receiver 里可能带指针/泛型
    for m in re.finditer(r"\bfunc\s*\([^)]*\)\s+([A-Za-z_]\w*)\s*(?:\[[^\]]*\])?\s*\(", text):
        if m.group(1) not in GO_KEYWORDS:
            def_offsets.add(m.start(1))
    # 接口方法声明：type I interface { M(x int) error } —— M( 是声明不是调用
    for m in re.finditer(r"\binterface\s*\{([^}]*)\}", text, flags=re.S):
        block = m.group(1)
        base = m.start(1)
        for mm in re.finditer(r"\b([A-Za-z_]\w*)\s*(?:\[[^\]]*\])?\s*\(", block):
            if mm.group(1) not in GO_KEYWORDS:
                def_offsets.add(base + mm.start(1))

    # struct 嵌入（inherit 基因）：type A struct { Base } / { *pkg.Base } / 多行嵌入
    inherits: List[str] = []
    for m in re.finditer(r"\btype\s+[A-Za-z_]\w*\s+struct\s*\{([^}]*)\}", text, flags=re.S):
        for line in m.group(1).splitlines():
            line = line.strip()
            # 嵌入字段特征：整行只有一个标识符链（可带 * 前缀或 pkg 前缀），
            # 且不是普通字段（Name string 这种双 token 的普通字段排除）
            if re.fullmatch(r"\*?[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*", line):
                base = line.lstrip("*")
                if base and base != "error":
                    inherits.append(base)

    calls: List[str] = []
    for m in re.finditer(r"\b([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\s*(?:\[[^\]]*\])?\s*\(", text):
        name = m.group(1)
        root = name.split(".")[0]
        if root in GO_KEYWORDS:
            continue
        pre = text[max(0, m.start() - 60):m.start()]
        if re.search(r"\bfunc\s*$", pre):
            continue                     # func Name( 定义，非调用
        if m.start() in def_offsets:
            continue                     # 函数/方法定义行，非调用
        calls.append(name)
    # 去重保序
    seen_calls: Set[str] = set()
    uniq_calls = [c for c in calls if not (c in seen_calls or seen_calls.add(c))]
    return uniq_calls, sorted(set(inherits))


# ========== Rust 函数级分析（v3.7 新增，零依赖轻量解析器） ==========
def _strip_rust_noise(text: str) -> str:
    """去掉 Rust 注释/字符串/字符/raw string/生命周期/属性，替换为空格（v3.7）。
    顺序关键：
      1. raw string 先（r#"..."# 内含引号）
      2. 生命周期先于 char（'a 不成对，必须单独剔除，否则 char 正则会把它和
         后面最近的 ' 配对吞掉一大段代码）
      3. 字符串先于 // 行注释（"http://x" 里的 // 不能被当成注释吃掉后面）
    """
    text = re.sub(r'r(#+)"(?:[^"\\]|\\.|"(?!#))*"', " ", text)   # raw string r#"..."#
    text = re.sub(r"'(?!')[A-Za-z_]\w*", " ", text)              # 生命周期 'a / 'static / '_（后不跟 ' 才是生命周期）
    text = re.sub(r'"(?:[^"\\]|\\.)*"', " ", text)               # 字符串（先于 // 注释，防 "http://x" 被破坏）
    text = re.sub(r"'(?:[^'\\]|\\.)*'", " ", text)               # char 字面量 'x' / '\n'
    text = re.sub(r"//[^\n]*", " ", text)                        # 行注释
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)           # 块注释
    text = re.sub(r"#!?\[[^\]]*\]", " ", text)                   # 属性 #[...] / #![...]
    return text


def extract_rust_call_graph(rs_file: str) -> Tuple[List[str], List[str]]:
    """轻量 Rust 函数级分析（v3.7 新增，零依赖启发式，非完整 AST）：
    返回 (calls, inherits)：
      calls    = 文件里出现过的被调用名或属性链（'fmt::format' / 'Foo::new' / 'obj.method'）
      inherits = trait 继承的父 trait 名（trait Foo: Bar + Baz）
    已剔除注释、字符串、raw string、属性噪音；函数/方法定义行不视为调用。
    Rust 无类继承，trait 继承（trait Foo: Bar）是最接近的形态，故提取为 inherit 基因。
    精确度靠 _build_genes 的跨文件符号验证兜底，不追求 100% AST 级。"""
    text = _strip_rust_noise(read_text(rs_file))
    if not text:
        return [], []

    def_offsets: Set[int] = set()        # 函数/方法定义位置（调用检测排除用）

    # 函数/方法定义：fn Name( | fn Name<T>( | fn Name<'a, T>( | pub(crate) fn ...
    for m in re.finditer(r"\bfn\s+([A-Za-z_]\w*)\s*(?:<[^>]*>)?\s*\(", text):
        if m.group(1) not in RUST_KEYWORDS:
            def_offsets.add(m.start(1))
    # 关联函数/方法含 where 子句：fn Name<T>(...) where T: Trait {
    # 上述正则已覆盖（<...> 后直接跟 '('；where 在 ')' 之后不影响名字偏移）

    # trait 继承（inherit 基因）：trait Foo: Bar + Baz where ... {
    inherits: List[str] = []
    for m in re.finditer(r"\btrait\s+[A-Za-z_]\w*\s*(?:<[^>]*>)?\s*:\s*([^{;]+?)\s*(?:where\b|\{)", text):
        for base in m.group(1).split("+"):
            base = base.strip()
            if base and base not in ("Sized", "Send", "Sync", "Unpin", "Copy", "Clone",
                                     "Debug", "Display", "Default", "Eq", "PartialEq",
                                     "Ord", "PartialOrd", "Hash", "Into", "From",
                                     "TryFrom", "TryInto", "Iterator", "AsRef", "AsMut",
                                     "Deref", "DerefMut", "Drop", "Error", "ToString",
                                     "FromStr"):
                # 过滤标准库常见 trait（跨文件验证时项目里通常没有定义，纯噪音）
                inherits.append(base)

    calls: List[str] = []
    for m in re.finditer(r"\b([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\s*\(", text):
        name = m.group(1)
        root = name.split(".")[0]
        if root in ("self", "Self"):
            # self.method( / self.storage.write( —— self 是关键字不能当 root，
            # 取叶子名记录（Rust 方法内调用是标配，漏记会导致死代码误报）
            leaf = name.split(".")[-1]
            if leaf not in RUST_KEYWORDS:
                calls.append(leaf)
            continue
        if root in RUST_KEYWORDS:
            continue
        pre = text[max(0, m.start() - 60):m.start()]
        if re.search(r"\bfn\s*$", pre):
            continue                     # fn Name( 定义，非调用
        if re.search(r"\bmacro_rules!\s*$", pre):
            continue                     # macro_rules! name( 定义
        if m.start() in def_offsets:
            continue                     # 函数/方法定义行，非调用
        calls.append(name)
    # 去重保序
    seen_calls: Set[str] = set()
    uniq_calls = [c for c in calls if not (c in seen_calls or seen_calls.add(c))]
    return uniq_calls, sorted(set(inherits))


# ========== 多语言定义提取（v3.5：死代码 / 复杂度共用；v3.7 加入 Rust） ==========
def _lineno_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _js_defs(text: str) -> List[Tuple[str, int, int, str]]:
    """JS/TS 定义提取：[(name, offset, lineno, kind)]，kind ∈ {func, class}。
    函数/类/箭头函数/对象方法（含 static/async/get/set）。"""
    out: List[Tuple[str, int, int, str]] = []
    for pat in (r"\bfunction\s+([A-Za-z_$][\w$]*)",
                r"\bclass\s+([A-Za-z_$][\w$]*)",
                r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?"
                r"(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>"):
        for m in re.finditer(pat, text):
            out.append((m.group(1), m.start(1), _lineno_of(text, m.start(1)),
                        "class" if "class" in pat else "func"))
    for m in re.finditer(r"(?m)^\s*(?:(?:static|async|get|set)\s+)?([A-Za-z_$][\w$]*)\s*\([^)]*\)\s*\{",
                         text):
        name = m.group(1)
        if name in JS_KEYWORDS:
            continue
        out.append((name, m.start(1), _lineno_of(text, m.start(1)), "func"))
    for m in re.finditer(r"(?:[{,;]\s*)(?:(?:static|async|get|set)\s+)?([A-Za-z_$][\w$]*)\s*\([^)]*\)\s*\{",
                         text):
        name = m.group(1)
        if name in JS_KEYWORDS:
            continue
        out.append((name, m.start(1), _lineno_of(text, m.start(1)), "func"))
    return out


def _java_defs(text: str) -> List[Tuple[str, int, int, str]]:
    """Java 定义提取：[(name, offset, lineno, kind)]。
    类（class/interface/enum/record）+ 方法/构造器（_java_def_context 启发式）。"""
    out: List[Tuple[str, int, int, str]] = []
    for m in re.finditer(r"\b(?:class|interface|enum|record)\s+([A-Za-z_$][\w$]*)", text):
        out.append((m.group(1), m.start(1), _lineno_of(text, m.start(1)), "class"))
    for m in re.finditer(r"\b([A-Za-z_$][\w$]*)\s*\([^)]*\)\s*(?:\s+implements\b[^{;]*)?"
                         r"(?:throws\s+[\w.,\s]+)?[;{]", text):
        name = m.group(1)
        if name in JAVA_KEYWORDS:
            continue
        pre = text[max(0, m.start(1) - 100):m.start(1)]
        if _java_def_context(pre):
            # 行首裸调用排除（与 extract_java_call_graph 一致）：helper(); 非定义
            if not re.search(r"[A-Za-z_$]", pre.split("\n")[-1]) \
                    and not m.group(0).rstrip().endswith("{"):
                continue
            out.append((name, m.start(1), _lineno_of(text, m.start(1)), "func"))
    return out


def _go_defs(text: str) -> List[Tuple[str, int, int, str]]:
    """Go 定义提取：[(name, offset, lineno, kind)]。
    函数（含泛型）/方法（receiver）/类型（struct/interface）。"""
    out: List[Tuple[str, int, int, str]] = []
    for m in re.finditer(r"\bfunc\s+([A-Za-z_]\w*)\s*(?:\[[^\]]*\])?\s*\(", text):
        if m.group(1) not in GO_KEYWORDS:
            out.append((m.group(1), m.start(1), _lineno_of(text, m.start(1)), "func"))
    for m in re.finditer(r"\bfunc\s*\([^)]*\)\s+([A-Za-z_]\w*)\s*(?:\[[^\]]*\])?\s*\(", text):
        if m.group(1) not in GO_KEYWORDS:
            out.append((m.group(1), m.start(1), _lineno_of(text, m.start(1)), "func"))
    for m in re.finditer(r"\btype\s+([A-Za-z_]\w*)\s+(?:struct|interface)\b", text):
        out.append((m.group(1), m.start(1), _lineno_of(text, m.start(1)), "class"))
    return out


def _rust_defs(text: str) -> List[Tuple[str, int, int, str]]:
    """Rust 定义提取：[(name, offset, lineno, kind)]，kind ∈ {func, class}。
    函数/方法（fn，含泛型/生命周期）/类型（struct/enum/trait）。"""
    out: List[Tuple[str, int, int, str]] = []
    for m in re.finditer(r"\bfn\s+([A-Za-z_]\w*)\s*(?:<[^>]*>)?\s*\(", text):
        if m.group(1) not in RUST_KEYWORDS:
            out.append((m.group(1), m.start(1), _lineno_of(text, m.start(1)), "func"))
    for m in re.finditer(r"\b(?:struct|enum|trait)\s+([A-Za-z_]\w*)", text):
        if m.group(1) not in RUST_KEYWORDS:
            out.append((m.group(1), m.start(1), _lineno_of(text, m.start(1)), "class"))
    return out


def _func_body_end(text: str, start: int) -> Optional[int]:
    """从 start 起找第一个 '{'，返回其匹配的 '}' 的 offset；无 '{' 返回 None。"""
    brace = text.find("{", start)
    if brace == -1:
        return None
    depth = 0
    for i in range(brace, len(text)):
        c = text[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i
    return None


def _count_complexity(body: str, lang: str) -> int:
    """轻量圈复杂度：1 + 分支/循环/条件关键词 + 逻辑与或 + 三元（JS/Java）。"""
    c = 1
    if lang in ("js", "java"):
        c += len(re.findall(r"\b(?:if|for|while|switch|case|catch)\b", body))
        c += len(re.findall(r"&&|\|\|", body))
        c += len(re.findall(r"\?[^.?]", body))            # 三元（排除 ?. 可选链 / ?? 空值合并）
    elif lang == "go":
        c += len(re.findall(r"\b(?:if|for|switch|case|select)\b", body))
        c += len(re.findall(r"&&|\|\|", body))
    elif lang == "rust":
        c += len(re.findall(r"\b(?:if|for|while|loop|match|case)\b", body))
        c += len(re.findall(r"&&|\|\|", body))
        c += len(re.findall(r"\?\s*[;\)\}]", body))      # ? 传播运算符（排除 ?Sized / ?'a）
    return c


def verify_candidate(py_file: str, target_symbol: str) -> bool:
    """小虫子验证：文件里是否真的引用了目标（v3 增强：import / 属性链 / 符号定义）"""
    ext = os.path.splitext(py_file)[1].lower()
    if ext == ".py":
        tree = _cache.get_ast(py_file)
        if tree is None:
            return False
        root_name = target_symbol.split(".")[0]
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == target_symbol or alias.name.startswith(target_symbol + "."):
                        return True
            elif isinstance(node, ast.ImportFrom):
                if node.module == target_symbol or node.module == root_name:
                    return True
            elif isinstance(node, ast.Attribute):
                if isinstance(node.value, ast.Name) and node.value.id == root_name:
                    return True
            elif isinstance(node, ast.Name) and node.id == root_name:
                return True
        return False
    text = read_text(py_file)
    if not text:
        return False
    key = target_symbol.split(".")[-1]
    return bool(re.search(r"\b" + re.escape(key) + r"\b", text))


def extract_symbols(path: str) -> List[str]:
    """提取文件里定义的函数/类名（多语言，语义搜索 + 跨文件调用验证的原料）。
    v3.2：走 FileCache.symbols，符号结果缓存，不再重复全树遍历。"""
    return _cache.symbols(path)
