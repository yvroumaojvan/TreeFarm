# JS 示例项目

演示：JS 函数级调用分析 / 箭头函数 / 类方法 / 重复代码。

```
js-project/
├── app.js        # 入口：调用 services
├── services/
│   ├── user.js   # 用户服务（类 + 箭头函数）
│   └── order.js  # 订单服务（函数）
└── notes.md
```

```bash
python3 tree_farm.py <本项目> --graph app.js
python3 tree_farm.py <本项目> --duplicates-func
python3 tree_farm.py <本项目> --complexity
```
