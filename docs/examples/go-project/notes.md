# Go 示例项目

演示：Go 函数级调用 / 方法（receiver）/ struct 嵌入 → inherit 基因 / 接口。

```
go-project/
├── main.go        # 入口
├── greeter.go     # Greeter 结构体 + 方法 + 嵌入 Logger
└── notes.md
```

```bash
python3 tree_farm.py <本项目> --dead-code
# Greeter/Logger 都被使用，不应报死类

python3 tree_farm.py <本项目> --graph main.go
python3 tree_farm.py <本项目> --complexity greeter.go
```
