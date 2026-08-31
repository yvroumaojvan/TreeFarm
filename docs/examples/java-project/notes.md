# Java 示例项目

演示：Java 继承基因（extends/implements）/ 影响分析 / 接口。

```
java-project/
├── src/
│   ├── Main.java          # 入口
│   └── com/demo/
│       ├── Payment.java   # 接口
│       ├── Alipay.java    # 实现 Payment
│       ├── WechatPay.java # 实现 Payment
│       └── OrderService.java  # 被 Main 调用
└── notes.md
```

```bash
python3 tree_farm.py <本项目> --dead-code
# Payment 接口的实现类都被实例化，不应报死类

python3 tree_farm.py <本项目> --graph src/com/demo/OrderService.java
python3 tree_farm.py <本项目> --impact src/com/demo/Payment.java
```
