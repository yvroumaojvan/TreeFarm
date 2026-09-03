# TreeFarm API 文档

> 树场机制的 Python API。零依赖（纯标准库），`import tree_farm as tf` 全量可用。
> 本文档覆盖 v4.7：入口聚合导出 + 核心类 + 分析函数（含 v4.7 Grader 化 spec 模块 / 契约检测 / v3.7 新增 Rust/代码异味/REPL）。

## 导入方式

```python
import tree_farm as tf          # 聚合入口（等价旧单文件 API）
from treefarm import TreeFarm   # 或直接导入包内类
from treefarm.analysis import detect_code_smells   # 分析函数可单独导入
```

---

## 核心类

### TreeFarm

树场主类，管理整个树场的生命周期。

```python
farm = TreeFarm(root: str)
farm.plant(progress: Optional[Callable] = None) -> Tuple[bool, Dict]
    # 建库/增量更新。返回 (是否新库, {"added":..., "eaten":..., "renamed":...})
farm.brief(compact: bool = False) -> str           # AI 工作简报
farm.brief_data() -> Dict                          # 结构化简报（--json 用）
farm.bird(source, target, is_weak=False) -> Dict   # 报小鸟（小虫子验证）
farm.analyze(target_file, llm) -> str              # LLM 自动找小鸟
farm.fix_protocol(target_file) -> str              # 修复协议
farm.trash.report() -> str                         # 垃圾箱状态
farm.session.next_round() / converged()            # 轮次 / 收敛检查
```

**分析类方法**（内部调 analysis.py，均返回格式化字符串）：

| 方法 | 命令 | 说明 |
|------|------|------|
| `dead_code()` | `--dead-code` | 死代码检测 |
| `circular_deps()` | `--circular-deps` | 循环依赖 |
| `impact(file, symbol=None, depth=3)` | `--impact` | 影响分析 |
| `complexity(target=None)` | `--complexity` | 代码复杂度 |
| `architecture()` | `--architecture` | 架构分层 |
| `debt()` | `--debt` | 技术债务（四维打分） |
| `smells()` | `--smells` | 代码异味（v3.7） |
| `call_graph(target=None)` | `--graph` | 调用图（Mermaid） |
| `call_graph_data(target=None)` | `--graph --json` | 调用图结构化数据 |
| `duplicates(threshold=0.6, top=10)` | `--duplicates` | 重复代码 |
| `duplicates_func(threshold=0.6, top=10)` | `--duplicates-func` | 函数级查重 |

**核心属性**：`root` `scanned`（`{"tree": [...], "weed": [...]}`）`bank`（GeneBank）
`weed_index` `small_tree` `trash` `session` `symbol_index` `content_index` `module_map`。

### GeneBank（storage.py）

SQLite 基因库（WAL 模式，原子事务）。

```python
bank = GeneBank(root)
bank.add(gene: Dict) -> bool            # 入库（stable_id 去重）
bank.eat(source, target)                # 吃掉假耦合
bank.rename_source(old, new) -> int     # 重命名迁移
bank.drop_file(path, mods) -> int       # 级联删除（source + target + 模块名）
bank.all_genes() -> List[Dict]          # 全部基因
bank.genes_of_source(f) -> List[Dict]
bank.sources_referencing(mod) -> List[str]
bank.stats() -> Dict                    # 基因统计（强/弱/函数级）
bank.mtime_update(f, mtime, size, fp)   # 增量指纹
bank.save_search_index / load_search_index
bank.db_info() -> Dict
bank.close()
```

基因格式（schema v4）：

```python
{"source": 文件路径, "target": 模块名或文件路径, "symbol": 符号名,
 "relation": "import|call|inherit|weak", "kind": "strong|weak",
 "confidence": float, "verified": bool, "ts": float}
```

`call`/`inherit` 基因：target=文件路径、symbol=符号名（跨文件验证后入库）。

### FileCache（common.py）

文件内容/AST/符号缓存（内存有界，自动淘汰）：

```python
cache.text(path) -> str
cache.get_ast(path) -> Optional[ast.Module]
cache.symbols(path) -> List[str]        # 多语言符号（Python AST / JS / Java / Go / Rust）
```

### SymbolIndex / SmallTree / TrashBin / Session / WeedIndex

- `SymbolIndex(symbol_map)`：符号 → 文件索引，`query(name)` 查找
- `SmallTree(out_index, inn_index)`：小树视角，`related(f) -> (out, inn)` 双向耦合
- `TrashBin(bank)`：垃圾箱熔断器，`record(source, failed, reason)` / `report()` / `trash()`
- `Session(bank)`：轮次状态，`data` / `next_round()` / `converged()` / `set_llm()`
- `WeedIndex()`：杂草摘要，`build(weed_files)` / `report()`

---

## 分析函数（analysis.py）

v3.6 拆分后全部可独立导入：

```python
from treefarm.analysis import (
    detect_dead_code, detect_circular_dependencies, impact_analysis,
    calculate_complexity, calculate_complexity_any, detect_architecture_layers,
    detect_code_smells,                # v3.7
    generate_call_graph, generate_call_graph_data,   # v3.7 迁出
    detect_duplicates, detect_duplicate_func_pairs,  # v3.7 迁出
    collect_function_grams, calculate_debt,          # v3.7 迁出
)
```

### detect_code_smells(tree_files, root=None, long_func_lines=50, max_params=5,
                       max_nesting=4, big_class_lines=300) -> Dict

代码异味检测（v3.7 新增）。返回：

```python
{"smells": [
    {"type": "long_function|long_parameter_list|deep_nesting|"
             "duplicate_condition|magic_number|god_class",
     "severity": "high|medium|low",
     "file": str, "line": int, "name": str,
     "detail": str, "suggestion": str},
  ],
 "counts": {type: 数量},
 "total": int}
```

按严重程度排序（high > medium > low）。Python 走 AST；JS/TS/Java/Go/Rust 走启发式。

### calculate_debt(tree_files, bank, module_map, root=None) -> Dict

技术债务四维打分（死代码 / 高复杂度 / 高重复 / 循环依赖），0~100 越低越好：

```python
{"total": int, "grade": "A|B|C|D", "emoji": str,
 "dimensions": [{"label": str, "score": int} x4],
 "suggestions": [str]}
```

### generate_call_graph(bank, module_map, root, small_tree=None, target_file=None) -> str

Mermaid 调用图文本（含标题头）。`small_tree` 仅在文件模式需要。

### detect_duplicates(tree_files, threshold=0.6) -> (pairs, files_count)

内容 n-gram Jaccard 查重，返回 `([(fa, fb, score)], 参与比较文件数)`。

### detect_duplicate_func_pairs(tree_files, threshold=0.6) -> [(fa, fa_line, fb, fb_line, score)]

函数级查重（分桶优化 O(n·k)），供 `--duplicates-func` 和 `--debt` 共用。

---

## 解析器（parser.py）

多语言零依赖解析器：

```python
from treefarm.parser import (
    extract_genes,            # import 级基因（全语言）
    extract_call_graph,       # Python AST 函数级（calls, inherits）
    extract_js_call_graph,    # JS/TS 函数级（defs, calls）
    extract_java_call_graph,  # Java 函数级（calls, inherits）
    extract_go_call_graph,    # Go 函数级（calls, inherits）
    extract_rust_call_graph,  # Rust 函数级（calls, inherits）  ← v3.7
    extract_symbols,          # 多语言符号提取
    verify_candidate,         # 小虫子候选验证
)
```

**Rust 解析器（v3.7）**：`extract_rust_call_graph(rs_file) -> (calls, inherits)`
- 函数/方法定义（fn，含泛型 `<T>`、生命周期 `<'a>`、`unsafe fn`、`async fn`、`where` 子句）
- trait 继承 → inherit 基因（`trait Foo: Bar + Baz`；标准库 trait 自动过滤）
- 宏调用（`name!`）排除；噪音剔除（注释/字符串/raw string `r#"..."#`/字符/生命周期/属性）
- `_rust_defs` 供死代码/复杂度/查重共用

---

## 配置 API（config.py）

```python
from treefarm.config import load_config, LLMClient

cfg = load_config(root)        # 项目 → 用户主目录 → 环境变量 三级查找
llm = LLMClient(root)          # LLM 客户端
llm.available() -> bool        # 是否配置了可用 API
llm.chat(messages) -> str      # 发请求（TREEFARM_ / OPENAI / DASHSCOPE / DEEPSEEK / MOONSHOT / ARK）
```

---

## 版本

| 版本 | 关键变化 |
|------|---------|
| v3.7 | Rust 函数级分析 + `--smells` 代码异味 + `--repl` 交互模式 + core 拆分 |
| v3.6 | 单文件拆分为 treefarm/ 多模块包；影响/架构跨语言确认 |
| v3.5 | Go 函数级分析 + 死代码/复杂度 4 语言 + 函数级查重 + `--debt` |
| v3.4 | Java 函数级分析 + JS 解析器加固 |
| v3.3 | JS/TS 函数级分析 + `--graph` + `--duplicates` |
