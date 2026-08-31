# -*- coding: utf-8 -*-
"""treefarm 包 —— 树场机制（多模块版，v3.8）
模块划分：
  common    常量 / 工具 / 文件缓存 / 语义搜索 / 指纹增量
  parser    多语言基因提取（Python AST + JS/Java/Go 轻量解析器）
  storage   GeneBank / TrashBin / Session / WeedIndex / SmallTree（SQLite）
  config    配置解析 + LLMClient
  analysis  死代码 / 循环依赖 / 影响分析 / 复杂度 / 架构分层
  core      TreeFarm 主类
  cli       命令分发 / main
  sandbox   小沙箱（v3.8 新增：动态代码分析 / 受限执行 / 多语言 subprocess / 调用追踪 / 覆盖率）
直接运行请用外层入口：`python3 tree_farm.py <项目>`
"""
