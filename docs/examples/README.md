# 🌳 TreeFarm 示例项目

本目录包含 5 个不同语言的示例项目，用于演示 TreeFarm 的各种分析功能。

| 项目 | 语言 | 演示重点 |
|------|------|---------|
| [python-project/](python-project/) | Python | 死代码 / 循环依赖 / 代码异味 |
| [js-project/](js-project/) | JS | 函数级调用 / 重复代码 |
| [java-project/](java-project/) | Java | 继承基因 / 影响分析 |
| [go-project/](go-project/) | Go | 结构体嵌入 / 接口 |
| [rust-project/](rust-project/) | Rust | trait 继承 / 泛型 / 生命周期（v3.7） |
| [mixed-project/](mixed-project/) | 混合 | 跨语言影响分析 |

## 快速体验

```bash
cd tree-farm/scripts

# 全项目体检
python3 tree_farm.py ../../docs/examples/mixed-project

# 技术债务 + 代码异味
python3 tree_farm.py ../../docs/examples/mixed-project --debt
python3 tree_farm.py ../../docs/examples/mixed-project --smells

# 调用图（复制输出到 mermaid.live 渲染）
python3 tree_farm.py ../../docs/examples/mixed-project --graph

# Rust 项目单独分析（v3.7 新语言）
python3 tree_farm.py ../../docs/examples/rust-project --dead-code
python3 tree_farm.py ../../docs/examples/rust-project --complexity

# 交互式排查
python3 tree_farm.py ../../docs/examples/mixed-project --repl
```

> 提示：每个项目内都有 `notes.md` 说明这个示例演示了什么、期望看到什么输出。
