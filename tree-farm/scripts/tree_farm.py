# -*- coding: utf-8 -*-
"""
树场机制 —— v2.0
对应设计文档：《树场机制的核心.txt》

v2.0 新增：
  · 基因格式 schema 拍板（快递单：source/target/symbol/relation/kind/confidence/verified...）
  · LLM API 接入（--analyze：引擎自己「打电话」让 AI 报小鸟，OpenAI 兼容接口）
  · 语义搜索（--search：符号索引 + n-gram 模糊匹配，零依赖轻量版）
  · 多语言支持（Python AST + JS/TS/Java/C/C++/Go 正则提取）

用法：
  python3 tree_farm.py <项目>                      # 树场体检
  python3 tree_farm.py <项目> --brief              # AI 工作简报
  python3 tree_farm.py <项目> --bird <源> <目标>   # 报小鸟（强耦合）
  python3 tree_farm.py <项目> --bird-weak <源> <目标>   # 报小鸟（弱耦合）
  python3 tree_farm.py <项目> --analyze <文件>     # 让 LLM 自动找小鸟（需配 key，无 key 显示 dry-run）
  python3 tree_farm.py <项目> --search <关键词>    # 语义搜索相关文件/符号
  python3 tree_farm.py <项目> --symbols <文件>     # 看文件里的符号（函数/类名）
  python3 tree_farm.py <项目> --trash              # 垃圾箱
  python3 tree_farm.py <项目> --next-round         # 新一轮
  python3 tree_farm.py <项目> --converge           # 收敛检查
  python3 tree_farm.py <项目> --fix <文件>         # 修复协议

LLM 配置（环境变量）：
  TREEFARM_API_KEY=你的key TREEFARM_API_URL=https://api.deepseek.com/v1 TREEFARM_MODEL=deepseek-chat
  （任何 OpenAI 兼容接口都行：DeepSeek / 通义 / 豆包 / Kimi / 本地 Ollama 等）
"""

import ast
import json
import os
import re
import sys
import time
from collections import Counter

# ========== 配置 ==========
WEED_EXTS = {".json", ".txt", ".md", ".png", ".jpg", ".jpeg", ".gif", ".xml",
             ".yml", ".yaml", ".html", ".css", ".svg", ".csv", ".ini"}
CODE_EXTS = {".py", ".js", ".ts", ".java", ".c", ".h", ".cpp", ".go", ".rs"}
SKIP_DIRS = {".git", "__pycache__", "node_modules", "build", "dist", ".idea", ".vscode"}
SMALL_TREE_RATIO = 0.10
TRASH_FAIL_LIMIT = 2
CONVERGE_LIMIT = 2
WEAK_CONFIRM_LIMIT = 2
GENE_FILE = "gene_bank.json"
MTIME_FILE = "mtime_index.json"
SESSION_FILE = "session.json"
SCHEMA_VERSION = 2

# ========== 基因格式 schema（快递单，v2.0 拍板）==========
GENE_SCHEMA = {
    "schema_version": SCHEMA_VERSION,
    "fields": [
        "id",           # 基因编号
        "source",       # 文件A（谁依赖）
        "target",       # 目标（模块名或符号）
        "symbol",       # 具体符号（函数/类/变量名，没有就填空串）
        "relation",     # 关系类型: import / call / inherit / weak
        "direction",    # 方向: out（A引用B）/ in（B被引用，冗余由系统推）
        "kind",         # 强耦合 strong / 弱耦合 weak
        "confidence",   # 可信度 0~1
        "verified",     # 小虫子是否验证过
        "first_seen",   # 首次入库时间
        "last_verified" # 最后验证时间
    ],
    "rules": "kind=strong 必须 verified=True；kind=weak 必须 confidence<=0.5 且双分支确认",
}


def normalize_gene(g):
    """把任意格式的基因填进快递单（缺的格子补默认值），旧基因自动兼容升级"""
    base = {
        "id": "g_" + str(abs(hash(g.get("source", "") + "|" + g.get("target", ""))) % 10 ** 8),
        "source": g.get("source", ""),
        "target": g.get("target", ""),
        "symbol": g.get("symbol", ""),
        "relation": g.get("relation", "weak" if g.get("kind") == "weak" else "import"),
        "direction": g.get("direction", ""),
        "kind": g.get("kind", "strong"),
        "confidence": g.get("confidence", 1.0),
        "verified": g.get("verified", g.get("kind", "strong") == "strong"),
        "first_seen": g.get("ts", g.get("first_seen", time.time())),
        "last_verified": g.get("last_verified", time.time()),
    }
    return base


# ========== 文件分类 & 扫描 ==========
def classify(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in WEED_EXTS:
        return "weed"
    if ext in CODE_EXTS:
        return "tree"
    return "other"


def scan(root):
    result = {"weed": [], "tree": [], "other": []}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for f in filenames:
            full = os.path.join(dirpath, f)
            result[classify(full)].append(full)
    return result


def module_to_path(root, module_name):
    parts = module_name.replace("\\", "/").split("/")
    parts = [p for p in parts if p]
    if not parts:
        return None
    # 去掉扩展名再试
    if parts[-1].endswith(".py"):
        parts[-1] = parts[-1][:-3]
    # 试 .py 和包
    cand = os.path.join(root, *parts) + ".py"
    if os.path.isfile(cand):
        return cand
    cand2 = os.path.join(root, *parts, "__init__.py")
    if os.path.isfile(cand2):
        return cand2
    # 多语言：原样拼接
    for ext in (".js", ".ts", ".java", ".go", ".c", ".h", ".cpp"):
        cand3 = os.path.join(root, *parts) + ext
        if os.path.isfile(cand3):
            return cand3
        cand4 = os.path.join(root, *parts, "index" + ext)
        if os.path.isfile(cand4):
            return cand4
    return None


# ========== 基因提取（多语言）==========
IMPORT_PATTERNS = {
    ".py": None,  # 走 AST
    ".js": [r"import\s+[^'\"\n]*?from\s*['\"]([^'\"]+)['\"]",
            r"require\s*\(\s*['\"]([^'\"]+)['\"]\s*\)"],
    ".ts": [r"import\s+[^'\"\n]*?from\s*['\"]([^'\"]+)['\"]"],
    ".java": [r"import\s+(?:static\s+)?([\w.]+)"],
    ".c": [r"#\s*include\s*[<\"]([^>\"]+)[>\"]"],
    ".h": [r"#\s*include\s*[<\"]([^>\"]+)[>\"]"],
    ".cpp": [r"#\s*include\s*[<\"]([^>\"]+)[>\"]"],
    ".go": [r"import\s*\(([^)]*)\)", r"import\s+\"([^\"]+)\""],
}


def extract_genes(py_file):
    """提取强耦合基因（多语言）。返回 [(target, relation)]"""
    ext = os.path.splitext(py_file)[1].lower()
    found = []
    try:
        with open(py_file, "r", encoding="utf-8", errors="ignore") as f:
            text = f.read()
    except Exception:
        return found

    if ext == ".py":
        try:
            tree = ast.parse(text)
        except Exception:
            return found
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    found.append((alias.name, "import"))
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                for alias in node.names:
                    found.append((module, "import"))
        return found

    patterns = IMPORT_PATTERNS.get(ext, [])
    for pat in patterns:
        for m in re.finditer(pat, text):
            raw = m.group(1).strip()
            if not raw:
                continue
            if ext == ".go":
                # go 多行 import 块，按行拆
                for line in raw.splitlines():
                    line = line.strip().strip('"')
                    if line and not line.startswith("//"):
                        found.append((line.split("/")[0], "import"))
            else:
                found.append((raw, "import"))
    # 去重
    seen = set()
    result = []
    for t, r in found:
        if t not in seen:
            seen.add(t)
            result.append((t, r))
    return result


def verify_candidate(py_file, target_symbol):
    """小虫子验证：文件里是否真的引用了目标。多语言用文本证据检查。"""
    try:
        with open(py_file, "r", encoding="utf-8", errors="ignore") as f:
            text = f.read()
    except Exception:
        return False
    ext = os.path.splitext(py_file)[1].lower()
    if ext == ".py":
        try:
            tree = ast.parse(text)
        except Exception:
            return False
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == target_symbol or alias.name.startswith(target_symbol + "."):
                        return True
            elif isinstance(node, ast.ImportFrom):
                if node.module == target_symbol:
                    return True
            elif isinstance(node, ast.Attribute):
                if isinstance(node.value, ast.Name) and node.value.id == target_symbol:
                    return True
        return False
    # 非 Python：文本证据（目标模块名/符号名出现在文件里）
    key = target_symbol.split(".")[-1]
    return bool(re.search(r"\b" + re.escape(key) + r"\b", text))


def extract_symbols(path):
    """提取文件里定义的函数/类名（多语言，语义搜索的原料）"""
    syms = []
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            text = f.read()
    except Exception:
        return syms
    if path.endswith(".py"):
        try:
            tree = ast.parse(text)
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    syms.append(node.name)
        except Exception:
            pass
    else:
        syms += re.findall(r"\bfunction\s+(\w+)", text)
        syms += re.findall(r"\bclass\s+(\w+)", text)
        syms += re.findall(r"\bdef\s+(\w+)", text)
        syms += re.findall(r"\b(?:public|private|protected)?\s*(?:static\s+)?\w[\w<>, \[\]]*\s+(\w+)\s*\(", text)
    return sorted(set(syms))


# ========== 语义搜索（零依赖轻量版）==========
def _ngrams(text, n=3):
    text = text.lower()
    return [text[i:i + n] for i in range(max(len(text) - n + 1, 0))]


def _cosine(a, b):
    """字符 n-gram 余弦相似度 0~1"""
    ca, cb = Counter(_ngrams(a)), Counter(_ngrams(b))
    if not ca or not cb:
        return 0.0
    inter = sum((ca & cb).values())
    if not inter:
        return 0.0
    return inter / (sum(ca.values()) * sum(cb.values())) ** 0.5


class SymbolIndex:
    """符号索引：文件 → 符号表（语义搜索的原料）"""

    def __init__(self, tree_files):
        self.index = {}  # 符号 → [文件...]
        for f in tree_files:
            for s in extract_symbols(f):
                self.index.setdefault(s, []).append(f)

    def find_symbol(self, symbol):
        return self.index.get(symbol, [])

    def all_symbols(self):
        return sorted(self.index.keys())


def semantic_search(query, tree_files, symbol_index, top=5):
    """
    两级搜索：
    1. 精确符号命中（symbol_index）
    2. n-gram 模糊相似度（对文件路径+符号表算相似度）
    """
    results = []
    # 一级：符号精确命中
    hit_files = symbol_index.find_symbol(query)
    for f in hit_files:
        results.append((f, 1.0, f"符号精确命中: {query}"))
    # 二级：模糊匹配（符号相似）
    fuzzy = []
    for sym, files in symbol_index.index.items():
        score = _cosine(query, sym)
        if score >= 0.4:
            for f in files:
                fuzzy.append((f, score, f"符号相似: {sym}"))
    # 三级：文件内容摘要模糊
    for f in tree_files:
        try:
            with open(f, "r", encoding="utf-8", errors="ignore") as fh:
                head = fh.read(2000)
        except Exception:
            head = ""
        score = _cosine(query, os.path.basename(f)) * 0.5 + _cosine(query, head) * 0.5
        if score >= 0.3:
            fuzzy.append((f, score, "内容模糊匹配"))
    fuzzy.sort(key=lambda x: -x[1])
    seen = set()
    for f, s, why in fuzzy:
        if f not in seen:
            seen.add(f)
            results.append((f, round(s, 2), why))
    # 按文件去重，保留最高分
    best = {}
    for f, s, why in results:
        if f not in best or s > best[f][0]:
            best[f] = (s, why)
    final = sorted([(f, s, why) for f, (s, why) in best.items()], key=lambda x: -x[1])
    return final[:top]


# ========== 基因库 ==========
class GeneBank:
    def __init__(self, root):
        self.root = root
        self.store_dir = os.path.join(root, ".tree_farm")
        self.gene_file = os.path.join(self.store_dir, GENE_FILE)
        self.mtime_file = os.path.join(self.store_dir, MTIME_FILE)
        self.genes = []
        self.mtime_index = {}
        self._seen = set()  # (source, target) 哈希索引，O(1) 查重（几千文件时防 O(n²) 卡死）
        self.load()

    def load(self):
        if os.path.isfile(self.gene_file):
            with open(self.gene_file, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict):  # v2 格式：{"schema_version": N, "genes": [...]}
                raw = raw.get("genes", [])
            self.genes = [normalize_gene(g) for g in raw]  # 旧基因自动升级
            self._seen = {(g["source"], g["target"]) for g in self.genes}
        if os.path.isfile(self.mtime_file):
            with open(self.mtime_file, "r", encoding="utf-8") as f:
                self.mtime_index = json.load(f)

    def save(self):
        os.makedirs(self.store_dir, exist_ok=True)
        with open(self.gene_file, "w", encoding="utf-8") as f:
            json.dump({"schema_version": SCHEMA_VERSION, "genes": self.genes},
                      f, ensure_ascii=False, indent=2)
        with open(self.mtime_file, "w", encoding="utf-8") as f:
            json.dump(self.mtime_index, f, ensure_ascii=False, indent=2)

    def add(self, g):
        key = (g["source"], g["target"])
        if key in self._seen:
            return False
        self._seen.add(key)
        self.genes.append(normalize_gene(g))
        return True

    def eat(self, source, target):
        self._seen.discard((source, target))
        before = len(self.genes)
        self.genes = [g for g in self.genes
                      if not (g["source"] == source and g["target"] == target)]
        return len(self.genes) < before

    def fresh_scan(self, tree_files):
        eaten = 0
        for f in tree_files:
            if not os.path.isfile(f):
                continue
            st = os.stat(f)
            key = [st.st_mtime, st.st_size]
            if self.mtime_index.get(f) != key:
                for g in [g for g in self.genes if g["source"] == f]:
                    if not verify_candidate(f, g["target"]):
                        self.eat(f, g["target"])
                        eaten += 1
                self.mtime_index[f] = key
        return eaten

    def stats(self):
        return {
            "genes": len(self.genes),
            "strong": len([g for g in self.genes if g["kind"] == "strong"]),
            "weak": len([g for g in self.genes if g["kind"] == "weak"]),
        }


class WeedIndex:
    def __init__(self):
        self.entries = []

    def build(self, weed_files):
        self.entries = []
        for f in weed_files:
            try:
                with open(f, "r", encoding="utf-8", errors="ignore") as fh:
                    first_lines = [fh.readline().strip() for _ in range(3)]
                summary = " | ".join(x for x in first_lines if x)
                if len(summary) > 60:
                    summary = summary[:60] + "..."
            except Exception:
                summary = "(无法读取)"
            self.entries.append({"path": f, "size": os.path.getsize(f), "summary": summary})

    def report(self):
        total = sum(e["size"] for e in self.entries)
        lines = [f"杂草 {len(self.entries)} 个文件，合计 {total} 字节（只存索引，未读全文）"]
        for e in self.entries[:20]:
            lines.append(f"  {e['path']}  [{e['size']}B]  {e['summary']}")
        if len(self.entries) > 20:
            lines.append(f"  ... 还有 {len(self.entries) - 20} 个")
        return "\n".join(lines)


class SmallTree:
    def __init__(self, out_index, inn_index, ratio=SMALL_TREE_RATIO):
        self.out_index = out_index
        self.inn_index = inn_index
        self.ratio = ratio

    def active(self, tree_count):
        return len(self.inn_index) + len(self.out_index) > tree_count * self.ratio

    def related(self, py_file):
        """out = 它引用别人（正向，重构看这个）；inn = 别人引用它（反向，修 bug 看这个）
        用预构建索引，O(1) 查询——大项目必备（9187 文件 × 5.7 万基因 = 5 亿次循环会卡死）"""
        return self.out_index.get(py_file, []), self.inn_index.get(py_file, [])


class TrashBin:
    def __init__(self, root, fail_limit=TRASH_FAIL_LIMIT):
        self.root = root
        self.store_dir = os.path.join(root, ".tree_farm")
        self.trash_file = os.path.join(self.store_dir, "trash.json")
        self.fail_limit = fail_limit
        self.records = {}
        self.load()

    def load(self):
        if os.path.isfile(self.trash_file):
            with open(self.trash_file, "r", encoding="utf-8") as f:
                self.records = json.load(f)

    def save(self):
        os.makedirs(self.store_dir, exist_ok=True)
        with open(self.trash_file, "w", encoding="utf-8") as f:
            json.dump(self.records, f, ensure_ascii=False, indent=2)

    def report_failure(self, py_file, reason):
        r = self.records.setdefault(py_file, {"fails": 0, "reasons": []})
        r["fails"] += 1
        r["reasons"].append(reason)
        self.save()
        return r["fails"] >= self.fail_limit

    def trash(self):
        return [f for f, r in self.records.items() if r["fails"] >= self.fail_limit]

    def is_empty(self):
        return len(self.trash()) == 0

    def report(self):
        if self.is_empty():
            return "垃圾箱: 空 ✅"
        lines = [f"垃圾箱: {len(self.trash())} 个文件未处理 ⚠️（不清空 = 任务未完成）"]
        for f in self.trash():
            r = self.records[f]
            lines.append(f"  {f}  失败 {r['fails']} 次: {', '.join(r['reasons'])}")
        return "\n".join(lines)


class Session:
    def __init__(self, root):
        self.store_dir = os.path.join(root, ".tree_farm")
        self.session_file = os.path.join(self.store_dir, SESSION_FILE)
        self.data = {"round": 1, "birds_this_round": 0, "weak_pending": {},
                     "llm_enabled": True}  # LLM 默认开启；用户说「关闭 LLM」→ False
        self.load()

    def load(self):
        if os.path.isfile(self.session_file):
            with open(self.session_file, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            loaded.setdefault("llm_enabled", True)  # 旧会话兼容
            self.data = loaded

    def save(self):
        os.makedirs(self.store_dir, exist_ok=True)
        with open(self.session_file, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)

    def set_llm(self, on):
        self.data["llm_enabled"] = on
        self.save()

    def next_round(self):
        self.data["round"] += 1
        self.data["birds_this_round"] = 0
        self.save()

    def add_bird(self):
        self.data["birds_this_round"] += 1
        self.save()

    def converged(self):
        return self.data["birds_this_round"] <= CONVERGE_LIMIT


# ========== LLM 客户端（OpenAI 兼容，纯标准库，自动识别用户的 API）==========
# 小白模式：啥也不用配。引擎会自己按顺序找：
#   1. 显式配置：项目目录 .treefarm.json 或 ~/.treefarm.json
#   2. 环境变量：TREEFARM_API_KEY（最强指定），然后自动识别各家常见 key
# 找到哪个用哪个。找不到就 dry-run（预览请求，不烧钱）。
PROVIDERS = [
    # (环境变量, base_url, 默认模型)   —— TREEFARM_* 最强，其次各家通用 key
    ("TREEFARM_API_KEY", None, None),          # 显式指定，url/model 可用 TREEFARM_API_URL/MODEL 覆盖
    ("OPENAI_API_KEY", "https://api.openai.com/v1", "gpt-4o-mini"),
    ("DASHSCOPE_API_KEY", "https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen-plus"),
    ("DEEPSEEK_API_KEY", "https://api.deepseek.com/v1", "deepseek-chat"),
    ("MOONSHOT_API_KEY", "https://api.moonshot.cn/v1", "moonshot-v1-8k"),
    ("ARK_API_KEY", "https://ark.cn-beijing.volces.com/api/v3", "doubao-pro-32k"),
]


def _load_config_file():
    """读取配置文件（项目级优先，用户级兜底）。返回 dict 或 None"""
    for path in (".treefarm.json",
                 os.path.join(os.path.expanduser("~"), ".treefarm.json")):
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict) and data.get("api_key"):
                    return data
            except Exception:
                pass
    return None


class LLMClient:
    """引擎的「电话」：自动找卡插上，把问题打包发给大模型，拿回 JSON 回答。零依赖 urllib。"""

    def __init__(self):
        self.api_key = ""
        self.base_url = ""
        self.model = ""
        self.source = "未找到"
        self.detect()

    def detect(self):
        """自动识别：配置文件 → 环境变量。找到就赋值并记来源"""
        # 1. 配置文件
        cfg = _load_config_file()
        if cfg:
            self.api_key = cfg.get("api_key", "")
            self.base_url = cfg.get("base_url", "https://api.deepseek.com/v1")
            self.model = cfg.get("model", "deepseek-chat")
            self.source = "配置文件 " + (".treefarm.json" if os.path.isfile(".treefarm.json")
                                         else os.path.expanduser("~/.treefarm.json"))
            return
        # 2. 环境变量
        for env, url, model in PROVIDERS:
            key = os.environ.get(env)
            if key:
                self.api_key = key
                if env == "TREEFARM_API_KEY":
                    self.base_url = os.environ.get("TREEFARM_API_URL", "https://api.deepseek.com/v1")
                    self.model = os.environ.get("TREEFARM_MODEL", "deepseek-chat")
                else:
                    self.base_url = url
                    self.model = model
                self.source = f"环境变量 {env}"
                return
        # 3. 没找到
        self.source = "未找到可用 API key（dry-run 模式）"

    def available(self):
        return bool(self.api_key)

    def chat(self, messages):
        import urllib.request
        url = self.base_url.rstrip("/") + "/chat/completions"
        body = json.dumps({
            "model": self.model,
            "messages": messages,
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        }).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + self.api_key,
        })
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            raise RuntimeError(
                f"请求失败（网络不通 或 key 无效）: {e}\n"
                "国内网络建议换 DeepSeek/通义：在项目目录写 .treefarm.json 指定\n"
                "（格式: {\"api_key\":\"sk-...\",\"base_url\":\"https://api.deepseek.com/v1\","
                "\"model\":\"deepseek-chat\"}）")
        return data["choices"][0]["message"]["content"]


class TreeFarm:
    def __init__(self, root):
        self.root = root
        self.scanned = None
        self.bank = None
        self.weed_index = None
        self.small_tree = None
        self.trash = TrashBin(root)
        self.session = Session(root)
        self.symbol_index = None
        self.module_map = {}

    def plant(self):
        self.scanned = scan(self.root)
        tree_files = self.scanned["tree"]

        self.weed_index = WeedIndex()
        self.weed_index.build(self.scanned["weed"])

        self.bank = GeneBank(self.root)
        is_new = len(self.bank.genes) == 0

        for f in tree_files:
            mod = f.replace(self.root + os.sep, "").replace(os.sep, ".")[:-3]
            self.module_map[mod] = f
            if f.endswith("__init__.py"):
                pkg = mod[: -len(".__init__")]
                self.module_map.setdefault(pkg, f)

        added = 0
        for f in tree_files:
            for target, rel in extract_genes(f):
                if self.bank.add({"source": f, "target": target, "relation": rel,
                                  "kind": "strong", "confidence": 1.0,
                                  "verified": True, "ts": time.time()}):
                    added += 1

        eaten = 0 if is_new else self.bank.fresh_scan(tree_files)
        self.bank.save()
        # 预构建双向索引（大项目防 O(n×m) 卡死）
        out_index, inn_index = {}, {}
        for g in self.bank.genes:
            out_index.setdefault(g["source"], []).append(g)
            tf = self.module_map.get(g["target"])
            if tf:
                inn_index.setdefault(tf, []).append(g)
        self.small_tree = SmallTree(out_index, inn_index)
        self.symbol_index = SymbolIndex(tree_files)
        return is_new, added, eaten

    # ===== 小鸟机制 =====
    def bird(self, source, target, is_weak=False):
        self.session.add_bird()
        source = os.path.abspath(source)
        if is_weak:
            key = f"{source}|{target}"
            pending = self.session.data["weak_pending"]
            pending[key] = pending.get(key, 0) + 1
            if pending[key] >= WEAK_CONFIRM_LIMIT:
                added = self.bank.add({"source": source, "target": target, "kind": "weak",
                                       "confidence": 0.5, "verified": False, "ts": time.time()})
                self.bank.save()
                self.session.save()
                return {"status": "weak_confirmed", "added": added,
                        "confirms": pending[key], "note": "弱耦合已入库（低优先级）"}
            self.session.save()
            return {"status": "weak_pending", "confirms": pending[key],
                    "note": f"弱耦合待确认（还需 {WEAK_CONFIRM_LIMIT - pending[key]} 个独立分支）"}
        if verify_candidate(source, target):
            added = self.bank.add({"source": source, "target": target, "kind": "strong",
                                   "confidence": 1.0, "verified": True, "ts": time.time()})
            self.bank.save()
            return {"status": "verified_true", "added": added, "note": "小虫子验证为真，基因入库"}
        else:
            self.bank.eat(source, target)
            self.bank.save()
            failed = self.trash.report_failure(source, f"假耦合: {target}")
            msg = "小虫子验证为假，基因被吃掉 🐛"
            if failed:
                msg += f" —— ⚠️ 该文件已失败 {TRASH_FAIL_LIMIT} 次，丢入垃圾箱！"
            return {"status": "eaten_false", "trash_triggered": failed, "note": msg}

    # ===== LLM 自动报小鸟 =====
    def analyze(self, target_file, llm):
        """引擎自己「打电话」：简报+代码 → LLM → 小鸟候选 → 小虫子验证入库"""
        if not self.session.data.get("llm_enabled", True):
            return ("📵 LLM 功能已关闭（用户说过「关闭 LLM」）。"
                    "说「开启 LLM」或运行 --llm on 恢复。")
        if not os.path.isfile(target_file):
            return f"✖ 文件不存在: {target_file}"
        try:
            with open(target_file, "r", encoding="utf-8", errors="ignore") as f:
                code = f.read()
        except Exception:
            code = "(读取失败)"
        out, inn = self.small_tree.related(target_file)
        known = [(g["target"], g["kind"]) for g in out]
        rel = os.path.relpath(target_file, self.root)

        prompt = (
            "你是树场机制里的「小鸟侦察员」。你的任务是找出代码里隐藏的可疑耦合。\n"
            f"正在分析文件: {rel}\n"
            f"它已知的依赖（基因库，已验证）: {known}\n"
            "请阅读下面的代码，找出【基因库还没有的】可疑耦合，包括隐式耦合\n"
            "（比如：函数返回值格式被外部依赖、共享的常量/约定、时序依赖、数据格式耦合）。\n"
            "只报有代码证据的，不确定的别报。\n"
            "输出 JSON（严格格式）:\n"
            '{"birds": [{"target": "模块名或符号", "relation": "import|call|inherit|weak", "evidence": "一句话证据"}]}\n'
            "没有发现就返回 {\"birds\": []}\n\n"
            f"=== 代码开始 ===\n{code[:6000]}\n=== 代码结束 ==="
        )

        if not llm.available():
            # dry-run：没找到 key，展示引擎会发什么
            return ("⚠️ " + llm.source + "。这是 dry-run 预览——引擎会自动找卡，找到就发真请求：\n"
                    "自动识别顺序: 项目/.treefarm.json → ~/.treefarm.json → 环境变量\n"
                    "（支持 TREEFARM_ / OPENAI / DASHSCOPE / DEEPSEEK / MOONSHOT / ARK）\n"
                    "示例: echo '{\"api_key\":\"sk-xxx\",\"base_url\":\"https://api.deepseek.com/v1\","
                    "\"model\":\"deepseek-chat\"}' > .treefarm.json\n\n"
                    f"请求模型: {llm.model or '未指定'}\n请求内容:\n{prompt}")

        try:
            raw = llm.chat([{"role": "user", "content": prompt}])
            data = json.loads(raw)
        except Exception as e:
            return f"✖ LLM 调用/解析失败: {e}\n原始返回: {raw if 'raw' in dir() else '无'}"

        birds = data.get("birds", [])
        if not birds:
            return "🐦 小鸟侦察员没发现新耦合（或都已在基因库里）。"
        lines = [f"🐦 小鸟侦察员发现 {len(birds)} 个候选，正在交给小虫子验证..."]
        for b in birds:
            tgt = b.get("target", "")
            relation = b.get("relation", "weak")
            is_weak = relation == "weak"
            result = self.bird(target_file, tgt, is_weak=is_weak)
            lines.append(f"  {tgt} [{relation}] 证据: {b.get('evidence', '')} → {result['status']}")
        return "\n".join(lines)

    # ===== 简报 =====
    def brief(self):
        s = self.scanned
        b = self.bank.stats()
        lines = ["=" * 52, "🌳🌳 树场简报（AI 请先读这个，别全读项目）🌳🌳", "=" * 52]
        lines.append(f"会话: 第 {self.session.data['round']} 轮 | 本轮已报小鸟 "
                     f"{self.session.data['birds_this_round']} 只 | 收敛阈值 ≤{CONVERGE_LIMIT} 只")
        lines.append(f"基因库: {b['genes']} 条（强 {b['strong']} / 弱 {b['weak']}）| "
                     f"schema v{SCHEMA_VERSION} | "
                     f"小树: {'激活 ✅' if self.small_tree.active(len(s['tree'])) else '未激活'}")
        lines.append("")
        lines.append("【大树清单 · 每个文件的相关基因（小树视角）】")
        for f in s["tree"]:
            size = os.path.getsize(f) if os.path.isfile(f) else 0
            rel = os.path.relpath(f, self.root)
            if f.endswith(".py"):
                out, inn = self.small_tree.related(f)
                lines.append(f"  {rel} [{size}B]")
                lines.append(f"      → 引用: {[g['target'] for g in out] or '(无)'}")
                lines.append(f"      ← 被引用: {[os.path.relpath(g['source'], self.root) for g in inn] or '(无)'}")
            else:
                lines.append(f"  {rel} [{size}B]  (非 Python，符号: {extract_symbols(f)[:6] or '无'})")
        lines.append("")
        lines.append("【杂草索引 · 不读全文，命中才展开】")
        lines.append(self.weed_index.report())
        lines.append("")
        lines.append(self.trash.report())
        lines.append("")
        lines.append("【下一步】")
        lines.append("  1. 挑重点文件展开阅读 → 发现可疑耦合就报小鸟")
        lines.append("  2. 强耦合: --bird | 弱耦合: --bird-weak | 自动: --analyze <文件>")
        lines.append("  3. 找相关代码: --search <关键词>")
        lines.append("  4. 每轮结束 --converge；垃圾箱 --fix 必须清空")
        return "\n".join(lines)

    def fix_protocol(self, target_file):
        if target_file not in self.trash.trash():
            return f"{target_file} 不在垃圾箱里，无需修复。"
        r = self.trash.records[target_file]
        lines = [f"🔧 修复协议（{target_file}）",
                 f"  失败次数: {r['fails']} | 原因: {', '.join(r['reasons'])}",
                 "  修复步骤: 1. 新开一场思维树深度阅读 2. 定位为什么 AI 总在这出错",
                 "            3. 修正代码 4. 重新整体排查",
                 "  ⚠️ 需用户同意后才能执行修复（默认逐处确认）"]
        return "\n".join(lines)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    root = os.path.abspath(sys.argv[1])
    farm = TreeFarm(root)
    is_new, added, eaten = farm.plant()

    if len(sys.argv) == 2:
        print(f"建库={'是' if is_new else '否'} | 新增基因 {added} | 腐肉啃掉 {eaten}")
        b = farm.bank.stats()
        print(f"大树 {len(farm.scanned['tree'])} | 杂草 {len(farm.scanned['weed'])} | "
              f"基因库 {b['genes']} 条（schema v{SCHEMA_VERSION}）")
        print("想看细节: --brief  |  报小鸟: --bird  |  自动: --analyze  |  搜索: --search")
        return

    cmd = sys.argv[2]
    if cmd == "--brief":
        print(farm.brief())
    elif cmd == "--bird" and len(sys.argv) >= 5:
        print(farm.bird(sys.argv[3], sys.argv[4]))
    elif cmd == "--bird-weak" and len(sys.argv) >= 5:
        print(farm.bird(sys.argv[3], sys.argv[4], is_weak=True))
    elif cmd == "--analyze" and len(sys.argv) >= 4:
        print(farm.analyze(os.path.abspath(sys.argv[3]), LLMClient()))
    elif cmd == "--llm" and len(sys.argv) >= 4:
        llm = LLMClient()
        sub = sys.argv[3]
        if sub == "off":
            farm.session.set_llm(False)
            print("📵 LLM 已关闭。说「开启 LLM」或 --llm on 恢复。")
        elif sub == "on":
            farm.session.set_llm(True)
            print("📞 LLM 已开启（默认开启）。")
        elif sub == "status":
            state = "✅ 开启" if farm.session.data.get("llm_enabled", True) else "📵 已关闭"
            card = ("📞 LLM 状态: " + state + "\n"
                    f"   卡: {llm.source}\n"
                    f"   模型: {llm.model or '未指定'}\n"
                    f"   接口: {llm.base_url or '未指定'}")
            print(card)
    elif cmd == "--search" and len(sys.argv) >= 4:
        hits = semantic_search(sys.argv[3], farm.scanned["tree"], farm.symbol_index)
        if not hits:
            print(f"🔍 没找到与「{sys.argv[3]}」相关的文件")
        else:
            print(f"🔍 「{sys.argv[3]}」的相关文件（按相似度排序）：")
            for f, score, why in hits:
                print(f"  {score:.2f}  {os.path.relpath(f, root)}  ({why})")
    elif cmd == "--symbols" and len(sys.argv) >= 4:
        syms = extract_symbols(os.path.abspath(sys.argv[3]))
        print(f"符号（函数/类）: {syms or '无'}")
    elif cmd == "--trash":
        print(farm.trash.report())
    elif cmd == "--next-round":
        farm.session.next_round()
        print(f"第 {farm.session.data['round']} 轮开始")
    elif cmd == "--converge":
        print(f"本轮新增小鸟 {farm.session.data['birds_this_round']} 只 | "
              f"{'✅ 已收敛，排查结束' if farm.session.converged() else '⏳ 未收敛，继续排查'}")
        if not farm.trash.is_empty():
            print("⚠️ 但垃圾箱未清空 —— 任务仍未完成！")
    elif cmd == "--fix" and len(sys.argv) >= 4:
        print(farm.fix_protocol(os.path.abspath(sys.argv[3])))
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
