---
name: tree-of-thought-innovation
description: 灵感树（TreeOfThought 创新模式 v1.0，乖宝 2026-09-22 原创）——让 AI 学会「灵光一现」。内核→表达形式→新颖评分→灵感果实库。默认关闭、绝不自动触发。开启方式：用户说「灵感树」/「创新模式」/「开创新分支」→ 本次会话启用；用「抓虫树」/「关闭灵感树」切回。与抓虫树共用思维树开关，双模式由 tree.js 统一入口切换。
---

# 💡 灵感树 v1.0（思维树创新模式 · 乖宝原创）

> 抓虫树帮 AI 找"哪里错了"；**灵感树帮 AI 找"还能怎么想"**。
> 理论（乖宝 2026-09-22 原创）：灵感 = 把两个毫不相干的东西串起来。
> AI 创新链 = 内核 → 召唤表达形式 → 载体 = 创新成果（拆掉"先有感受"的前提）。

## 0. 开关（默认关闭，绝不自动触发）
- 开启：「灵感树」「创新模式」「开创新分支」
- 关闭：「抓虫树」「关闭灵感树」
- 单次：「灵感树：<内核>」

## 1. 命令流（scripts/innovation.js，统一入口 scripts/tree.js）
```
node tree.js idea idea "内核"         种种子（一个直觉/问题/胡思乱想）
node tree.js idea forms '[...]'       长枝丫（每种表达形式一个分支）
node tree.js idea score n_2 '{...}'   双维评分
node tree.js idea converge            摘果（最优表达 = 创新成果）
node tree.js idea harvest             入库（灵感果实可复用）
node tree.js idea recall "新内核"      联想（旧果实 + 新内核 = 灵光一现）
node tree.js idea render|status       画树/看状态
```

## 2. 分支 JSON 格式（forms）
```json
[
  {"form": "表达形式名（算法/比喻/机制/诗/结构…）",
   "idea": "用这个形式怎么表达内核",
   "why": "为什么这个形式适配内核"}
]
```

## 3. 评分（novelty 权重最高——与抓虫树相反）
```
综合 = 适配内核(relevance)×35% + 新颖度(novelty)×40%
     + 表达力(expressiveness)×15% + 可实现(feasibility)×10%
```

## 4. 果实库（乖宝原创增量，没人做过）
- 文件：innovation_harvest.json（零依赖）
- 灵感存下来、整理好、以后还能再用；新内核自动联想旧果实组合成新灵感

## 5. 质量铁律（沿用设计理念）
- 面向小白：全程中文、给下一步提示
- 轻量化：零依赖、纯 Node、手机可跑
- 语义联想交给 AI 复核（similarity.js 只管字面相似，AI 判语义）
