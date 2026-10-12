---
name: innovation-engine
description: 创新引擎（蝴蝶效应·弱信号不规则圆 + 创新组合引擎），AI agent 的创新推理技能（鱼肉猫卷 2026-10-12 原创设计）。带会话级开关状态机。默认关闭、绝不自动使用。开启方式：用户明确说出「开启创新引擎」/「打开创新引擎」/「用创新引擎」/「创新引擎」等指令 → 本技能进入【开启状态】，之后本次会话内遇到需要创新方案的任务（找新解法、生成多方案、组合突破、评估创新度）自动使用，无需用户每轮重复说。单次使用：用户说「创新引擎：任务内容」→ 只对这一次用。关闭方式：用户说「关闭创新引擎」→ 回到关闭状态；或本次会话结束 → 自动回到默认关闭。开启后执行：内核 → 自动生成多方案分支（12 策略池+果实嫁接+变异）→ 画不规则圆（每条边=一个方案，半径=知识强度）→ 找最特殊边 → 多轮进化重组最不规则圆 → 创新度评估 → 可选导出报告页。执行模式默认开启：思考时可调用宿主工具读代码、跑测试、写文件。Use ONLY when the state machine says ON (user said "开启创新引擎" and never said "关闭创新引擎" in this session, OR user explicitly requests innovation-engine for this task); never auto-trigger when OFF.
---

# 🌱 创新引擎（蝴蝶效应·弱信号不规则圆 + 创新组合引擎）

> 作者原创：每个弱信号分支 = 圆的一条边（半径 = 知识强度）。
> 规则圆 vs 不规则圆对比 → 提取最特殊边 → 替换重组 → **最不规则的圆 = 创新最多的圆**。
> 「哪个圆最不规整、凹凸最多，就代表这个圆创新最多」—— 创新 = 偏离规则的量。

## 0. 开关与触发方式（最重要，先读这个！）

本技能**默认关闭，绝不自动使用**（作者硬性要求：宁缺毋滥）。由用户的一句话控制：

| 用户说的话 | 效果 |
|-----------|------|
| 「开启创新引擎」/「打开创新引擎」/「用创新引擎」 | 🟢 **开启**：之后遇到创新类任务就用，直到用户说关闭 |
| 「创新引擎：<任务>」/「用创新引擎想一下<任务>」 | 🟢 **单次使用**：只这一次用 |
| 「关闭创新引擎」/「关掉创新引擎」 | 🔴 **关闭**：恢复正常回答 |

**注意**：本技能是 **给 AI agent 用的**（不是给人敲命令）——agent 开启后在推理中调用创新引擎生成方案、评估创新度。

## 1. 技能包结构（自包含，任何 agent 导入即用）

```
innovation-engine/                ← 技能包根目录（整个文件夹导入技能库）
├── SKILL.md                      ← 技能入口（本文件）
├── README.md                     ← 导入/使用说明
└── scripts/                      ← 引擎本体（零依赖，Node 直接跑）
    ├── innovation.js             灵感树主入口（run/gen/circle/evolve 命令）
    ├── innovation_generate.js    方案生成器（12 策略池+果实嫁接+变异+自动评分）
    ├── innovation_irregular_circle.js  蝴蝶效应不规则圆算法（纯函数模块）
    ├── innovation_circle_view.js 导出报告页（任务快照，只读）
    ├── innovation_config.json    配置文件（功能开关/分支数量/模式）
    ├── similarity.js             相似度工具（trigram Jaccard）
    └── test_*.js                 测试（62 用例）
```

**所有命令都在 `<技能包根>/scripts/` 目录下执行**（以下示例假设已 cd 到该目录）。

## 2. Agent 调用协议（最重要！本技能是给 AI agent 用的）

### 第 0 步：问题自评（先判断要不要搜索！作者 2026-10-13 钦定）

```bash
cd <技能包根>/scripts/

# 评估一个问题：AI 自己判断"脑子里的资料够不够、问题复不复杂、能不能自己解决"
node innovation_assess.js "<问题>"
```

**输出「需要搜索的程度」0~100% + 三档判断**：
- **≤20%** 🟢 直接回答（资料足够）
- **20~60%** 🟡 建议搜索（部分资料可能过时/不足）
- **≥60%** 🔴 必须搜索（最新动态/复杂领域，资料跟不上——先去搜索最新资料再回答）

四个维度：时间敏感(0.35) + 复杂度(0.25) + 知识缺口(0.25) + 事实性(0.15)。
**agent 流程**：拿到问题 → 先 assess → ≥60 先联网搜索最新资料 → 再决定直接答或跑引擎深推。

### 一键主入口（agent 只用这一条就够）：

```bash
cd <技能包根>/scripts/

# 完整流程：自动生成多方案 → 画不规则圆 → 多轮进化 → 输出结构化结果
node innovation.js run "<内核/问题>" [轮数1-5] [--out 结果.json] [--html 报告.html] [--mode simple|dev] [--count 分支数]

# 例：跑 3 轮进化，输出 JSON + HTML 报告
node innovation.js run "如何让AI自己产生创新方案" 3 --out result.json --html 报告.html
```

**run 输出结构（JSON，agent 直接消费）**：
- `kernel`：内核文本
- `rounds[]`：每轮 { round, kernel, count, branches[16条方案], special(最特殊边), innovation{score,level,cv,sharp}, circle(完整圆分析) }
- `final`：{ specialEdges[每轮最特殊边], avgInnovation, summary }
- `task`：{ currentRound, totalRounds }（导出快照用）

### 分步命令（agent 需要精细控制时用）：

```bash
# ① gen：只生成方案（不进化）
node innovation.js gen "<内核/问题>"
# ② circle：对当前分支画圆
node innovation.js circle --out c1.json
# ③ evolve：多轮进化
node innovation.js evolve "<内核>" <轮数1-5>
# ④ 导出报告页（任务快照，只读）
node innovation_circle_view.js c1.json 报告.html
```

### 模块直调（agent 写脚本时用 require）：

```js
const gen = require('./innovation_generate.js');          // generate(kernel, {count, aiRadius})
const circle = require('./innovation_irregular_circle.js'); // analyze(branches, params)
```

## 3. 配置（innovation_config.json，想改啥改啥）

- `mode`: 'simple'=普通用户模式（大白话）/ 'dev'=开发者模式（全细节）
- `features`: 功能开关（autoGenerate / circle / evolve / exportHtml 一键开/关）
- `branches`: 分支数量（min 3 / default 6 / max 12）
- `circle`: 圆算法参数（zThreshold / sharpDelta / 权重 / 等级阈值）
- `evolve.rounds`: 默认进化轮数

## 4. 什么时候用（agent 判断标准）

- 用户要「新解法 / 多种方案 / 换思路」→ gen
- 用户要「评估创新度 / 看方案分布 / 找最特殊的方案」→ circle
- 用户要「组合突破 / 迭代进化 / 越搞越野」→ evolve
- 用户要「导出报告 / 保存快照」→ circle + circle_view

## 5. 测试状态

- 新功能 62/62 + 旧功能 38/38 零破坏 = 100/100 全绿
- 回归：旧命令 idea→forms→score→converge→harvest 手工链路 OK
- 版本：2026-10-13 凌晨集成（v1.1，「创新组合引擎」定位）

## 6. 铁律提醒

- **这不是评分系统**（2026-10-13 定版）——评分器管"打几分"，圆管"创新长什么样、刺多尖"；本质是创新组合引擎
- **知识强度 radius** = AI 打分 + 算法微调：clamp(0.5×相关性 + 0.3×新颖度 + 0.2×完整度)，支持外部传 aiRadius 底分
- 输出时：simple 模式大白话、突出最特殊边、给整轮总结
