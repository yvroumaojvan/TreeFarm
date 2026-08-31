# Python 示例项目

演示：死代码检测 / 循环依赖 / 代码异味 / 语义搜索。

## 结构

```
python-project/
├── main.py         # 入口：调用 service
├── services/
│   ├── order.py    # 订单服务（调用 inventory + 通知）
│   └── inventory.py# 库存服务（被 order 调用）
├── utils/
│   ├── helpers.py  # 工具函数（含一个死函数 dead_helper）
│   └── constants.py# 常量（被 main 引用）
└── notes.md
```

## 期望输出

```bash
python3 tree_farm.py <本项目> --dead-code
# 应看到 utils/helpers.py 里的 dead_helper 未使用

python3 tree_farm.py <本项目> --circular-deps
# 应看到 services/order ↔ services/inventory 循环依赖

python3 tree_farm.py <本项目> --impact services/order.py
# 应列出 main.py 受影响

python3 tree_farm.py <本项目> --smells
# 应看到 utils/helpers.py 里有长参数列表异味
```
