# TreeFarm API 文档

## 概述

TreeFarm 是树场机制的引擎：轻量级代码理解工具，零依赖（纯 Python 标准库 + Node 可选）。

核心设计：把项目按"大树（代码）/ 杂草（资源文档）"分类，建基因库存耦合关系，
配合小虫子（静态验证）、小鸟（报耦合）、垃圾箱（熔断）等机制，让 AI 用最少的
token 读懂大项目。

## 快速上手

```bash
python3 tree_farm.py <项目>                      # 树场体检（首次自动建库）
python3 tree_farm.py <项目> --brief [--compact]  # AI 工作简报（省 token 核心，先读这个）
python3 tree_farm.py <项目> --search <关键词>    # 语义搜索
python3 tree_farm.py <项目> --dead-code          # 死代码检测
python3 tree_farm.py <项目> --circular-deps      # 循环依赖检测
python3 tree_farm.py <项目> --impact <文件>      # 影响分析
python3 tree_farm.py <项目> --complexity [文件]  # 代码复杂度
python3 tree_farm.py <项目> --architecture       # 架构分层识别
```

中文命令等价：`分析 死代码` / `分析 循环依赖` / `分析 复杂度` / `分析 架构` / `分析 影响 <文件>`。

---

## CLI 命令一览

| 命令 | 说明 |
|------|------|
| `--brief [--compact]` | AI 工作简报（先读它，别全读项目） |
| `--genes <文件>` | 小树视角：某文件的正/反向基因 |
| `--bird <源> <目标>` | 报小鸟（强耦合，小虫子静态验证真伪） |
| `--bird-weak <源> <目标>` | 报小鸟（弱耦合，需 ≥2 独立分支确认） |
| `--analyze <文件>` | LLM 自动找小鸟（自动识别 API，无 key 则 dry-run） |
| `--search <关键词>` | 语义搜索（符号精确 + 归一化模糊 + 内容 n-gram） |
| `--symbols <文件>` | 查看文件里的函数/类符号 |
| `--trash` | 垃圾箱状态（不清空 = 任务未完成） |
| `--next-round` | 进入下一排查轮 |
| `--converge` | 收敛检查（本轮新增小鸟 ≤2 = 结束） |
| `--fix <文件>` | 修复协议（需用户同意后才执行） |
| `--llm on\|off\|status` | LLM 开关（默认开启） |
| `--db` | SQLite 存储状态 |
| `--dead-code` | 💀 死代码检测（v3.1） |
| `--circular-deps` | 🔄 循环依赖检测（v3.1） |
| `--impact <文件> [符号] [深度]` | 💥 影响分析（v3.1） |
| `--complexity [文件]` | 📊 代码复杂度（v3.1） |
| `--architecture` | 🏗️ 架构分层识别（v3.1） |
| `--debug` | 附加任意命令，输出调试日志 |

---

## 核心类

### TreeFarm

树场主类，管理整个树场的生命周期。

#### 方法

##### `__init__(root)`
初始化树场。
- `root` (str): 项目根目录路径

##### `plant()` -> `(is_new, stat)`
扫描项目，构建/增量更新基因库与索引。
- 返回 `is_new` (bool) 是否新建库；`stat` 含 `added`/`eaten`/`renamed` 统计

##### `brief(compact=False)` -> `str`
生成 AI 工作简报（大树清单 + 杂草索引 + 垃圾箱状态 + 下一步）。
- `compact` (bool): 精简模式，省输出 token

##### `bird(source, target, is_weak=False)` -> `dict`
报小鸟（添加耦合关系）。强耦合经小虫子静态验证；弱耦合需双分支确认。
- 返回 `{"status": "verified_true" | "eaten_false" | "weak_pending" | "weak_confirmed", ...}`

##### `analyze(target_file, llm)` -> `str`
让 LLM 自动分析文件、找出可疑耦合（无 key 时 dry-run 预览）。

##### `dead_code()` -> `str`
💀 死代码检测报告（v3.1）。

##### `circular_deps()` -> `str`
🔄 循环依赖检测报告（v3.1）。

##### `impact(target_file, target_symbol=None, max_depth=3)` -> `str`
💥 影响分析报告（v3.1）：改某文件/函数会影响哪些地方。

##### `complexity(target_file=None)` -> `str`
📊 代码复杂度报告（v3.1）：单文件或整个项目。

##### `architecture()` -> `str`
🏗️ 架构分层识别报告（v3.1）。

### GeneBank

基因库：SQLite 持久化（原子事务 / 索引查询 / WAL 并发安全）。旧 JSON 自动迁移。

#### 方法

| 方法 | 说明 |
|------|------|
| `add(gene)` | 添加基因（已存在返回 False） |
| `eat(source, target)` | 删除基因（被小虫子吃掉） |
| `replace_source(source, genes)` | 增量更新：整体替换某文件的基因 |
| `drop_source(source)` | 删除某文件作为依赖方的全部基因 |
| `drop_file(path, module_names)` | 文件删除级联回收（source + target + 模块名） |
| `rename_source(old, new)` | 重命名迁移基因 |
| `sources_referencing(target)` | 谁引用了该 target（重命名/删除后重扫用） |
| `genes_of_source(source)` / `genes_of_target(target)` | 按方向查询基因 |
| `all_genes()` | 全部基因 |
| `stats()` | 基因统计（总量/强/弱/函数级+继承） |
| `db_info()` | SQLite 存储状态 |
| `mtime_snapshot/update/remove` | mtime 索引（增量扫描 + 腐肉检测） |
| `save_search_index/drop/load` | 搜索索引（符号 + n-gram 持久化） |
| `close()` | 关闭数据库连接 |

---

## 工具函数

> v3.6 起拆分为多模块：入口 `tree_farm.py` 聚合导出全部符号（`import tree_farm as tf` 用法不变），
> 实际实现在 `scripts/treefarm/` 包：`common`（常量/工具/缓存/搜索）、`parser`（多语言提取）、
> `storage`（SQLite 存储）、`config`（配置/LLM）、`analysis`（5 大分析）、`core`（TreeFarm 主类）、`cli`（命令分发）。

| 函数 | 模块 | 说明 |
|------|------|------|
| `stable_id(source, target, symbol, relation)` | common | sha1 稳定基因 id（跨会话不变） |
| `normalize_gene(g)` | common | 基因标准化（旧格式自动升级） |
| `classify(path)` | common | 文件分类：weed / tree / other |
| `scan(root)` | common | 扫描项目，返回 {weed, tree, other} |
| `rel_module(root, path)` / `module_to_path(root, module)` | common | 路径 ↔ 模块名 |
| `extract_genes(py_file)` | parser | 提取 import 级基因（多语言） |
| `extract_call_graph(py_file)` | parser | Python 函数级调用 + 类继承提取 |
| `extract_js_call_graph(js_file)` | parser | JS/TS 函数级调用提取（v3.3 零依赖解析器） |
| `extract_java_call_graph(java_file)` | parser | Java 函数级调用 + extends/implements 继承提取（v3.4 零依赖解析器） |
| `extract_go_call_graph(go_file)` | parser | Go 函数级调用 + struct 嵌入继承提取（v3.5 零依赖解析器） |
| `verify_candidate(py_file, target_symbol)` | parser | 小虫子验证：引用是否真实 |
| `extract_symbols(path)` | parser | 提取文件定义的函数/类符号 |
| `semantic_search(query, tree_files, symbol_index, content_index, top)` | common | 三级语义搜索 |
| `detect_dead_code(tree_files)` | analysis | 💀 死代码检测（v3.1，v3.5 支持 Python/JS/Java/Go） |
| `detect_circular_dependencies(bank, module_map)` | analysis | 🔄 循环依赖检测（v3.1） |
| `impact_analysis(bank, module_map, target_file, target_symbol, max_depth)` | analysis | 💥 影响分析（v3.1，v3.6 确认 Python/JS/Java/Go 跨语言生效） |
| `calculate_complexity(py_file)` | analysis | 📊 圈复杂度（v3.1，Python AST） |
| `calculate_complexity_any(path)` | analysis | 📊 圈复杂度（v3.5，Python/JS/Java/Go 通用） |
| `detect_architecture_layers(tree_files, bank, module_map, root)` | analysis | 🏗️ 架构分层（v3.1，v3.6 确认语言无关） |
| `scan_changes(tree_files, bank)` | common | 真增量扫描（新增/修改/删除/重命名） |

---

## LLM 配置（可选，用于 `--analyze`）

环境变量（按优先级）：

```bash
export TREEFARM_API_KEY=sk-xxx          # 最强（可配 URL/模型）
export TREEFARM_API_URL=https://api.deepseek.com/v1
export TREEFARM_MODEL=deepseek-chat
# 或任意 OpenAI 兼容 key：OPENAI / DASHSCOPE / DEEPSEEK / MOONSHOT / ARK
```

或配置文件（项目目录 `.treefarm.json` → 用户主目录 `~/.treefarm.json`）：

```json
{"api_key": "sk-xxx", "base_url": "https://api.deepseek.com/v1", "model": "deepseek-chat"}
```

---

## 基因格式（schema v3）

```python
{
    "id": "g_sha1前12位",      # 稳定哈希（跨会话不变）
    "source": "源文件路径",     # 谁依赖
    "target": "目标",          # import: 模块名 / call·inherit: 目标文件路径
    "symbol": "符号名",        # call/inherit 的符号（import 为空）
    "relation": "import | call | inherit | weak",
    "direction": "",           # 保留字段
    "kind": "strong | weak",
    "confidence": 1.0,
    "verified": True,          # 小虫子是否验证过
    "first_seen": 时间戳,
    "last_verified": 时间戳
}
```

规则：`kind=strong` 必须 `verified=True`；`kind=weak` 必须 `confidence<=0.5` 且双分支确认。

## 存储

- 数据库：`<项目>/.tree_farm/tree_farm.db`（SQLite，WAL 模式）
- 旧版 JSON（gene_bank.json / mtime_index.json / session.json / trash.json）首次运行自动迁移并留 `.bak`
