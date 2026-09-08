# -*- coding: utf-8 -*-
"""树场机制 —— 多语言基因/符号提取层（v4.7；v3.6 拆分自单文件 tree_farm.py）。

包含：import 级基因提取（Python AST / 其余语言正则）、函数级调用分析
（Python AST 精确；JS/TS、Java、Go 零依赖轻量解析器）、多语言定义提取
（死代码/复杂度共用）、小虫子候选验证、符号提取。
"""

import ast
import os
import re
from typing import List, Optional, Set, Tuple

from .common import (CPP_KEYWORDS, GO_KEYWORDS, JAVA_KEYWORDS, JS_KEYWORDS,
                     RUST_KEYWORDS, _JAVA_DEF_PREFIX, _cache, read_text)


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


def _strip_kotlin_noise(text: str) -> str:
    """去掉 Kotlin 注释与字符串字面量（含三引号原始字符串），替换为空格（v4.9.11）。"""
    text = re.sub(r"//[^\n]*", " ", text)
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    text = re.sub(r'"""[\s\S]*?"""', " ", text)      # 三引号原始字符串
    text = re.sub(r"'(?:[^'\\]|\\.)*'", " ", text)   # char 字面量
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


# ========== C/C++ 函数级分析（v4.9.8 新增，零依赖轻量解析器） ==========
# 覆盖 .c/.h/.cpp：函数定义（含指针返回/类作用域 ::/构造析构 ~/抽象 =0[免]),
# 函数调用（obj.method / ptr->method / ns::func）、类继承（class X : public Y）。
# 统一按 C++ 关键字解析（C++ 关键字是 C 超集，解析 .c 无副作用）。

_CPLUS_TRAILERS = r"(?:\s+(?:const|volatile|noexcept|override|final|requires\s*\([^)]*\)))*"


def _strip_c_noise(text: str) -> str:
    """去掉 C/C++ 注释、字符串、字符字面量、预处理指令，替换为空格（v4.9.8）。
    顺序关键（沿用 Rust 教训）：
      0. #if 0 ... #endif 禁用块整块剥掉（真实项目超常见，函数提取不能进禁用代码）
      1. 字符串先于 // 行注释（"http://x" 里的 // 不能被当成注释吃掉后面）
      2. char 字面量在字符串后（模板/泛型里的 'a' 不配对会吞代码，用 [^'\\]|\\. 限定）
      3. 预处理整行（#include/#define/#if 等）最后清——# 可能出现在字符串里已清掉
    """
    text = re.sub(r"(?ms)^[ \t]*#[ \t]*if\s+0\b.*?^[ \t]*#[ \t]*endif\b", " ", text)
    # v4.9.8 第5轮：残缺 #if 0（无 #endif 的截断文件）→ 剥到文件尾，防禁用代码被提取
    text = re.sub(r"(?ms)^[ \t]*#[ \t]*if\s+0\b(?:(?!^[ \t]*#[ \t]*endif\b).)*", " ", text)
    text = re.sub(r'"(?:[^"\\]|\\.)*"', " ", text)       # 字符串
    text = re.sub(r"'(?:[^'\\]|\\.)*'", " ", text)        # char 字面量
    text = re.sub(r"//[^\n]*", " ", text)                 # 行注释
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)    # 块注释
    text = re.sub(r"(?m)^[ \t]*#.*$", " ", text)          # 预处理整行
    return text


def _c_defs(text: str) -> List[Tuple[str, int, int, str]]:
    """C/C++ 定义提取：[(name, offset, lineno, kind)]，kind ∈ {func, class}。

    函数/方法：NAME(...) 后跟 {（含尾随 const/noexcept/override）；NAME 可带
    ns::Class:: 作用域（构造/析构 ~Foo）、可带返回类型指针（前面部分不参与）。
    排除：关键字（if/for/while/switch/return/new/delete/cast 等）、全大写宏名、
    operator 运算符重载、函数声明（NAME(...) 后跟 ;）。
    类/结构体/联合：class X : public Y { / struct X { / union X{（排除前向声明 X;）。
    """
    out: List[Tuple[str, int, int, str]] = []
    # 类/结构体/联合定义（含继承列表 / final）；排除前向声明 class Foo; ——
    # 继承列表由 extract_c 单独提（inherit 基因）
    for m in re.finditer(
            r"\b(class|struct|union)\s+([A-Za-z_]\w*)"
            r"(?:\s+final)?\s*(?::\s*[^{]*)?\{", text):
        if m.group(2) not in CPP_KEYWORDS:
            out.append((m.group(2), m.start(2), _lineno_of(text, m.start(2)), "class"))
    # 函数/方法定义：NAME(...){ / NAME(...) : init-list { （构造初始化列表）
    # 类名可带模板参数：Stack<T>::push(const T&)（v4.9.8 模板方法形态）
    for m in re.finditer(
            r"(?m)(?!\b(?:if|for|while|switch|catch|return|sizeof|delete|new|"
            r"static_cast|dynamic_cast|const_cast|reinterpret_cast|decltype|"
            r"typeid|using|template)\s*\()"
            r"(?<![\w:~])(~?[A-Za-z_]\w*(?:<[^>]*>)?(?:::[A-Za-z_~]\w*(?:<[^>]*>)?)*)"
            r"\s*\(([^;{}]*)\)"
            r"(?:\s*:\s*[^{;]+)?" + _CPLUS_TRAILERS + r"\s*(\{|;)",
            text):
        name = m.group(1)
        root = name.split("::")[0]
        if root in CPP_KEYWORDS:
            continue
        leaf = name.split("::")[-1]
        if leaf.startswith("operator"):
            continue                        # operator+ 等运算符重载
        if m.group(3) == ";":
            continue                        # 函数声明（.h 原型），非定义
        if leaf.isupper():
            continue                        # 全大写宏名（DECLARE_X(...) { 形态）
        out.append((leaf, m.start(1), _lineno_of(text, m.start(1)), "func"))
    return out


def extract_c_call_graph(c_file: str) -> Tuple[List[str], List[str]]:
    """轻量 C/C++ 函数级分析（v4.9.8 新增，零依赖启发式，非完整 AST）：
    返回 (calls, inherits)：
      calls    = 被调用名或作用域链（'func' / 'obj.method' / 'p->method'->'method' /
                 'ns::func'->'ns.func'）；:: 转为 . 便于跨文件符号匹配
      inherits = 类继承/实现的基类名（class X : public Y / struct S : B）
    已剔除注释、字符串、预处理噪音；定义行/声明行/析构 ~/运算符重载不视为调用。
    精确度靠 _build_genes 的跨文件符号验证兜底，不追求 100% AST 级。"""
    text = _strip_c_noise(read_text(c_file))
    if not text:
        return [], []

    def_offsets: Set[int] = set()
    decl_offsets: Set[int] = set()
    for name, off, _, _ in _c_defs(text):
        def_offsets.add(off)
    # 声明行（.h 原型 / 纯虚 = 0）：NAME(...) ;  → 名字位置加入排除集合。
    # 严格要求「返回类型 + NAME(...) ;」形态（int add(int a); / virtual void run() = 0;），
    # 避免把真实调用语句 add(1, helper(2)); 误判为声明（首轮实测驱动）。
    # 返回类型排除语句关键字：return/if/for/while... 否则 "return used_func();"
    # 会被误判成「伪类型 return + 声明」，把真实调用吞掉（第2轮实测驱动）。
    for m in re.finditer(
            r"(?<![\w:~])(?:(?:virtual|static|extern|constexpr|inline|friend|"
            r"explicit|mutable|const)\s+)*"
            r"(?!\b(?:return|if|for|while|switch|catch|sizeof|new|delete|goto|"
            r"throw|case|do|else|continue|break|typedef)\b)"
            r"([A-Za-z_]\w*(?:\s+\w+){0,2}(?:\s*[*&]+)?)"   # 返回类型：≤3 词 + 可选指针（有界，防 ReDoS）
            r"\s+"
            r"(~?[A-Za-z_]\w*(?:::[A-Za-z_~]\w*)*)\s*\([^;{}]*\)"
            r"(?:\s*(?:const|override|noexcept|final|volatile))*\s*(?:=\s*0)?\s*;",
            text):
        decl_offsets.add(m.start(2))

    inherits: List[str] = []
    # class Foo : public Bar, private Baz {
    for m in re.finditer(
            r"\b(?:class|struct|union)\s+[A-Za-z_]\w*\s*:\s*"
            r"(?:public|private|protected)\s+([\w:]+)", text):
        inherits.append(m.group(1))

    calls: List[str] = []
    for m in re.finditer(r"\b([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*"
                         r"(?:::[A-Za-z_~]\w*)*)\s*\(", text):
        name = m.group(1).replace("::", ".")
        root = name.split(".")[0]
        if root in CPP_KEYWORDS or root.isupper():
            continue                       # 关键字调用（if(/return(） / 宏调用
        pre = text[max(0, m.start() - 80):m.start()]
        if pre.rstrip().endswith("~"):
            continue                       # ~Foo( 析构调用（含 ~Foo() 定义里的 Foo(）
        if re.search(r"\)\s*[,:]\s*$", pre) or re.search(r"\(\s*[,:]\s*$", pre):
            continue                       # 构造初始化列表 Foo() : count_(0) / 补参, field(x)
        if m.start(1) in def_offsets or m.start(1) in decl_offsets:
            continue                       # 函数/方法定义行或声明行，非调用
        if "operator" in name:
            continue                       # operator<( 等重载，非普通调用
        calls.append(name)
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


def _kotlin_defs(text: str) -> List[Tuple[str, int, int, str]]:
    """Kotlin 定义提取：[(name, offset, lineno, kind)]。
    v4.9.11 新增：类/接口/枚举/数据类/对象 + fun 函数/方法 + val/var 箭头函数。
    Kotlin 语法与 Java 相近，但用 fun 关键字定义函数（Java 正则不识别）。"""
    out: List[Tuple[str, int, int, str]] = []
    for m in re.finditer(r"\b(?:class|interface|enum\s+class|data\s+class|sealed\s+class|object)\s+"
                         r"([A-Za-z_][\w]*)", text):
        out.append((m.group(1), m.start(1), _lineno_of(text, m.start(1)), "class"))
    # fun 函数/方法：fun name( / fun <T> name( / fun Name.name( / fun (A)->B.name(
    for m in re.finditer(r"\bfun\s+(?:<[^>]+>\s*)?(?:[A-Za-z_][\w<>?, .]*\.)?"
                         r"([A-Za-z_][\w]*)\s*\(", text):
        name = m.group(1)
        if name in JAVA_KEYWORDS:
            continue
        out.append((name, m.start(1), _lineno_of(text, m.start(1)), "func"))
    # val/var 箭头函数：val name = { ... } / val name: T = fun(...) / val name = { a, b -> ... }
    for m in re.finditer(r"\b(?:val|var)\s+([A-Za-z_][\w]*)\s*(?::[^=\n]*)?=\s*"
                         r"(?:\{[^}]*\}|fun\s*\([^)]*\)|\([^)]*\)\s*->)", text):
        out.append((m.group(1), m.start(1), _lineno_of(text, m.start(1)), "func"))
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
    if lang in ("js", "java", "kt"):
        c += len(re.findall(r"\b(?:if|for|while|switch|case|catch|when)\b", body))
        c += len(re.findall(r"&&|\|\|", body))
        c += len(re.findall(r"\?[^.?]", body))            # 三元（排除 ?. 可选链 / ?? 空值合并）
    elif lang == "go":
        c += len(re.findall(r"\b(?:if|for|switch|case|select)\b", body))
        c += len(re.findall(r"&&|\|\|", body))
    elif lang == "rust":
        c += len(re.findall(r"\b(?:if|for|while|loop|match|case)\b", body))
        c += len(re.findall(r"&&|\|\|", body))
        c += len(re.findall(r"\?\s*[;\)\}]", body))      # ? 传播运算符（排除 ?Sized / ?'a）
    elif lang in ("c", "cpp"):
        c += len(re.findall(r"\b(?:if|for|while|switch|do|catch)\b", body))
        c += len(re.findall(r"&&|\|\|", body))
        c += len(re.findall(r"\?[^?.]", body))           # 三元（排除 ?. 与 ? 语句）
        c += len(re.findall(r"\bcase\s+[^:;]*:", body))  # switch case（单独计数，防双重）
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
