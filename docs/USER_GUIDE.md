# 🌳 TreeFarm 用户手册

> 树场机制 —— 轻量级零依赖代码分析工具。
> 面向 AI 协作的「省 token 读项目」方案，同时是一套完整的代码健康检查 CLI。

**零依赖**：纯 Python 标准库，`pip install` 都不需要。`python3` 就能跑。

---

## 1. 安装

树场不需要安装。把 `tree-farm/` 目录放到任意位置，进入 `tree-farm/scripts/` 直接用：

```bash
cd tree-farm/scripts
python3 tree_farm.py <项目路径>            # 第一次运行自动建库
```

> 可选：`./install.sh`（仓库根）把它配置成 agent 技能（Claude / Qwen / Cursor 等）。

---

## 2. 快速开始（30 秒上手）

```bash
# 1. 体检 + 建库（首次自动扫描全项目）
python3 tree_farm.py <项目>

# 2. AI 工作简报（让 AI 先读这个，别全读项目 —— 省 token 核心）
python3 tree_farm.py <项目> --brief

# 3. 看一个文件的影响面（改代码前必看）
python3 tree_farm.py <项目> --impact <文件>

# 4. 代码健康检查（一次跑全）
python3 tree_farm.py <项目> --debt          # 技术债务总分
python3 tree_farm.py <项目> --smells        # 代码异味
python3 tree_farm.py <项目> --dead-code     # 死代码

# 5. 交互式排查（v3.7 新增）
python3 tree_farm.py <项目> --repl
```

---

## 3. 命令大全

### 3.1 树场核心（省 token 协作）

| 命令 | 说明 |
|------|------|
| `python3 tree_farm.py <项目>` | 树场体检：建库 / 增量更新 |
| `--brief [--compact]` | AI 工作简报（先读这个，别全读项目） |
| `--genes <文件>` | 小树视角：看某文件的正/反向耦合基因 |
| `--bird <源> <目标>` | 报小鸟（强耦合，自动小虫子静态验证） |
| `--bird-weak <源> <目标>` | 报小鸟（弱耦合，需 ≥2 独立分支确认） |
| `--analyze <文件>` | LLM 自动找小鸟（无 key 时 dry-run 预览） |
| `--search <关键词>` | 语义搜索（符号精确 + 归一化模糊 + 内容 n-gram） |
| `--symbols <文件>` | 看文件里的函数/类符号 |
| `--trash` | 垃圾箱状态（不清空 = 任务未完成） |
| `--next-round` | 进入下一排查轮 |
| `--converge` | 收敛检查（本轮新增小鸟 ≤2 = 结束） |
| `--fix <文件>` | 修复协议（需用户同意后执行） |
| `--llm on\|off\|status` | LLM 开关（默认开启） |
| `--db` | SQLite 存储状态 |

### 3.2 代码分析（v3.1+）

| 命令 | 说明 | 支持语言 |
|------|------|---------|
| `--dead-code` | 💀 死代码检测 | Python/JS/TS/Java/Go/Rust |
| `--circular-deps` | 🔄 循环依赖检测 | 全语言（import 级） |
| `--impact <文件> [符号] [深度]` | 💥 影响分析 | 全语言（函数级 call 基因） |
| `--complexity [文件]` | 📊 代码复杂度（圈复杂度） | Python/JS/TS/Java/Go/Rust |
| `--architecture` | 🏗️ 架构分层识别 | 全语言（路径关键词） |
| `--graph [文件]` | 🕸 调用图（Mermaid 输出） | 全语言 |
| `--duplicates [阈值]` | 🔁 重复代码检测 | 全语言 |
| `--duplicates-func [阈值]` | 🔁 函数级查重（更精确） | 全语言 |
| `--debt` | 📉 技术债务评估（四维打分 A~D） | 全语言 |
| `--smells` | 🧪 代码异味检测 | Python/JS/TS/Java/Go/Rust |

### 3.3 交互式 REPL（v3.7 新增）

```bash
python3 tree_farm.py <项目> --repl
```

启动后进入交互环境，支持：

- **Tab 补全**：命令名 + 项目内文件路径
- **命令历史**：保存在 `~/.treefarm_history`，上下箭头翻看
- **彩色输出**：标题高亮（自动检测终端，管道/重定向时自动降级）
- **项目缓存**：启动时扫描一次建库，后续命令复用，不重复扫描

支持命令：`help` `brief` `search` `dead-code` `circular-deps` `impact`
`complexity` `architecture` `graph` `duplicates` `duplicates-func` `debt`
`smells` `bird` `trash` `exit`（`help <命令>` 看单个命令用法）

### 3.4 中文命令

全部分析命令有中文等价形式：

```bash
python3 tree_farm.py <项目> 分析 死代码
python3 tree_farm.py <项目> 分析 循环依赖
python3 tree_farm.py <项目> 分析 复杂度
python3 tree_farm.py <项目> 分析 架构
python3 tree_farm.py <项目> 分析 调用图
python3 tree_farm.py <项目> 分析 重复代码
python3 tree_farm.py <项目> 分析 函数级查重
python3 tree_farm.py <项目> 分析 影响 <文件>
python3 tree_farm.py <项目> 分析 异味
```

### 3.5 通用参数

| 参数 | 说明 |
|------|------|
| `--debug` | 调试日志（stderr） |
| `--quiet` / `--no-progress` | 关进度条 |
| `--json` | 机器可读 JSON 输出（部分命令） |
| `--compact` | 简报精简版 |

---

## 4. 代码异味检测（v3.7 新增）

`--smells` 检测 6 类常见代码异味，按严重程度（高/中/低）排序输出：

| 异味 | 默认阈值 | 说明 |
|------|---------|------|
| 长函数 `long_function` | >50 行（>100 高） | 拆分为职责单一的小函数 |
| 长参数列表 `long_parameter_list` | >5 个（>10 高） | 参数收拢为配置对象 |
| 嵌套过深 `deep_nesting` | >4 层（>6 高） | 提前 return / 守卫子句 |
| 重复条件分支 `duplicate_condition` | 条件文本相同 | 合并分支 / 提取命名变量 |
| 魔法数字 `magic_number` | 非 0/1/2/-1 等 | 提取为命名常量 |
| 大类 `god_class` | >300 行或 >20 方法 | 按职责拆分 |

Python 走 AST 精确分析；JS/TS/Java/Go/Rust 走轻量启发式（可能误报，需人工确认）。
测试文件（`test_*`、`*_test.*`、`/tests/`）自动跳过魔法数字检测。

---

## 5. 配置

配置文件按优先级查找：**项目目录 → 用户主目录 → 环境变量**。

### 5.1 配置文件（`.treefarm.json` / `.treefarm.toml`）

```json
{
  "api_key": "sk-xxx",
  "base_url": "https://api.deepseek.com/v1",
  "model": "deepseek-chat",
  "scan": {
    "ignore": ["vendor/", "*.min.js"],
    "ignore_dirs": ["node_modules", "dist"]
  }
}
```

TOML 等价写法：

```toml
api_key = "sk-xxx"
base_url = "https://api.deepseek.com/v1"
model = "deepseek-chat"

[scan]
ignore = ["vendor/", "*.min.js"]
ignore_dirs = ["node_modules", "dist"]
```

### 5.2 环境变量

```
TREEFARM_API_KEY=... TREEFARM_API_URL=... TREEFARM_MODEL=...
OPENAI_API_KEY=... DASHSCOPE_API_KEY=... DEEPSEEK_API_KEY=...
MOONSHOT_API_KEY=... ARK_API_KEY=...
```

---

## 6. 常见问题（速查）

| 问题 | 答案 |
|------|------|
| 小项目值得开树场吗？ | 几个小文件直接读更划算。树场为「大项目 + 多轮排查」而生 |
| 基因库存在哪？ | `<项目>/.tree_farm/treefarm.db`（SQLite，WAL 模式） |
| 怎么清空重建？ | 删掉 `.tree_farm/` 目录再跑一次 |
| 增量扫描快吗？ | 100 文件项目增量 <0.3s；1000 文件首次约 2s |
| 输出有颜色吗？ | 终端自动彩色；管道/重定向自动降级纯文本 |
| 需要装依赖吗？ | 不需要，纯标准库。flake8/pytest 只在开发/CI 用 |

详细见 [FAQ.md](FAQ.md)。

---

## 7. 完整工作流示例

**场景：AI 排查一个 200 文件的多语言项目**

```bash
# 第 0 步：建库 + 简报（AI 只看这个）
python3 tree_farm.py /path/to/project --brief

# 第 1 步：技术债务摸底
python3 tree_farm.py /path/to/project --debt
python3 tree_farm.py /path/to/project --smells

# 第 2 步：看核心文件的耦合
python3 tree_farm.py /path/to/project --genes /path/to/project/src/core.py
python3 tree_farm.py /path/to/project --graph /path/to/project/src/core.py

# 第 3 步：改代码前做影响分析
python3 tree_farm.py /path/to/project --impact /path/to/project/src/core.py

# 第 4 步：改完清理（清死代码、破循环依赖、合并重复）
python3 tree_farm.py /path/to/project --dead-code
python3 tree_farm.py /path/to/project --circular-deps
python3 tree_farm.py /path/to/project --duplicates-func

# 第 5 步：收尾（垃圾箱必须清空）
python3 tree_farm.py /path/to/project --converge
python3 tree_farm.py /path/to/project --trash
```

更多用法见 [BEST_PRACTICES.md](BEST_PRACTICES.md)。
