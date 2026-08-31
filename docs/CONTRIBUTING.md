# 🤝 TreeFarm 贡献指南

> 欢迎贡献！本文档说明如何搭建开发环境、代码规范、测试规范与 PR 流程。

---

## 1. 开发环境

零依赖原则：**运行时零依赖，开发时允许**。

```bash
# 只需 Python 3.8+（运行时零依赖）
cd tree-farm/scripts

# 开发/CI 用到的工具（可选安装）
pip install flake8        # 代码规范
# pytest 可选（项目测试用 unittest，零依赖框架）
```

## 2. 代码规范

- **flake8**：`--max-line-length=120 --ignore=E501,W503`
- **零依赖硬约束**：`tree_farm.py` + `treefarm/` 下所有文件只允许 import
  Python 标准库 + 项目内部模块。第三方库只允许出现在开发/CI/插件里。
- **中文优先**：用户可见输出、docstring、注释用中文；标识符用英文
- **类型注解**：公共 API 必须带类型注解；内部函数建议带

```bash
# 本地检查
python3 -m flake8 tree_farm.py treefarm/ --max-line-length=120 --ignore=E501,W503
```

## 3. 测试规范

- **框架**：unittest（保持零依赖卖点）
- **运行**：

```bash
cd tree-farm/scripts
python3 -m unittest discover -s tests          # 全量
python3 -m unittest tests.test_tree_farm.TestRustCallGraph   # 单个类
```

- **要求**：
  - 新功能必须带测试（新增解析器 ≥8 个用例；新分析功能 ≥5 个）
  - 修复 bug 必须带回归测试（先写失败用例，再修代码）
  - 测试用 `make_project(files_dict)` 造临时项目，用完自动清理
  - 提交前全量测试必须绿：`Ran N tests ... OK`

## 4. 模块结构（改代码前先读）

```
tree-farm/scripts/
├── tree_farm.py          # 入口聚合层（__all__ 汇总公共 API，新符号记得加）
└── treefarm/
    ├── common.py         # 常量 / 工具 / 文件缓存 / 语义搜索 / 指纹增量
    ├── parser.py         # 多语言基因/符号提取（Python AST + JS/Java/Go/Rust 轻量解析器）
    ├── storage.py        # GeneBank / TrashBin / Session / WeedIndex / SmallTree
    ├── config.py         # 配置解析 + LLMClient
    ├── analysis.py       # 全部分析算法（死代码/循环依赖/影响/复杂度/架构/异味/查重/债务/调用图）
    ├── core.py           # TreeFarm 主类（薄包装 + 树场机制）
    └── cli.py            # 命令分发 / REPL / main
```

**依赖链单向**：`common → parser/storage/config → analysis → core → cli`。
新增分析功能放 `analysis.py`，新语言解析器放 `parser.py`，禁止反向依赖。

## 5. PR 流程

1. Fork + 建分支：`git checkout -b feat/xxx`
2. 开发 → 本地全量测试绿 + flake8 干净
3. Commit 信息规范（中文，一行标题 + 要点列表）：

```
feat: 描述一句话

- 要点 1
- 要点 2
```

4. 推分支 → 提 PR。PR 描述里写：
   - 改了什么、为什么
   - 新增/变更的测试
   - 实测性能/效果数据（如有）

## 6. Issue 规范

- **Bug 报告**：复现步骤 + 期望行为 + 实际行为 + 环境（Python 版本/系统）
- **功能请求**：场景 + 期望能力 + 参考（如有）
- **问题讨论**：直接开 Discussion

## 7. 发布流程（维护者）

1. 全量测试绿 + flake8 干净 + 零依赖检查通过
2. 更新 `CHANGELOG.md`（新版本节，记重要变更与测试数）
3. 更新根 `README.md` 的功能列表
4. 打 tag：`vX.Y.Z`
5. 同步到融合版目录（`TreeOfThought+TreeFarm(融合版)/`）

> 📌 隐私红线：本仓库不含任何个人隐私信息；发布时检查提交内容，
> 禁止把用户名、路径、聊天记录等带入发布文件。
