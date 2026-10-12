#!/usr/bin/env node
'use strict';

/* ============================================================
 * innovation_generate.js —— 方案生成器（创新引擎核心：让 AI 自己会创新）
 * 修复 v0.9.1 病根：innovation.js 只会记账（forms/score 全靠人喂），
 * 本模块让它真正自己长出方案分支。
 *
 * 生成来源（三个，config 里可分别开关）：
 *   ① 策略池（strategyPool）：12 个本质不同的创新角度（替代/组合/换结构/缓存/
 *      并行/预计算/分块/换模型/重构/反向/简化/泛化）—— GPT 路线图"A~H 方案"的扩展
 *   ② 果实库嫁接（harvestGraft）：旧果实表达形式套新内核 = 跨域灵感（作者原创）
 *   ③ 变异（mutation）：对生成方案做变换（极端化/反向/组合）出变体
 *
 * 知识强度 radius（作者钦定：AI 打分 + 算法微调）：
 *   底分可外部传入（aiRadius，宿主 AI 打的 0~10），算法按内容特征微调：
 *   radius = clamp(0.5*relevance + 0.3*novelty + 0.2*expressiveness, 1, 10)
 *   ——有规律、不呆板、与质量挂钩（相关性越高、越新鲜、越完整 = 知识越强）
 *
 * 用法：
 *   node innovation_generate.js "<内核>" [--count N] [--no-harvest] [--no-mutation]
 *   node innovation_generate.js --json '{"kernel":"...","aiRadius":7}'
 * 输出：分支 JSON 数组（每条含 name/radius/note/conclusion/score）——可直接
 *       喂 innovation_irregular_circle.js 画圆，或 innovation.js forms 长枝丫
 * ============================================================ */

var fs = require('fs');
var path = require('path');
var sim = require('./similarity.js');
var jaccard = sim.similarity;

var CONFIG_FILE = path.join(__dirname, 'innovation_config.json');
var HARVEST_FILE = path.join(__dirname, 'innovation_harvest.json');

function loadConfig() {
  try { return JSON.parse(fs.readFileSync(CONFIG_FILE, 'utf8')); } catch (e) { return {}; }
}
function loadHarvest() {
  try {
    var d = JSON.parse(fs.readFileSync(HARVEST_FILE, 'utf8'));
    if (Array.isArray(d)) return d;
  } catch (e) { /* 无库 → 空 */ }
  return [];
}

/* ---------- 12 个创新策略模板（面向小白，全中文） ---------- */
var STRATEGIES = [
  { key: 'replace',  name: '换种实现', note: '不纠结在原来的做法上，换一条完全不同的实现路径来达成目标', conc: '换个实现路径，从根上绕开原方案的限制' },
  { key: 'combine',  name: '组合拼装', note: '把两个原本各干各的组件/方法拼在一起，让它们互相补位', conc: 'A+B 组合，各取所长，产生 1+1>2 的效果' },
  { key: 'structure', name: '换数据结构', note: '数据怎么存决定了能怎么算——换一种存储结构，很多难题自动消解', conc: '存储结构一换，问题的复杂度直接降一档' },
  { key: 'cache',    name: '缓存加速', note: '把重复计算/重复读取的结果存下来，热点路径只算一次', conc: '空间换时间，重复劳动大幅减少' },
  { key: 'parallel', name: '并行拆分', note: '把一个大任务拆成几个互不相干的小任务同时干', conc: '多路并进，总耗时压缩' },
  { key: 'precompute', name: '预计算', note: '把以后一定会用到的结果提前算好/准备好，用到时直接拿', conc: '提前把账算完，用的时候就是查表' },
  { key: 'chunk',    name: '分块处理', note: '一口气做不完就切成小块一块块来，每块都能独立检查', conc: '大问题切成小问题，逐个击破' },
  { key: 'model',    name: '换算法模型', note: '换一套底层算法/思路模型，不再按老路推导', conc: '模型一换，结论路径完全不同' },
  { key: 'refactor', name: '重构调用链', note: '不修结果，改"谁调用谁"的结构——让外部输入根本到不了危险环节', conc: '从调用关系上下手，堵住问题的入口' },
  { key: 'reverse',  name: '反向思考', note: '反过来想：不追求"怎样才对"，先想"怎样一定会错"，绕开雷区', conc: '从反方向找约束，避开所有坑就是最优解' },
  { key: 'simplify', name: '简化瘦身', note: '去掉可有可无的部分，只留核心——少即是多', conc: '删掉累赘后，剩下的主干反而更稳更清晰' },
  { key: 'abstract', name: '泛化抽象', note: '把具体问题抽象成通用模型，用现成的通用解法反哺回这个问题', conc: '上升一层看问题，通用框架一接就通' }
];

/* ---------- 启发式自动评分（relevance/novelty/expressiveness/feasibility，0~10） ---------- */
function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }

function round1(v) { return Math.round(v * 10) / 10; }

function scoreBranch(kernel, branch, siblings, harvest) {
  var note = String(branch.note || '');
  // relevance：与内核的相似度（trigram）
  var relevance = Math.round(jaccard(kernel, note) * 10 * 10) / 10;
  // novelty：与同批兄弟方案的最大相似度越低越新鲜（0~10）
  var maxSim = 0;
  siblings.forEach(function (s) {
    if (s === branch) return;
    var sc = jaccard(note, String(s.note || ''));
    if (sc > maxSim) maxSim = sc;
  });
  var novelty = round1(clamp((1 - maxSim) * 10, 0, 10));
  // expressiveness：结构完整度（长度 + 有分隔符/连接词）
  var ex = 3;
  if (note.length >= 10) ex += 3;
  if (note.length >= 20) ex += 2;
  if (/[、，。→，·]|—|—|—/.test(note) || note.indexOf('：') >= 0) ex += 2;
  var expressiveness = clamp(ex, 0, 10);
  // feasibility：可行性关键词命中
  var fe = 4;
  var feKeys = ['替换', '缓存', '拆分', '重构', '组合', '减少', '复用', '并行', '通用', '提前', '抽象', '绕开'];
  feKeys.forEach(function (k) { if (note.indexOf(k) >= 0) fe += 1; });
  var feasibility = clamp(fe, 0, 10);
  var total = round1(relevance * 0.35 + novelty * 0.40 + expressiveness * 0.15 + feasibility * 0.10);
  return { relevance: relevance, novelty: novelty, expressiveness: expressiveness, feasibility: feasibility, total: total };
}

/* ---------- 知识强度：AI 底分 + 算法微调（作者钦定方案） ---------- */
function computeRadius(score, aiRadius) {
  var base = (typeof aiRadius === 'number' && isFinite(aiRadius)) ? aiRadius : 5;
  var adjust = 0.5 * score.relevance + 0.3 * score.novelty + 0.2 * score.expressiveness;
  var r = round1(clamp(base * 0.4 + adjust * 0.6, 1, 10));
  return r;
}

/* ---------- 变异算子：对方案做变换出变体 ---------- */
function mutate(branch, kernel, seed) {
  var note = String(branch.note || '');
  var ops = [
    function (t) { return '把上面的思路推到极端：' + t + '（做到最彻底）'; },
    function (t) { return '反向版本：' + t + '，但步骤全部反过来做一遍'; },
    function (t) { return '组合版本：' + t + '，再叠加「' + kernel.slice(0, 12) + '」这个内核一起考虑'; },
    function (t) { return '把它变简单：' + t + '，只保留最关键的一步'; },
    function (t) { return '跨界版本：' + t + '，再借鉴一个完全无关领域的方法来类比'; },
    function (t) { return '时间尺度版本：' + t + '，但把周期拉长/缩短到极致再想'; },
    function (t) { return '推翻前提版本：' + t + '，先假设现有前提全部是错的再重来'; },
    function (t) { return '最小闭环版本：' + t + '，先做最小可验证的闭环再扩展'; }
  ];
  var op = ops[Math.abs(seed) % ops.length];
  var name = branch.name + '·变体' + ((Math.abs(seed) % 8) + 1);
  return {
    name: name,
    note: op(note),
    conclusion: '变体探索：' + (branch.conclusion || '换个角度再试'),
    _mutated: true
  };
}

/* ---------- 主入口：生成方案分支 ---------- */
function generate(kernel, opts) {
  opts = opts || {};
  var config = loadConfig();
  var features = config.features || {};
  var genCfg = config.generate || {};
  var branches = [];

  if (!kernel || String(kernel).trim().length < 2) {
    return { ok: false, error: '内核文本太短，至少 2 个字' };
  }
  kernel = String(kernel).trim();

  /* ① 策略池 */
  if (features.autoGenerate !== false && genCfg.strategyPool !== false) {
    var pool = STRATEGIES;
    if (opts.count) pool = STRATEGIES.slice(0, Math.max(3, opts.count));
    pool.forEach(function (s) {
      branches.push({
        name: s.name,
        note: s.note + '。「' + kernel.slice(0, 40) + '」' + (kernel.length > 40 ? '…' : ''),
        conclusion: s.conc,
        _strategy: s.key
      });
    });
  }

  /* ② 果实库嫁接（跨域灵感） */
  if (genCfg.harvestGraft !== false) {
    var harvest = loadHarvest();
    harvest.forEach(function (f) {
      if (!f || !f.form || !f.idea) return;
      var rel = jaccard(kernel, String(f.seed || ''));
      if (rel > 0.05) {
        branches.push({
          name: '嫁接·' + f.form,
          note: '灵感嫁接：旧果实「' + String(f.seed || '').slice(0, 16) + '…」曾用 [' + f.form + '] 成功，把它套在新内核上：' + String(f.idea).slice(0, 60),
          conclusion: '跨域嫁接：借旧经验走新路',
          _grafted: true
        });
      }
    });
  }

  /* ③ 变异（只对策略池产物做，避免失控膨胀） */
  if (genCfg.mutation !== false) {
    var base = branches.filter(function (b) { return b._strategy; });
    var count = Math.min(3, base.length);
    for (var i = 0; i < count; i++) {
      branches.push(mutate(base[i], kernel, (kernel.length + i) * 31));
    }
  }

  if (!branches.length) {
    return { ok: false, error: '没生成出任何方案（可能果实库空 + 策略池被关）' };
  }

  /* ④ 自动评分 + 知识强度 */
  var scored = branches.map(function (b) {
    var sc = scoreBranch(kernel, b, branches, loadHarvest());
    b.score = sc;
    b.radius = computeRadius(sc, opts.aiRadius);
    return b;
  });

  /* ⑤ 聚焦加权（进化用）：上一轮最特殊边方向 +0.6 知识强度（不污染内核，温和引导） */
  if (opts.focus) {
    scored.forEach(function (b) {
      var hit = String(b.name).indexOf(opts.focus) >= 0 ||
                (b._strategy && String(opts.focus).indexOf(b._strategy) >= 0) ||
                (opts.focus && String(opts.focus).indexOf(b.name) >= 0);
      if (hit) b.radius = round1(clamp(b.radius + 0.6, 1, 10));
    });
  }

  return { ok: true, kernel: kernel, count: scored.length, branches: scored };
}

/* ---------- CLI ---------- */
function main() {
  var args = process.argv.slice(2);
  var config = loadConfig();
  var defaultCount = ((config.branches || {}).default) || 6;

  if (args.length === 0 || args[0] === '--help' || args[0] === 'help') {
    console.log('🌱 方案生成器 v1.0（创新引擎核心：让 AI 自己会创新）');
    console.log('用法：node innovation_generate.js "<内核>" [--count N] [--no-harvest] [--no-mutation]');
    console.log('      node innovation_generate.js --json \'{"kernel":"...","aiRadius":7}\'');
    console.log('输出：分支 JSON 数组（name/radius/note/conclusion/score），可直接画圆');
    process.exit(0);
  }

  var kernel = null, aiRadius = null, opts = {};
  for (var i = 0; i < args.length; i++) {
    if (args[i] === '--count' && args[i + 1]) {
      var n = parseInt(args[i + 1], 10);
      if (isFinite(n) && n >= 3 && n <= 12) opts.count = n;
      i++;
    } else if (args[i] === '--no-harvest') {
      opts.noHarvest = true;
    } else if (args[i] === '--no-mutation') {
      opts.noMutation = true;
    } else if (args[i] === '--json' && args[i + 1]) {
      try {
        var o = JSON.parse(args[i + 1]);
        kernel = o.kernel; aiRadius = o.aiRadius;
      } catch (e) { /* 忽略 */ }
      i++;
    } else if (kernel === null) {
      kernel = args[i];
    }
  }

  if (!kernel) { console.error('❌ 需要内核文本'); process.exit(1); }
  if (opts.noHarvest) { var c = loadConfig(); c.generate = c.generate || {}; c.generate.harvestGraft = false; try { fs.writeFileSync(CONFIG_FILE, JSON.stringify(c, null, 2)); } catch (e) {} }
  if (opts.noMutation) { var c2 = loadConfig(); c2.generate = c2.generate || {}; c2.generate.mutation = false; try { fs.writeFileSync(CONFIG_FILE, JSON.stringify(c2, null, 2)); } catch (e) {} }

  var res = generate(kernel, { aiRadius: aiRadius });
  if (!res.ok) { console.error('❌ ' + res.error); process.exit(2); }

  console.log('🌱 内核：「' + kernel + '」→ 生成 ' + res.count + ' 个方案分支：');
  res.branches.forEach(function (b, i) {
    console.log('  ' + (i + 1) + '. [' + b.name + '] 知识强度 ' + b.radius + ' · 综合 ' + b.score.total);
    console.log('     ' + String(b.note).slice(0, 80));
  });
  console.log('\n📋 可直接画圆的 JSON（喂 innovation_irregular_circle.js --json）：');
  console.log(JSON.stringify(res.branches.map(function (b) {
    return { name: b.name, radius: b.radius, note: b.note, conclusion: b.conclusion };
  }), null, 2));
}

if (require.main === module) {
  main();
}

module.exports = { generate: generate, STRATEGIES: STRATEGIES, computeRadius: computeRadius, scoreBranch: scoreBranch };
