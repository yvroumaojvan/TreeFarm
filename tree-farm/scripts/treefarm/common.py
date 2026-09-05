# -*- coding: utf-8 -*-
"""树场机制 —— 公共基础层（v4.7；v3.6 拆分自单文件 tree_farm.py）。

包含：配置常量 / 基因格式工具 / 文件分类与扫描 / 文件缓存 / 语义搜索 / 指纹与增量扫描。
仅依赖 Python 标准库。其他模块从这里 import 常量与基础函数。
"""

import ast
import hashlib
import logging
import os
import re
import time
from collections import Counter
from typing import Any, Dict, List, Optional, Set, Tuple

log = logging.getLogger("treefarm")

# ========== 配置常量（魔法数字全部收编于此） ==========
WEED_EXTS = {".json", ".txt", ".md", ".png", ".jpg", ".jpeg", ".gif", ".xml",
             ".yml", ".yaml", ".html", ".css", ".svg", ".csv", ".ini"}
CODE_EXTS = {".py", ".js", ".ts", ".java", ".c", ".h", ".cpp", ".go", ".rs"}
SKIP_DIRS = {".git", "__pycache__", "node_modules", "build", "dist", ".idea", ".vscode"}

SMALL_TREE_RATIO = 0.10          # 小树激活阈值：基因数 / 核心文件数
TRASH_WINDOW = 3                 # 垃圾箱熔断滑动窗口（小鸟来回次数）
TRASH_FAIL_LIMIT = 2             # 窗口内失败次数阈值（3 次里 2 次 = 66.7%）
CONVERGE_LIMIT = 2               # 一轮新增小鸟 ≤N 只 = 收敛
WEAK_CONFIRM_LIMIT = 2           # 弱耦合需 ≥N 个独立分支确认

SCHEMA_VERSION = 3               # 基因格式 schema 版本（v3：新增 call/inherit 关系）
VERSION = "4.9.4"                # 工具版本（v4.9.4：tornado 金标准误报治理——污点分级 param/concat/user、Popen 列表豁免、def open 排除、CRLF/临时文件/重定向/除零/竞态误报全消 + 测试问题 scope=test 独立展示）
DB_FILE = "tree_farm.db"         # 全部状态统一存一个 SQLite 文件

READ_HEAD_BYTES = 2000           # 内容匹配只读文件头
FINGERPRINT_BYTES = 4096         # 重命名检测内容指纹长度
GRAM_MAX = 300                   # 每文件内容 n-gram 索引上限
SEMANTIC_SYMBOL_THRESHOLD = 0.4  # 符号 n-gram 相似度阈值
SEMANTIC_CONTENT_THRESHOLD = 0.3  # 内容 n-gram 命中率阈值
LLM_TIMEOUT = 30                 # LLM 请求超时（秒）
LLM_CODE_CHARS = 6000            # 发给 LLM 的代码上限
BRIEF_MAX_SYMBOLS = 6            # 简报里非 Python 文件展示符号数
WEED_SUMMARY_LINES = 3           # 杂草摘要读前几行
WEED_SUMMARY_LEN = 60            # 杂草摘要截断长度
WEED_SHOW = 20                   # 简报展示前几个杂草

# ========== 多语言关键词（轻量解析器共用，v3.6 收编到公共层） ==========
JS_KEYWORDS = {"if", "for", "while", "switch", "catch", "return", "typeof",
               "delete", "new", "function", "class", "import", "export",
               "throw", "do", "else", "with", "in", "of", "try", "case",
               "default", "break", "continue", "yield", "await", "async",
               "var", "let", "const", "this", "super", "instanceof"}

JAVA_KEYWORDS = {"abstract", "assert", "boolean", "break", "byte", "case", "catch",
                 "char", "class", "const", "continue", "default", "do", "double",
                 "else", "enum", "extends", "final", "finally", "float", "for",
                 "goto", "if", "implements", "import", "instanceof", "int",
                 "interface", "long", "native", "new", "package", "private",
                 "protected", "public", "record", "return", "sealed", "short",
                 "static", "strictfp", "super", "switch", "synchronized", "this",
                 "throw", "throws", "transient", "try", "var", "void", "volatile",
                 "while", "yield", "true", "false", "null"}

# 出现在 '(' 前仍可能是定义的词（修饰符/返回类型/类型声明；其余关键字视为控制流/调用）
_JAVA_DEF_PREFIX = {"abstract", "default", "final", "native", "private", "protected",
                    "public", "record", "static", "strictfp", "synchronized", "transient",
                    "boolean", "byte", "char", "double", "float", "int", "long",
                    "short", "void", "var"}

GO_KEYWORDS = {"break", "case", "chan", "const", "continue", "default", "defer",
               "else", "fallthrough", "for", "func", "go", "goto", "if", "import",
               "interface", "map", "package", "range", "return", "select", "struct",
               "switch", "type", "var", "true", "false", "nil"}

# Rust 关键字（2015/2018/2021 edition 常用集合；宏调用 name! 由调用检测单独排除）
RUST_KEYWORDS = {"as", "async", "await", "break", "const", "continue", "crate",
                 "dyn", "else", "enum", "extern", "false", "fn", "for", "if",
                 "impl", "in", "let", "loop", "match", "mod", "move", "mut",
                 "pub", "ref", "return", "self", "Self", "static", "struct",
                 "super", "trait", "true", "type", "unsafe", "use", "where",
                 "while", "union", "abstract", "become", "box", "do", "final",
                 "macro", "override", "priv", "typeof", "unsized", "virtual",
                 "yield", "try", "macro_rules"}

# ========== 基因格式 schema（快递单 v3） ==========
GENE_SCHEMA = {
    "schema_version": SCHEMA_VERSION,
    "fields": [
        "id", "source", "target", "symbol", "relation",
        "direction", "kind", "confidence", "verified",
        "first_seen", "last_verified"
    ],
    "relations": "import / call / inherit / weak",
    "rules": "kind=strong 必须 verified=True；kind=weak 必须 confidence<=0.5 且双分支确认；"
             "call/inherit 由 AST 提取，可跨文件验证",
}


def stable_id(source: str, target: str, symbol: str, relation: str) -> str:
    """稳定基因 id：sha1(source|target|symbol|relation) 前 12 位 hex。
    修复 v2 的 hash() 随机种子问题（跨会话 id 不稳定）。"""
    raw = "|".join([source, target, symbol, relation]).encode("utf-8")
    return "g_" + hashlib.sha1(raw).hexdigest()[:12]


def normalize_gene(g: Dict[str, Any]) -> Dict[str, Any]:
    """把任意格式的基因填进快递单（缺的格子补默认值），旧基因自动兼容升级"""
    source = g.get("source", "")
    target = g.get("target", "")
    symbol = g.get("symbol", "")
    relation = g.get("relation", "weak" if g.get("kind") == "weak" else "import")
    base = {
        "id": g.get("id") or stable_id(source, target, symbol, relation),
        "source": source,
        "target": target,
        "symbol": symbol,
        "relation": relation,
        "direction": g.get("direction", ""),
        "kind": g.get("kind", "strong"),
        "confidence": g.get("confidence", 0.5 if g.get("kind") == "weak" else 1.0),
        "verified": bool(g.get("verified", g.get("kind", "strong") == "strong")),
        "first_seen": g.get("ts", g.get("first_seen", time.time())),
        "last_verified": g.get("last_verified", time.time()),
    }
    return base


# ========== 文件分类 & 扫描 ==========
def classify(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext in WEED_EXTS:
        return "weed"
    if ext in CODE_EXTS:
        return "tree"
    return "other"


def _seg_match(seg: str, pat: str) -> bool:
    """单段 glob 匹配（* ? 通配符，只匹配一段，不跨 /）"""
    regex = "".join("[^/]*" if c == "*" else ("[^/]" if c == "?" else re.escape(c))
                    for c in pat)
    return re.fullmatch(regex, seg) is not None


def _seg_seq_match(segs: List[str], pat_segs: List[str]) -> bool:
    """段序列匹配：** 匹配 0 或多个段，其余段逐段 glob 匹配"""
    def match(si: int, pi: int) -> bool:
        if pi == len(pat_segs):
            return si == len(segs)
        p = pat_segs[pi]
        if p == "**":
            for k in range(si, len(segs) + 1):
                if match(k, pi + 1):
                    return True
            return False
        if si >= len(segs):
            return False
        return _seg_match(segs[si], p) and match(si + 1, pi + 1)

    return match(0, 0)


def _matches_ignore(rel: str, patterns: Optional[List[str]]) -> bool:
    """gitignore 风格忽略匹配（v3.2 简化版，支持 ** / * / ?）：
    含 '/' 的 pattern 匹配相对路径（** 可跨目录）；
    不含 '/' 的 pattern 匹配任意层级的文件名/目录名。
    # 注释与 ! 取反不支持（保持简单）。"""
    if not patterns:
        return False
    segs = rel.replace(os.sep, "/").split("/")
    for pat in patterns:
        pat = pat.strip()
        if not pat or pat.startswith("#") or pat.startswith("!"):
            continue
        pat = pat.rstrip("/")
        if "/" not in pat:
            if any(_seg_match(seg, pat) for seg in segs):
                return True
        elif _seg_seq_match(segs, pat.split("/")):
            return True
    return False


def scan(root: str, ignore_patterns: Optional[List[str]] = None,
         ignore_dirs: Optional[List[str]] = None) -> Dict[str, List[str]]:
    """扫描项目文件（v3.2：支持 .gitignore 风格忽略规则 + 额外忽略目录）。
    ignore_patterns: 相对路径 glob（**/vendor/**、*.min.js、build/ 等）；
    ignore_dirs: 目录名列表（精确匹配，整棵子树跳过）。"""
    result: Dict[str, List[str]] = {"weed": [], "tree": [], "other": []}
    # v4.5 修复：单文件项目 - os.walk(文件) 返回空，需单独处理
    if os.path.isfile(root):
        result[classify(root)].append(root)
        return result
    ignore_dirs = set(ignore_dirs or [])
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root)
        kept: List[str] = []
        for d in dirnames:
            if d in SKIP_DIRS or d.startswith(".") or d in ignore_dirs:
                continue
            if rel_dir != "." and _matches_ignore(os.path.join(rel_dir, d), ignore_patterns):
                continue
            kept.append(d)
        dirnames[:] = kept
        for f in filenames:
            full = os.path.join(dirpath, f)
            rel = os.path.relpath(full, root)
            if _matches_ignore(rel, ignore_patterns):
                continue
            result[classify(full)].append(full)
    return result


def rel_module(root: str, path: str) -> str:
    """路径 → 模块名（修复 v2 bug：非 Python 文件 [:-3] 截错扩展名）"""
    rel = os.path.relpath(path, root)
    name, _ = os.path.splitext(rel)
    mod = name.replace(os.sep, ".")
    if mod.endswith(".__init__"):
        mod = mod[: -len(".__init__")]
    return mod


def module_to_path(root: str, module_name: str) -> Optional[str]:
    """模块名 → 文件路径（支持 .py 包、index.js 约定、多语言扩展名）"""
    parts = module_name.replace("\\", "/").split("/")
    parts = [p for p in parts if p]
    if not parts:
        return None
    if parts[-1].endswith(".py"):
        parts[-1] = parts[-1][:-3]
    cand = os.path.join(root, *parts) + ".py"
    if os.path.isfile(cand):
        return cand
    cand2 = os.path.join(root, *parts, "__init__.py")
    if os.path.isfile(cand2):
        return cand2
    for ext in (".js", ".ts", ".java", ".go", ".c", ".h", ".cpp"):
        cand3 = os.path.join(root, *parts) + ext
        if os.path.isfile(cand3):
            return cand3
        cand4 = os.path.join(root, *parts, "index" + ext)
        if os.path.isfile(cand4):
            return cand4
    return None


class FileCache:
    """文件内容 / AST 缓存（v3.2 性能优化：消灭首次扫描的重复读文件 + 重复 AST 解析）。

    键 = (绝对路径, st_mtime_ns, st_size) —— 文件一变更键就变，缓存自动失效，
    不会读到过期内容（与增量扫描的 mtime/size 判定逻辑一致）。
    FIFO 淘汰 + 容量上限，内存有界；plant 扫描前后 clear() 释放。
    """

    def __init__(self, max_entries: int = 4096):
        self.max_entries = max_entries
        self._text: Dict[Tuple[str, int, int], str] = {}
        self._asts: Dict[Tuple[str, int, int], Optional[ast.Module]] = {}
        self._syms: Dict[Tuple[str, int, int], List[str]] = {}
        self._order: List[Tuple[str, int, int]] = []

    def clear(self) -> None:
        self._text.clear()
        self._asts.clear()
        self._syms.clear()
        self._order.clear()

    def _key(self, path: str) -> Optional[Tuple[str, int, int]]:
        try:
            st = os.stat(path)
            return (path, st.st_mtime_ns, st.st_size)
        except OSError:
            return None

    def _touch(self, key: Tuple[str, int, int]) -> None:
        self._order.append(key)
        if len(self._order) > self.max_entries:
            drop = len(self._order) - self.max_entries
            for old in self._order[:drop]:
                self._text.pop(old, None)
                self._asts.pop(old, None)
                self._syms.pop(old, None)
            self._order = self._order[drop:]

    def text(self, path: str) -> str:
        """读文件文本（带缓存）。读失败返回 ''。"""
        key = self._key(path)
        if key is None:
            return ""
        val = self._text.get(key)
        if val is None:
            val = self._read_uncached(path)
            self._text[key] = val
            self._touch(key)
        return val

    def get_ast(self, path: str) -> Optional[ast.Module]:
        """AST 解析（带缓存）。非 Python / 解析失败返回 None。
        方法名用 get_ast 而非 ast，避免与模块级 ast 混淆（深度分析报告建议）。"""
        key = self._key(path)
        if key is None or not path.endswith(".py"):
            return None
        tree = self._asts.get(key)
        if tree is None:
            tree = self._parse_uncached(path)
            self._asts[key] = tree
            self._touch(key)
        return tree

    def symbols(self, path: str) -> List[str]:
        """文件定义的函数/类名（带缓存，v3.2：避免重复全树遍历）。"""
        key = self._key(path)
        if key is None:
            return []
        val = self._syms.get(key)
        if val is None:
            val = self._symbols_uncached(path)
            self._syms[key] = val
            self._touch(key)
        return val

    def _symbols_uncached(self, path: str) -> List[str]:
        """提取符号（Python 走 AST 缓存，其他语言走文本缓存 + 正则）"""
        syms: List[str] = []
        if path.endswith(".py"):
            tree = self.get_ast(path)
            if tree is not None:
                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        syms.append(node.name)
        else:
            text = self.text(path)
            if not text:
                return syms
            syms += re.findall(r"\bfunction\s+(\w+)", text)
            syms += re.findall(r"\bclass\s+(\w+)", text)
            syms += re.findall(r"\bdef\s+(\w+)", text)
            # v3.3/v3.4：JS/TS 箭头函数符号（const/let/var name = (...) => 或 name = x =>）
            syms += re.findall(r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?"
                               r"(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>", text)
            # Go 走专属正则（见下方），跳过通用方法定义正则（会把 import ( 误判成符号）
            if not path.endswith(".go"):
                syms += re.findall(r"\b(?:public|private|protected)?\s*(?:static\s+)?\w[\w<>, \[\]]*\s+(\w+)\s*\(", text)
            # v3.4：Java 方法定义符号（修饰符/返回类型 + 名字 + (...) [throws] { 或 ;）
            if path.endswith(".java"):
                for m in re.finditer(r"(?m)^\s*[A-Za-z_$][\w$<>?\[\], .]*\s+([A-Za-z_$][\w$]*)\s*\([^)]*\)"
                                     r"\s*(?:throws\s+[\w.,\s]+)?[;{]", text):
                    if m.group(1) not in JAVA_KEYWORDS:
                        syms.append(m.group(1))
            # v3.5：Go 函数/方法/类型符号（func Name( / func (r *T) Name( / type Name struct|interface）
            if path.endswith(".go"):
                syms += re.findall(r"\bfunc\s+([A-Za-z_]\w*)\s*(?:\[[^\]]*\])?\s*\(", text)
                syms += re.findall(r"\bfunc\s*\([^)]*\)\s+([A-Za-z_]\w*)\s*(?:\[[^\]]*\])?\s*\(", text)
                syms += re.findall(r"\btype\s+([A-Za-z_]\w*)\s+(?:struct|interface)\b", text)
            # v3.7：Rust 函数/类型符号（fn Name( / struct|enum|trait Name）
            if path.endswith(".rs"):
                syms += re.findall(r"\bfn\s+([A-Za-z_]\w*)\s*(?:<[^>]*>)?\s*\(", text)
                syms += re.findall(r"\b(?:struct|enum|trait)\s+([A-Za-z_]\w*)", text)
        return sorted(set(syms))

    @staticmethod
    def _read_uncached(path: str) -> str:
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                return f.read()
        except Exception as e:
            log.debug("读取失败 %s: %s", path, e)
            return ""

    @staticmethod
    def _parse_uncached(path: str) -> Optional[ast.Module]:
        text = FileCache._read_uncached(path)
        if not text:
            return None
        try:
            return ast.parse(text)
        except SyntaxError as e:
            log.debug("AST 解析失败 %s: %s", path, e)
            return None


_cache = FileCache()


def read_text(path: str) -> str:
    """读文件文本（v3.2：走 FileCache，首次扫描不再重复读盘）"""
    return _cache.text(path)


# ========== 语义搜索（v3：标识符归一化 + 预构建索引） ==========
def normalize_identifier(s: str) -> str:
    """标识符归一化：camelCase / PascalCase / snake_case / kebab-case → 下划线小写。
    UserAuth → user_auth；userAuth → user_auth；user-auth → user_auth"""
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", s)   # XMLParser → XML_Parser
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", s)       # userAuth → user_Auth
    return s.lower().replace("-", "_")


def _ngrams(text: str, n: int = 3) -> List[str]:
    text = text.lower()
    return [text[i:i + n] for i in range(max(len(text) - n + 1, 0))]


def _cosine(a: str, b: str) -> float:
    """字符 n-gram 余弦相似度 0~1"""
    ca, cb = Counter(_ngrams(a)), Counter(_ngrams(b))
    if not ca or not cb:
        return 0.0
    inter = sum((ca & cb).values())
    if not inter:
        return 0.0
    return inter / (sum(ca.values()) * sum(cb.values())) ** 0.5


def _gram_hashes(text: str, n: int = 3, limit: int = GRAM_MAX) -> List[str]:
    """内容 n-gram → 哈希集合（预构建索引用，只留含字母数字的 gram，上限 limit）"""
    grams = [g for g in _ngrams(text, n) if re.search(r"[A-Za-z0-9\u4e00-\u9fff]", g)]
    return [hashlib.sha1(g.encode("utf-8")).hexdigest()[:6] for g in grams[:limit]]


def identifier_tokens(s: str) -> Set[str]:
    """标识符分块：user_auth → {user, auth}，用于精确 token 匹配"""
    return set(normalize_identifier(s).split("_"))


class SymbolIndex:
    """符号索引：符号 → [文件...]（v3 可持久化到 search_index 表）"""

    def __init__(self, index: Optional[Dict[str, List[str]]] = None):
        self.index = index or {}

    def find_symbol(self, symbol: str) -> List[str]:
        return self.index.get(symbol, [])

    def all_symbols(self) -> List[str]:
        return sorted(self.index.keys())


def semantic_search(query: str, tree_files: List[str], symbol_index: SymbolIndex,
                    content_index: Optional[Dict[str, List[str]]] = None, top: int = 5) -> List[Tuple[str, float, str]]:
    """语义搜索（v3：归一化 + 预构建内容索引）：
    三级搜索：
    1. 符号精确命中（含归一化后精确命中）
    2. 符号模糊匹配（n-gram 相似度 ≥ 阈值；标识符分块 token 命中加分）
    3. 内容命中率（预构建 n-gram 哈希索引，Jaccard 命中率 ≥ 阈值；无索引时回退实时读文件头）
    """
    q_norm = normalize_identifier(query)
    q_tokens = identifier_tokens(query)
    results: List[Tuple[str, float, str]] = []

    # 一级：符号精确（原样 + 归一化）
    for f in symbol_index.find_symbol(query):
        results.append((f, 1.0, f"符号精确命中: {query}"))
    if q_norm != query:
        for f in symbol_index.find_symbol(q_norm):
            results.append((f, 1.0, f"符号精确命中: {q_norm}"))

    # 二级：符号模糊（归一化 n-gram + token 命中）
    fuzzy: List[Tuple[str, float, str]] = []
    for sym, files in symbol_index.index.items():
        s_norm = normalize_identifier(sym)
        score = _cosine(q_norm, s_norm)
        if score >= SEMANTIC_SYMBOL_THRESHOLD:
            for f in files:
                fuzzy.append((f, score, f"符号相似: {sym}"))
        elif q_tokens and q_tokens & identifier_tokens(sym):
            for f in files:
                fuzzy.append((f, 0.5, f"符号分块命中: {sym}"))

    # 三级：内容命中率（预构建索引 or 实时）
    if content_index is not None:
        q_hashes = set(_gram_hashes(query))
        if q_hashes:
            for f in tree_files:
                fg = set(content_index.get(f, []))
                if fg:
                    hit = len(q_hashes & fg) / len(q_hashes)
                    if hit >= SEMANTIC_CONTENT_THRESHOLD:
                        fuzzy.append((f, hit, f"内容命中 {hit:.0%}"))
    else:
        for f in tree_files:
            head = read_text(f)[:READ_HEAD_BYTES]
            score = _cosine(query, os.path.basename(f)) * 0.5 + _cosine(query, head) * 0.5
            if score >= SEMANTIC_CONTENT_THRESHOLD:
                fuzzy.append((f, score, "内容模糊匹配"))

    fuzzy.sort(key=lambda x: -x[1])
    seen: Set[str] = set()
    for f, s, why in fuzzy:
        if f not in seen:
            seen.add(f)
            results.append((f, round(s, 2), why))
    best: Dict[str, Tuple[float, str]] = {}
    for f, s, why in results:
        if f not in best or s > best[f][0]:
            best[f] = (s, why)
    final = sorted([(f, s, why) for f, (s, why) in best.items()], key=lambda x: -x[1])
    return final[:top]


# ========== 指纹与增量扫描（v3：新增/修改/删除/重命名） ==========
def file_fingerprint(path: str) -> str:
    """内容指纹：sha1(前 FINGERPRINT_BYTES 字节 + 大小)。用于重命名检测。"""
    try:
        with open(path, "rb") as f:
            head = f.read(FINGERPRINT_BYTES)
        return hashlib.sha1(head + str(os.path.getsize(path)).encode()).hexdigest()[:12]
    except OSError:
        return ""


def scan_changes(tree_files: List[str], bank: Any) -> Tuple[Dict[str, Tuple[float, int, str]],
                                                            List[str], List[Tuple[str, str]]]:
    """真增量扫描（v3）。
    返回 (changed, deleted, renamed)：
      changed  = {path: (mtime, size, fingerprint)}  新增或内容变更的文件
      deleted  = 记录过但已不存在的文件
      renamed  = [(旧路径, 新路径)] 内容指纹匹配出的重命名
    """
    prev = bank.mtime_snapshot()
    current = {}
    for f in tree_files:
        try:
            st = os.stat(f)
        except OSError:
            continue
        old = prev.get(f)
        # v3.2：mtime/size 都没变 → 复用旧指纹（不重读文件）；变了才重算。
        # 旧记录无指纹（迁移数据）时也重算，避免迁移后首次误判。
        if old is not None and old[0] == st.st_mtime and old[1] == st.st_size:
            fp = old[2] if len(old) >= 3 else ""
            if fp:
                current[f] = (st.st_mtime, st.st_size, fp)
                continue
        fp = file_fingerprint(f)
        current[f] = (st.st_mtime, st.st_size, fp)

    deleted = [f for f in prev if f not in current]
    changed: Dict[str, Tuple[float, int, str]] = {}
    for f, meta in current.items():
        old = prev.get(f)
        if old is None or old[0] != meta[0] or old[1] != meta[1]:
            changed[f] = meta
        elif len(old) >= 3 and old[2] and meta[2] and old[2] != meta[2]:
            # mtime/size 没变但指纹变了（罕见：内容改但 mtime 没动），仍重扫。
            # 注意：旧记录无指纹（迁移数据）时不做此比较，避免迁移后首次全量误判。
            changed[f] = meta

    # 重命名检测：消失文件的指纹 == 新出现文件的指纹 → 视为重命名
    renamed: List[Tuple[str, str]] = []
    new_files = [f for f in current if f not in prev]
    # 指纹 → 新路径（注意：字典 in 查 key，所以按指纹建索引）
    new_fp = {current[f][2]: f for f in new_files if current[f][2]}
    for old in deleted:
        old_meta = prev.get(old, [])
        old_fp = old_meta[2] if len(old_meta) >= 3 else ""
        if old_fp and old_fp in new_fp:
            renamed.append((old, new_fp[old_fp]))
    return changed, deleted, renamed
