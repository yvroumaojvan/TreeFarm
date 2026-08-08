#!/usr/bin/env node
'use strict';

/* ============================================================
 * render-tree.js — 思维树文字渲染器（通用版）
 *
 * 把思维树推理结果 JSON 渲染成「文字树」——替代原 Operit 插件的
 * Canvas 图像显示。任何 CLI / 聊天界面都能"看图"，零依赖，纯 Node。
 *
 * 用法：
 *   node render-tree.js --demo                    # 渲染内置演示数据
 *   node render-tree.js data.json                 # 渲染 JSON 文件
 *   node render-tree.js --compact data.json       # 紧凑模式（分支只显示结论）
 *   cat data.json | node render-tree.js           # 从 stdin 读入
 *
 * JSON 数据结构（与 SKILL.md 数据模型一致）：
 * {
 *   "trunk": "精简后的问题（树干）",
 *   "rounds": [{
 *     "round": 1,
 *     "branches": [ { "dimension", "hypothesis", "reasoning", "conclusion" } ],
 *     "consensus": "本轮共识",
 *     "weakSignals": ["弱信号1", "弱信号2"]
 *   }],
 *   "stopReason": "converged | maxRounds | timeBudget | error",
 *   "summary": "推理摘要",
 *   "answer": "最终答案",
 *   "weakSignals": ["顶层弱信号（兼容原版工具输出结构，可选）"],
 *   "meta": { "rounds": 3, "converged": true }
 * }
 * ============================================================ */

var fs = require('fs');

/* ---------------- 符号表（与 SKILL.md 一致） ---------------- */
var SYM = {
  trunk: '🌳', branch: '🌿', consensus: '🧡', weak: '⚡',
  answer: '💎', summary: '📋', stop: '⏹', search: '🔍'
};

var STOP_REASON_TEXT = {
  converged: '已收敛（共识一致性 ≥ 90%）',
  maxRounds: '达到最大轮数',
  timeBudget: '时间预算用尽',
  error: '异常中断'
};

/* 兼容不支持 emoji 的环境：--ascii 参数开启文字前缀 */
var USE_ASCII = process.argv.indexOf('--ascii') >= 0;
var MARK = {
  trunk: USE_ASCII ? '[树干]' : SYM.trunk + ' ',
  branch: USE_ASCII ? '[分支]' : SYM.branch + ' ',
  consensus: USE_ASCII ? '[共识]' : SYM.consensus + ' ',
  weak: USE_ASCII ? '[弱信号]' : SYM.weak + ' ',
  answer: USE_ASCII ? '[答案]' : SYM.answer + ' ',
  summary: USE_ASCII ? '[摘要]' : SYM.summary + ' ',
  stop: USE_ASCII ? '[停止]' : SYM.stop + ' ',
  search: USE_ASCII ? '[轮次]' : SYM.search + ' '
};

/* ---------------- 工具函数 ---------------- */
function oneLine(s, max) {
  if (s === null || s === undefined) return '';
  return String(s).replace(/\s+/g, ' ').trim().slice(0, max);
}

/* ---------------- 节点渲染（标准树形递归） ---------------- */
// node = { label, children: [node|{text}] }  text 节点渲染为叶子行
function renderNode(lines, node, prefix, isLast) {
  lines.push(prefix + (isLast ? '└── ' : '├── ') + node.label);
  var childPrefix = prefix + (isLast ? '    ' : '│   ');
  var kids = node.children || [];
  for (var i = 0; i < kids.length; i++) {
    var k = kids[i];
    if (k.text !== undefined) {
      lines.push(childPrefix + '    ' + k.text);
    } else {
      renderNode(lines, k, childPrefix, i === kids.length - 1);
    }
  }
}

/* ---------------- 思维树 → 树节点 ---------------- */
// 兼容原版工具输出：rounds 可能是数组（本轮格式）或数字（meta.rounds 计数）
function getRounds(data) {
  if (Array.isArray(data.rounds)) return data.rounds;
  return [];
}

function getRoundCount(data) {
  if (Array.isArray(data.rounds)) return data.rounds.length;
  if (data.meta && typeof data.meta.rounds === 'number') return data.meta.rounds;
  return 0;
}

function buildTree(data, compact) {
  var rounds = getRounds(data);
  var root = {
    label: MARK.trunk + '树干：' + (oneLine(data.trunk, 60) || '(空)'),
    children: []
  };

  for (var ri = 0; ri < rounds.length; ri++) {
    var r = rounds[ri];
    var rn = r.round || (ri + 1);
    var branches = r.branches || [];
    var ws = r.weakSignals || [];

    // 轮次节点：分支们 + 共识/弱信号
    var roundNode = {
      label: MARK.search + '第 ' + rn + ' 轮 · ' + branches.length + ' 个分支' +
        (ri > 0 ? '（基于共识再发散）' : ''),
      children: []
    };
    for (var bi = 0; bi < branches.length; bi++) {
      var b = branches[bi];
      var branchNode = { label: MARK.branch + (b.dimension || '分支' + (bi + 1)), children: [] };
      if (compact) {
        var brief = oneLine(b.conclusion || b.reasoning || b.hypothesis, 60);
        if (brief) branchNode.children.push({ text: '→ ' + brief });
      } else {
        if (b.hypothesis) branchNode.children.push({ text: '假设 → ' + oneLine(b.hypothesis, 60) });
        if (b.reasoning) branchNode.children.push({ text: '推理 → ' + oneLine(b.reasoning, 60) });
        if (b.conclusion) branchNode.children.push({ text: '结论 → ' + oneLine(b.conclusion, 60) });
      }
      roundNode.children.push(branchNode);
    }
    // 共识（收拢汇聚节点）；无共识但存在弱信号时单独挂弱信号节点（防丢失）
    if (r.consensus) {
      var consNode = { label: MARK.consensus + '第 ' + rn + ' 轮共识', children: [] };
      consNode.children.push({ text: '共识 → ' + oneLine(r.consensus, 80) });
      for (var wi = 0; wi < ws.length; wi++) {
        consNode.children.push({
          text: MARK.weak + '弱信号 → ' + oneLine(ws[wi], 80) + '（' + ws.length + '/' + branches.length + ' 分支提及）'
        });
      }
      roundNode.children.push(consNode);
    } else if (ws.length > 0) {
      var wsNode = { label: MARK.weak + '第 ' + rn + ' 轮弱信号', children: [] };
      for (var wj = 0; wj < ws.length; wj++) {
        wsNode.children.push({
          text: '→ ' + oneLine(ws[wj], 80) + '（' + ws.length + '/' + branches.length + ' 分支提及）'
        });
      }
      roundNode.children.push(wsNode);
    }
    root.children.push(roundNode);
  }

  // 顶层弱信号记录（兼容原版工具输出结构：result.weakSignals 数组）
  var topWS = data.weakSignals || [];
  if (topWS.length > 0) {
    var topNode = { label: MARK.weak + '弱信号记录', children: [] };
    for (var ti = 0; ti < topWS.length; ti++) {
      topNode.children.push({ text: '→ ' + oneLine(topWS[ti], 80) });
    }
    root.children.push(topNode);
  }

  // 最终答案（果实长在树梢）——即使 0 轮也渲染，保证答案永不丢失
  if (data.answer || data.summary || data.stopReason) {
    var answerNode = { label: MARK.answer + '最终答案', children: [] };
    var answerText = data.answer || '';
    var lines = String(answerText).split('\n').map(function (l) { return l.trim(); }).filter(Boolean);
    for (var li = 0; li < lines.length; li++) {
      answerNode.children.push({ text: lines[li] });
    }
    if (data.summary) {
      answerNode.children.push({ text: MARK.summary + '推理摘要 → ' + oneLine(data.summary, 100) });
    }
    var reason = STOP_REASON_TEXT[data.stopReason] || data.stopReason || '';
    if (reason) {
      answerNode.children.push({ text: MARK.stop + '停止原因 → ' + reason });
    }
    root.children.push(answerNode);
  }
  return root;
}

/* ---------------- 入口 ---------------- */
function render(data, compact) {
  var rounds = getRounds(data);
  var branchCount = 0;
  rounds.forEach(function (r) { branchCount += (r.branches || []).length; });
  var reason = STOP_REASON_TEXT[data.stopReason] || data.stopReason || '';

  var header = MARK.trunk.trim() + ' 思维树 · 共 ' + getRoundCount(data) + ' 轮 · ' +
    branchCount + ' 个分支' + (reason ? ' · ' + reason : '');
  var lines = [header, '│'];
  renderNode(lines, buildTree(data, compact), '', true);
  return lines.join('\n');
}

function parseArgs() {
  var args = process.argv.slice(2);
  var opts = { compact: false, input: null };
  for (var i = 0; i < args.length; i++) {
    if (args[i] === '--compact' || args[i] === '-c') opts.compact = true;
    else if (args[i] === '--demo') opts.demo = true;
    else if (args[i] === '--ascii') { /* 已在顶部处理 */ }
    else opts.input = args[i];
  }
  return opts;
}

function demoData() {
  // ★ 演示数据必须示范"真 AI 思考"：维度按问题领域选，推理结合具体数字与事实，
  //   绝不使用「技术可行性/成本效益/用户体验」万能模板（那是模板垃圾，不是思考）。
  // ★ 默认配置：8 分支 × 3 轮（与 SKILL.md 配置项一致）
  return {
    trunk: '该不该辞职去创业？',
    rounds: [
      {
        round: 1,
        branches: [
          { dimension: '财务安全边际', hypothesis: '辞职后收入归零，存款只能支撑 6 个月生活', reasoning: '创业从启动到稳定盈利通常需要 9~18 个月，6 个月缓冲覆盖不了完整周期', conclusion: '必须先把缓冲金攒到 18 个月，或保留兼职收入' },
          { dimension: '市场验证信号', hypothesis: '需求是真实存在的，不是想象出来的', reasoning: '目前还没有任何付费客户和订单，行业又处于下行周期，需求只停留在假设阶段', conclusion: '先拿到 3~5 个付费订单，再考虑全职投入' },
          { dimension: '能力与机会成本', hypothesis: '现有技术能力可以支撑创业', reasoning: '写代码是强项，但创业还需要销售、运营、财税等新能力，且机会成本是放弃稳定涨薪', conclusion: '能力缺口可以靠合伙人或外包补齐，不是主要障碍' },
          { dimension: '家庭支持与退路', hypothesis: '家人支持辞职创业', reasoning: '如果伴侣或父母反对，心理压力会拖垮决策质量，失败后也没有退路', conclusion: '需要先沟通并设定止损线：6 个月没起色就回归职场' },
          { dimension: '行业周期与政策', hypothesis: '行业处于下行期，创业时机不佳', reasoning: '下行期融资难、客户预算收缩，个人能力再强也难逆势', conclusion: '评估行业回暖时点，或选择逆周期的细分方向' },
          { dimension: '竞争者格局', hypothesis: '市场还有空隙可钻', reasoning: '大厂和同赛道小团队已经存在，正面硬刚没有胜算', conclusion: '先做细分市场差异化定位，避开正面竞争' },
          { dimension: '个人精力与健康', hypothesis: '能承受创业前期的高强度', reasoning: '全职创业 996 是常态，长期透支会直接影响判断力', conclusion: '建立可持续的工作节奏，而不是短期冲刺' },
          { dimension: '现金流与合规', hypothesis: '注册个体户/公司即可开业', reasoning: '社保断缴、税务申报、发票开具等琐事会持续占用精力', conclusion: '财务代办外包，把时间留给核心业务' }
        ],
        consensus: '核心问题不是"要不要创业"，而是"什么时候、以什么方式"——先验证需求、再全职投入，同时补足能力与现金流短板',
        weakSignals: ['社保断缴超过 3 个月会影响医保报销，这个风险几乎所有分支都忽略了']
      },
      {
        round: 2,
        branches: [
          { dimension: '最小验证方案', hypothesis: '用最小成本验证付费意愿', reasoning: '2 周内做出 MVP 挂到 3 个平台测真实转化率，比任何市场调研都可靠', conclusion: '本月启动 MVP 测试，以转化率数据决定去留' },
          { dimension: '收入断档缓冲', hypothesis: '辞职前后可以保留部分收入来源', reasoning: '先接外包/兼职过渡，把收入缺口压到最小，降低断档风险', conclusion: '过渡期 3 个月，边赚边验证' },
          { dimension: '止损与回归机制', hypothesis: '创业失败有退路', reasoning: '提前与行业保持联系，明确"6 个月无起色即回归"的硬指标，失败成本可控', conclusion: '最坏情况是回到原点，不算沉没成本' },
          { dimension: '客源获取渠道', hypothesis: '有稳定获客渠道', reasoning: '平台 + 人脉 + 内容输出三渠道并行，单一渠道风险太高', conclusion: '三渠道各自设定获客目标，分散风险' },
          { dimension: '差异化定位', hypothesis: '细分市场有空隙', reasoning: '聚焦特定行业痛点，避开大厂主赛道，才有生存空间', conclusion: 'MVP 只针对 1 个细分人群做深做透' },
          { dimension: '合伙人评估', hypothesis: '需要互补型合伙人', reasoning: '销售/运营短板需互补，但股权分配必须提前谈清，否则后患无穷', conclusion: '先单干验证，跑通后再考虑合伙人' },
          { dimension: '定价与毛利', hypothesis: '按价值定价能覆盖成本', reasoning: '低价抢单不可持续，需按客户价值定价并测试接受度', conclusion: '定 3 档价格测试市场接受度' },
          { dimension: '现金流管理', hypothesis: '现金流能撑过验证期', reasoning: '预留 3 个月生活 + 3 个月运营资金，安全线内才允许全职', conclusion: '资金安全线 = 6 个月总支出' }
        ],
        consensus: '以"先验证、后全职"为路径，3 个月内用转化率数据决定全职时间表；先单干、后合伙人',
        weakSignals: ['过渡期要主动续缴社保，否则医保断档影响看病报销']
      },
      {
        round: 3,
        branches: [
          { dimension: '转化率目标', hypothesis: 'MVP 转化率可以量化衡量', reasoning: '3 平台挂 2 周，转化率 >2% 即视为需求成立', conclusion: '设硬指标：转化率 2% 为全职门槛' },
          { dimension: '兼职过渡排期', hypothesis: '过渡期收入不断', reasoning: '每周 20 小时接单 + 固定创业时段，排期必须错峰', conclusion: '固定"创业时段"，防止两头都做不好' },
          { dimension: '第一单获客计划', hypothesis: '能拿到前 3 个付费客户', reasoning: '从熟人圈 + 垂直社区切入，首单可优惠但必须真付费', conclusion: '30 天内签下第一单' },
          { dimension: '定价验证', hypothesis: '按价值定价可行', reasoning: 'A/B 测试 3 档价格，观察哪档转化和复购最好', conclusion: '用数据定最终价格' },
          { dimension: '时间管理', hypothesis: '碎片时间可以高效利用', reasoning: '番茄钟 + 任务看板，每天固定 3 小时深度工作', conclusion: '建立可持续节奏，保护睡眠' },
          { dimension: '风险准备金', hypothesis: '最坏情况可控', reasoning: '预留 6 个月生活备用金，不动用股市资金和借款', conclusion: '安全线内才允许全职' },
          { dimension: '家庭沟通节点', hypothesis: '家人支持可预期', reasoning: '每月一次进度同步，用数据说话比情绪说服有效', conclusion: '设 3 个固定沟通节点' },
          { dimension: '退出条件复盘', hypothesis: '失败可以优雅退出', reasoning: '第 3 个月末按数据做"继续/全职/止损"三选一', conclusion: '决策日设为第 90 天' }
        ],
        consensus: '3 个月验证期目标清晰：转化率 2% + 首单 + 定价数据齐备，第 90 天做最终决策',
        weakSignals: []
      }
    ],
    stopReason: 'maxRounds',
    summary: '三轮推理从财务、市场、能力、家庭、行业、竞争、健康、合规八个维度展开，第 2 轮聚焦验证路径，第 3 轮细化为可执行的 90 天计划；弱信号（社保断缴风险）跨轮被验证为真信号。',
    answer: '结论先行：现在不该直接辞职。用 90 天做最小验证：2 周 MVP 挂 3 个平台测转化率，30 天内拿下首单，同时攒够 18 个月缓冲金，第 90 天按数据做全职决定。\n关键依据：①创业回本周期 9~18 个月，6 个月存款不够；②没有付费客户的需求只是假设；③弱信号提示社保断缴影响医保，过渡期需主动续缴。\n风险与不确定性：行业下行期转化率可能低于预期，需要 2% 转化率硬指标把关。\n行动建议：本月启动 MVP + 3 平台测转化，固定每天 3 小时深度工作，第 30 天复盘首单进度。'
  };
}

function main() {
  var opts = parseArgs();
  var data = null;

  if (opts.demo) {
    data = demoData();
  } else if (opts.input) {
    try {
      data = JSON.parse(fs.readFileSync(opts.input, 'utf8'));
    } catch (e) {
      console.error('✖ 无法读取/解析 JSON 文件：' + opts.input + '\n  ' + e.message);
      process.exit(1);
    }
  } else if (!process.stdin.isTTY) {
    // 从 stdin 读
    try {
      data = JSON.parse(fs.readFileSync(0, 'utf8'));
    } catch (e) {
      console.error('✖ stdin 不是合法 JSON：' + e.message);
      process.exit(1);
    }
  } else {
    console.error('用法：\n  node render-tree.js --demo\n  node render-tree.js data.json\n  node render-tree.js --compact data.json\n  cat data.json | node render-tree.js\n参数：--compact 紧凑模式，--ascii 无 emoji 模式');
    process.exit(1);
  }

  process.stdout.write(render(data, opts.compact) + '\n');
}

main();
