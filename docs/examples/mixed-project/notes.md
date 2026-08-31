# 混合语言示例项目

演示：跨语言影响分析（改 Python 文件，看 Go/JS 谁受影响）—— 树场最重要的差异化能力。

```
mixed-project/
├── py_service/
│   ├── api.py      # Python API 服务
│   └── models.py   # 数据模型（被 api.py 和 main.py 引用）
├── go_worker/
│   ├── worker.go   # Go 后台任务（调用 api）
│   └── go.mod
├── js_front/
│   └── client.js   # JS 前端（调用 api）
└── notes.md
```

## 期望输出（核心演示）

```bash
python3 tree_farm.py <本项目> --impact py_service/api.py
# 应同时列出 go_worker/worker.go 和 js_front/client.js —— 跨语言影响链！

python3 tree_farm.py <本项目> --architecture
# 应按 py_service/go_worker/js_front 目录分层

python3 tree_farm.py <本项目> --debt
# 混合语言技术债务总分
```
