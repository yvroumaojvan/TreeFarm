# 🌱 创新引擎 · 蝴蝶效应·弱信号不规则圆（AI Agent 技能包）

> **原创设计：鱼肉猫卷** · 2026-10-12 构思 · 2026-10-13 工程落地
>
> 「每个弱信号分支 = 圆的一条边（半径 = 知识强度）。
> 规则圆 vs 不规则圆对比 → 提取最特殊边 → 替换重组 →
> **最不规则的圆 = 创新最多的圆**。哪个圆最不规整、凹凸最多，就代表这个圆创新最多。」
>
> —— 创新 = 偏离规则的量

## 这是什么

一个**给 AI agent 用的创新推理技能**（skill 技能包）：
输入一个问题/内核 → 自动生成多方案分支 → 画不规则圆 → 找最特殊边 →
多轮进化重组最不规则圆 → 输出结构化创新结果 + 可导出报告页。

零依赖（纯 Node 标准库），手机都能跑。

## 技能包结构

```
innovation-engine/
├── SKILL.md            ← 技能入口（agent 读这个就知道怎么用）
├── README.md           ← 本文件
└── scripts/            ← 引擎本体
    ├── innovation.js              主入口（run/gen/circle/evolve 命令）
    ├── innovation_generate.js     方案生成器（12 策略池+果实嫁接+变异+自动评分）
    ├── innovation_irregular_circle.js  不规则圆算法（纯函数，可 require）
    ├── innovation_circle_view.js  导出报告页（任务快照，只读）
    ├── innovation_config.json     配置文件
    ├── similarity.js              相似度工具
    └── test_*.js                  测试（62 用例）
```

## 怎么导入（任何 AI agent 通用）

1. 把整个 `innovation-engine/` 文件夹放进你的**技能库**（如 `~/.qwen/skills/`，或平台要求的技能目录）
2. Agent 读取 `SKILL.md` 即获知调用协议
3. 需要 Node.js 环境（`node -v` 可用即可）

## 怎么用（Agent 视角，一键）

```bash
cd <技能包根>/scripts/

# 一键完整流程：生成方案 → 画圆 → 多轮进化 → 结构化结果
node innovation.js run "你的问题" 3 --out result.json --html 报告.html
```

- `--out`：结构化 JSON（agent 直接消费：每轮方案/最特殊边/创新度/最终重组）
- `--html`：导出只读报告页（手机浏览器可看）
- `--mode dev`：开发者模式全量输出；默认 simple 大白话
- `--count N`：控制每轮方案数量（3~12）

分步控制：`gen`（只生成）/ `circle`（只画圆）/ `evolve`（只进化）。

## 配置（innovation_config.json）

- `mode`: 'simple'（大白话）/ 'dev'（全细节）
- `features`: autoGenerate / circle / evolve / exportHtml 功能开关
- `branches`: 分支数量 min 3 / default 6 / max 12
- `circle`: 圆算法参数（zThreshold / sharpDelta / 权重 / 等级阈值）
- `evolve.rounds`: 默认进化轮数

## 测试状态

- 不规则圆算法 34/34 · 生成器 18/18 · 导出页 10/10 · 旧功能 38/38 零破坏
- 全量 **100/100 全绿**（`node test_innovation_*.js` 可复验）

## 原理一句话

思维树每轮跑出的弱信号分支 = 圆的边；半径 = 该知识强度（AI 打分 + 算法微调）。
特殊边 = 离群值（z 分数）；多轮最特殊边替换重组 = 把每轮最大创新突破组合起来。
创新度 = 半径离散度 + 锐角数量（锐角 = 突破性边）。评分器管"打几分"，
这个圆管"创新长什么样、刺多尖"——**它是创新组合引擎，不是评分系统**。
