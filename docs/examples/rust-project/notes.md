# Rust 示例项目（v3.7 新增语言支持）

演示：Rust 函数级调用分析 / trait 继承 / 泛型 / 生命周期 / 死代码检测 / 复杂度。

## 结构

```
rust-project/
├── Cargo.toml      # 依赖声明（分析器只读，不解析）
├── src/
│   ├── main.rs     # 入口：调用 engine
│   ├── engine.rs   # 引擎：trait Engine 实现 + 泛型 + 生命周期
│   ├── storage.rs  # 存储：被 engine 引用
│   └── unused.rs   # 死代码演示（包含从未调用的函数和结构体）
└── notes.md
```

## 期望输出

```bash
python3 tree_farm.py <本项目> --dead-code
# unused.rs 里的 unused_fn / DeadStruct 应被检出

python3 tree_farm.py <本项目> --impact src/engine.rs
# 应列出 main.rs（跨文件调用方）

python3 tree_farm.py <本项目> --complexity
# engine.rs 的 run 方法复杂度最高

python3 tree_farm.py <本项目> --graph src/main.rs
# 调用图含 main → engine::Engine::run → storage
```
