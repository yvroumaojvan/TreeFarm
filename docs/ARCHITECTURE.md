# 🏗️ TreeFarm 架构设计

> 模块划分、依赖关系、核心算法说明、扩展指南。
> v3.6 完成单文件 → 多模块拆分；v3.7 完成 core.py 二次拆分（调用图/查重/债务迁入 analysis）；v4.7 新增 spec.py（功能画像 + Grader 综合评分/趋势），TreeFarm grader 化。

---

## 1. 总体架构

```
┌─────────────────────────────────────────────────┐
│                    tree_farm.py                  │  ← 入口聚合层
│             （import 全符号 + CLI main）          │
└──────────────────────┬──────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────┐
│                     cli.py                       │  ← 命令分发 / REPL
│        _dispatch() / _repl() / main()            │
└──────────────────────┬──────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────┐
│                     core.py                      │  ← TreeFarm 主类
│   plant(建库/增量) · bird(小鸟) · brief(简报)    │
│   fix(修复协议) · 分析薄包装方法                  │
└───────┬──────────────┬──────────────┬───────────┘
        │              │              │
┌───────▼─────┐ ┌──────▼──────┐ ┌─────▼──────────┐
│  parser.py  │ │ analysis.py │ │  storage.py    │
│ 多语言解析器 │ │ 全部分析算法 │ │  SQLite 存储   │
│ 基因/符号提取│ │ 异味/查重/  │ │  GeneBank 等   │
│             │ │ 债务/调用图 │ │                │
└───────┬─────┘ └──────┬──────┘ └─────┬──────────┘
        │              │              │
┌───────▼──────────────▼──────────────▼──────────┐
│                 common.py                      │
│  常量 · 工具 · FileCache · 语义搜索 · 指纹增量  │
└────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────┐
│                 config.py                       │
│       配置解析（.treefarm.toml/json）+ LLMClient │
└─────────────────────────────────────────────────┘
```

**依赖链单向**：`common → parser/storage/config → analysis → core → cli`。
core 只做薄包装（组装 + 输出格式），算法全在 analysis。

---

## 2. 模块职责

### common.py —— 公共底座
- 常量：`CODE_EXTS`（含 `.rs`）、`SKIP_DIRS`、各语言关键词（`JS/JAVA/GO/RUST_KEYWORDS`）
- `FileCache`：文件内容/AST/符号三缓存（内存有界，LRU 淘汰），多语言符号提取
- 语义搜索：符号精确 + 归一化模糊 + 内容 n-gram 三级召回
- 指纹增量：`file_fingerprint` + `scan_changes`（mtime/size/指纹三级判断）

### parser.py —— 多语言解析器
- `extract_genes`：import 级基因（Python AST；其余语言正则，含 `.rs` use 语句）
- 函数级调用分析：
  - Python：AST 精确（`extract_call_graph`）
  - JS/TS（v3.3）/ Java（v3.4）/ Go（v3.5）/ **Rust（v3.7）**：零依赖轻量解析器
- 多语言定义提取：`_js_defs / _java_defs / _go_defs / _rust_defs`
  （死代码 / 复杂度 / 查重 / 异味共用）
- `_strip_*_noise`：各语言注释/字符串噪音剔除（Rust 含生命周期/raw string 处理）

### storage.py —— 持久化
- `GeneBank`：SQLite 基因库（WAL、原子事务、批量写入）
- `TrashBin`：垃圾箱熔断器（跨进程持久化 trash.json 于 SQLite）
- `Session`：轮次 + 收敛
- `WeedIndex` / `SmallTree`：杂草摘要 / 双向耦合索引

### analysis.py —— 分析算法（v3.7 二次拆分后）
- 死代码 / 循环依赖 / 影响分析 / 复杂度 / 架构分层
- **代码异味**（v3.7）：`detect_code_smells`（6 类异味，Python AST + 其他语言启发式）
- **调用图**（v3.7 迁入）：`generate_call_graph(_data)` + Mermaid 渲染
- **查重**（v3.7 迁入）：`detect_duplicates`（文件级）/ `detect_duplicate_func_pairs`
  （函数级，分桶优化）
- **技术债务**（v3.7 迁入）：`calculate_debt`（四维打分）

### core.py —— 树场机制（v3.7 瘦身后 734 行）
- 生命周期：`plant`（建库/增量/重命名迁移/级联回收）
- 小鸟机制：`bird`（强/弱耦合，小虫子静态验证）
- 简报：`brief` / `brief_data`
- 分析薄包装：`dead_code / smells / debt / call_graph / duplicates / ...`（一行调 analysis）

### cli.py —— 命令分发
- `_dispatch`：全部 CLI 命令（英文 + 中文等价）
- `_repl`（v3.7）：交互式 REPL（readline 补全 + 历史 + 彩色）

---

## 3. 核心算法

### 3.1 基因提取（耦合建模）

```
文件 → 三层基因：
  import 级：import/use/require 语句 → target=模块名
  函数级：跨文件函数调用 → target=文件路径, symbol=符号名（仅其他文件定义才入库）
  继承级：Python class A(B) / Java extends / Go 嵌入 / Rust trait Foo: Bar
```

跨文件验证：`_add_deep_gene` 查 `symbol_map`（符号 → 文件），目标在**其他文件**
才入库；`verify_candidate` 二次验证降低误报。

### 3.2 增量扫描

```
scan_changes: mtime + size + 内容指纹 三级判断
变更文件 → drop_source → 重新提取 → 批量事务写入
删除文件 → 级联回收（source + target + 模块名引用）
重命名 → 迁移基因 + 引用旧路径的文件强制重扫
```

### 3.3 死代码检测

定义集合 vs 被调用集合的差集。Python 走 AST；JS/Java/Go/Rust 走轻量解析器
（函数/类定义 + 调用点提取 + 实例化/继承使用识别）。入口函数豁免。

### 3.4 代码异味（v3.7）

| 异味 | Python | 其他语言 |
|------|--------|---------|
| 长函数 | AST `end_lineno` | `_func_body_end` 括号配对 |
| 长参数 | AST `args` | 参数括号深度计数 |
| 嵌套过深 | AST 递归深度 | 最大缩进估算（保守阈值） |
| 重复条件 | AST 条件源码段规范化 | `if (...)` 文本规范化 |
| 魔法数字 | AST `Constant`（排除 range 参数） | 正则（排除常见数字） |
| 大类 | AST 行数/方法数 | 类块括号配对 + 类内 defs |

### 3.5 函数级查重

```
按函数体大小分桶（size//64）→ 只比较同桶+相邻桶 → n-gram Jaccard
O(n²) → ~O(n·k)，1000 文件实测 ~1.2s
```

### 3.6 技术债务四维打分

```
死代码比例(≤25) + 高复杂度函数(≤25) + 高度重复函数对(≤25) + 循环依赖环数(≤25)
0-10 A / 11-25 B / 26-45 C / 46+ D
```

---

## 4. 扩展指南

### 新增一门语言支持（如 Ruby）

1. `common.py`：`CODE_EXTS` 加 `.rb`；加 `RUBY_KEYWORDS`
2. `parser.py`：
   - `IMPORT_PATTERNS` 加 `.rb`
   - `_strip_ruby_noise` + `extract_ruby_call_graph` + `_ruby_defs`
3. `analysis.py`：`detect_dead_code` / `calculate_complexity_any` / `_heuristic_smells` 加分支
4. `core.py`：`_build_genes` 加 `.rb` 分支；`_symbols_uncached` 加符号提取
5. 测试：解析 ≥8 个 + 死代码/复杂度各 1-2 个

### 新增分析功能

1. 算法放 `analysis.py`（纯函数，不依赖 TreeFarm 实例，只依赖 bank/module_map/tree_files）
2. core.py 加薄包装方法（格式化输出）
3. cli.py 加命令（英文 + 中文）
4. tree_farm.py `__all__` 加公共符号
5. 测试 ≥5 个

### 新增解析器后别忘了

- `_symbols_uncached`（common.py）：符号提取分支
- `_function_grams_for_file`（analysis.py）：查重/复杂度共用分支
- `_count_complexity`（parser.py）：复杂度关键词分支
- `tree_farm.py` 的 `__all__`
