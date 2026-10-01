#!/usr/bin/env node
'use strict';

/* ============================================================
 * innovation.js —— 灵感树（TreeFarm 创新分支核心引擎）
 * 乖宝 2026-09-22 原创理论工程化：让 AI 有「灵光一现」的能力
 *
 * 理论：AI 创新链 = 内核 → 召唤表达形式 → 载体 = 创新成果
 *   （拆掉"先有感受"的前提，AI 的"感觉"就是"这段内核该用什么形式表达"）
 *
 * 命令（全部零依赖，Node 直接跑）：
 *   idea <内核文本>              种下内核种子（开始一次创新）
 *   forms <表达形式分支JSON>      长枝丫：每个分支=一种表达形式候选
 *   score <节点id> <评分JSON>     双维评分（relevance/novelty 权重最高）
 *   converge                     摘果：选出最优表达 = 创新成果
 *   harvest                      结成果实：存入果实库（可复用）
 *   recall <内核文本>             相似果实联想（新种子→旧果实组合=灵感）
 *   render                       画树
 *   status                       看状态
 *   --state <file>               状态文件（默认 .innovation_state.json）
 *   --harvest <file>             果实库文件（默认 innovation_harvest.json）
 *
 * 果实库：innovation_harvest.json —— 灵感存下来、整理好、以后还能再用
 *   [{seed, form, score, time}]  相似度用 trigram Jaccard（与弱信号聚类同族）
 * ============================================================ */

var fs = require('fs');
var path = require('path');

var DEFAULT_STATE = path.join(__dirname, '.innovation_state.json');
var DEFAULT_HARVEST = path.join(__dirname, 'innovation_harvest.json');

/* ---------- 参数解析 ---------- */
var args = process.argv.slice(2);
var stateFile = DEFAULT_STATE;
var harvestFile = DEFAULT_HARVEST;
var cmdArgs = [];
for (var i = 0; i < args.length; i++) {
  if (args[i] === '--state' && i + 1 < args.length) { stateFile = args[++i]; }
  else if (args[i] === '--harvest' && i + 1 < args.length) { harvestFile = args[++i]; }
  else { cmdArgs.push(args[i]); }
}
var cmd = cmdArgs[0];
var rest = cmdArgs.slice(1).join(' ');

/* ---------- 状态存取 ---------- */
function loadState() {
  try {
    var raw = fs.readFileSync(stateFile, 'utf8');
    var d = JSON.parse(raw);
    if (d && d.version) return d;
  } catch (e) { /* 损坏/空状态 → 新建 */ }
  return { version: 1, trunk: null, nodes: {}, order: [], nextId: 2, mode: 'innovation' };
}
function saveState(s) {
  fs.writeFileSync(stateFile, JSON.stringify(s, null, 2));
}
function loadHarvest() {
  try {
    var d = JSON.parse(fs.readFileSync(harvestFile, 'utf8'));
    if (Array.isArray(d)) return d;
  } catch (e) { /* 无库 → 空 */ }
  return [];
}
function saveHarvest(h) {
  fs.writeFileSync(harvestFile, JSON.stringify(h, null, 2));
}

/* ---------- 相似度（公共模块 similarity.js：trigram Jaccard + bigram 余弦 max，
   与思维树弱信号聚类同族——乖宝原创弱信号检测的工程底座） ---------- */
var sim = require('./similarity.js');
var jaccard = sim.similarity;

/* ---------- 命令实现 ---------- */
var s = loadState();

function fail(msg) { console.error('❌ ' + msg); process.exit(1); }

if (!cmd) {
  console.log('灵感树 v1.0（乖宝原创：内核→表达形式→果实库）');
  console.log('用法：node innovation.js <idea|forms|score|converge|harvest|recall|render|status> [参数]');
  console.log('例：node innovation.js idea "如何让AI拥有灵光一现的能力"');
  process.exit(0);
}

if (cmd === 'idea') {
  if (!rest) fail('idea 需要内核文本');
  s.trunk = rest;
  s.nodes = {};
  s.order = ['n_1'];
  s.nextId = 2;
  s.nodes.n_1 = { id: 'n_1', parent: null, depth: 0, type: 'trunk',
                  content: rest, status: 'expanded', children: [],
                  pruned: false, score: null };
  saveState(s);
  console.log('🌱 内核已种下：' + rest);
  console.log('下一步：forms "表达形式分支JSON"（每个分支=一种表达形式候选）');
} else if (cmd === 'forms') {
  if (!s.trunk) fail('还没有内核，先 idea');
  if (!cmdArgs[1]) fail('forms 需要分支JSON：[{form,idea,why}]（form=表达形式，idea=怎么用这个形式，why=为什么适配内核）');
  var branches;
  try { branches = JSON.parse(cmdArgs[1]); } catch (e) { fail('JSON 解析失败：' + e.message); }
  if (!Array.isArray(branches) || !branches.length) fail('分支必须是数组且非空');
  var parentId = cmdArgs[2] || 'n_1';
  if (!s.nodes[parentId]) fail('节点 ' + parentId + ' 不存在');
  var kids = [];
  branches.forEach(function (b) {
    var id = 'n_' + s.nextId++;
    s.nodes[id] = { id: id, parent: parentId, depth: s.nodes[parentId].depth + 1,
                    type: 'form', content: { form: b.form, idea: b.idea, why: b.why },
                    status: 'pending', children: [], pruned: false, score: null };
    kids.push(id);
    s.order.push(id);
  });
  s.nodes[parentId].children = s.nodes[parentId].children.concat(kids);
  s.nodes[parentId].status = 'expanded';
  saveState(s);
  console.log('🌿 长出 ' + kids.length + ' 种表达形式枝丫：');
  kids.forEach(function (id) {
    var n = s.nodes[id];
    console.log('  ' + id + ' [' + n.content.form + '] → ' + n.content.idea);
  });
  console.log('下一步：逐个 score 评分（relevance/novelty 双维，novelty 权重最高）');
} else if (cmd === 'score') {
  var sid = cmdArgs[1];
  if (!sid || !s.nodes[sid]) fail('score 需要有效的节点 id');
  var sc;
  try { sc = JSON.parse(cmdArgs[2]); } catch (e) { fail('评分JSON解析失败：' + e.message); }
  // 创新模式评分：relevance(适配内核) 35% + novelty(新颖度) 40% + expressiveness(表达力) 15% + feasibility(可实现) 10%
  var r = Number(sc.relevance || 0);
  var nv = Number(sc.novelty || 0);
  var ex = Number(sc.expressiveness || 0);
  var fe = Number(sc.feasibility || 0);
  var total = r * 0.35 + nv * 0.40 + ex * 0.15 + fe * 0.10;
  s.nodes[sid].score = { relevance: r, novelty: nv, expressiveness: ex, feasibility: fe, total: Math.round(total * 100) / 100 };
  s.nodes[sid].status = 'scored';
  saveState(s);
  console.log('⭐ ' + sid + ' 评分：适配 ' + r + ' · 新颖 ' + nv + ' · 表达 ' + ex + ' · 可行 ' + fe + ' → 综合 ' + s.nodes[sid].score.total);
} else if (cmd === 'converge') {
  var scored = s.order.filter(function (id) { return s.nodes[id].score && !s.nodes[id].pruned; });
  if (!scored.length) fail('还没有评分节点，先 score');
  scored.sort(function (a, b) { return s.nodes[b].score.total - s.nodes[a].score.total; });
  var best = scored[0];
  var n = s.nodes[best];
  console.log('🍎 摘果完成！最优表达形式：');
  console.log('  [' + n.content.form + ']');
  console.log('  内核：' + s.trunk);
  console.log('  表达：' + n.content.idea);
  console.log('  为什么适配：' + n.content.why);
  console.log('  综合分：' + n.score.total + '（适配 ' + n.score.relevance + ' / 新颖 ' + n.score.novelty + '）');
  console.log('下一步：harvest 存入果实库，或 recall 联想旧果实');
} else if (cmd === 'harvest') {
  var scoredH = s.order.filter(function (id) { return s.nodes[id].score && !s.nodes[id].pruned; });
  if (!scoredH.length) fail('还没有评分节点，先 score + converge');
  scoredH.sort(function (a, b) { return s.nodes[b].score.total - s.nodes[a].score.total; });
  var bestH = s.nodes[scoredH[0]];
  var h = loadHarvest();
  h.push({ seed: s.trunk, form: bestH.content.form, idea: bestH.content.idea,
           why: bestH.content.why, score: bestH.score.total, time: new Date().toISOString() });
  saveHarvest(h);
  console.log('📦 灵感果实已入库！果实库现有 ' + h.length + ' 颗果实');
  console.log('  果实：[' + bestH.content.form + '] ' + bestH.content.idea);
} else if (cmd === 'recall') {
  if (!rest) fail('recall 需要内核文本');
  var h = loadHarvest();
  if (!h.length) { console.log('🍂 果实库还是空的——先 idea → forms → score → converge → harvest 结出第一颗果实吧'); process.exit(0); }
  var ranked = h.map(function (f, idx) {
    return { f: f, sim: jaccard(rest, f.seed), idx: idx };
  }).filter(function (x) { return x.sim > 0.05; })
    .sort(function (a, b) { return b.sim - a.sim; })
    .slice(0, 3);
  if (!ranked.length) {
    console.log('🍂 没有相似的旧果实。可以试着用不同说法描述内核，或先 harvest 新果实。');
  } else {
    console.log('🔗 灵光一现！找到 ' + ranked.length + ' 颗相似果实可以串起来：');
    ranked.forEach(function (x) {
      console.log('  [相似度 ' + Math.round(x.sim * 100) + '%] 内核「' + x.f.seed.slice(0, 20) + '」');
      console.log('      → 曾用形式 [' + x.f.form + ']：' + x.f.idea.slice(0, 40));
    });
    console.log('  提示：把旧果实的表达形式，套在现在这颗新内核上试试——这就是灵感');
  }
} else if (cmd === 'render') {
  if (!s.trunk) fail('还没有内核');
  console.log('🌳 灵感树 · 内核：' + s.trunk);
  function draw(id, prefix) {
    var n = s.nodes[id];
    if (!n) return;
    var label = n.type === 'trunk' ? '🌱 内核' : '🌿 ' + (n.content.form || '');
    var sc = n.score ? ' ⭐' + n.score.total : (n.status === 'pending' ? ' ⏳' : '');
    var pruned = n.pruned ? ' ✂️' : '';
    console.log(prefix + (n.parent ? '├─ ' : '└─ ') + label + sc + pruned);
    n.children.forEach(function (c, i) {
      draw(c, prefix + (n.parent ? '│  ' : '   ') + (i === n.children.length - 1 ? '   ' : '│  '));
    });
  }
  draw('n_1', '');
} else if (cmd === 'status') {
  var totalN = s.order.length;
  var scoredN = s.order.filter(function (id) { return s.nodes[id].score; }).length;
  var hN = loadHarvest().length;
  console.log('📋 灵感树状态 · 内核: ' + (s.trunk ? '已种下' : '空') +
              ' · 枝丫 ' + totalN + ' · 已评分 ' + scoredN + ' · 果实库 ' + hN + ' 颗');
} else {
  fail('未知命令：' + cmd + '（可用 idea/forms/score/converge/harvest/recall/render/status）');
}
