#!/usr/bin/env node
'use strict';

/* ============================================================
 * innovation.js —— 灵感树（TreeFarm 创新分支核心引擎）
 * 鱼肉猫卷 2026-09-22 原创理论工程化：让 AI 有「灵光一现」的能力
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
   与思维树弱信号聚类同族——作者原创弱信号检测的工程底座） ---------- */
var sim = require('./similarity.js');
var jaccard = sim.similarity;

/* ---------- 创新引擎新功能（2026-10-13 集成：让 AI 自己会创新） ---------- */
var genMod = require('./innovation_generate.js');
var circleAlgo = require('./innovation_irregular_circle.js');
var generate = genMod.generate;

function loadConfig() {
  try {
    return JSON.parse(fs.readFileSync(path.join(__dirname, 'innovation_config.json'), 'utf8'));
  } catch (e) { return {}; }
}

/* ---------- 命令实现 ---------- */
var s = loadState();

function fail(msg) { console.error('❌ ' + msg); process.exit(1); }

if (!cmd) {
  console.log('灵感树 v1.0（作者原创：内核→表达形式→果实库）');
  console.log('用法：node innovation.js <run|gen|circle|evolve|idea|forms|score|converge|harvest|recall|render|status> [参数]');
  console.log('新功能（2026-10-13）：run "<内核>" 一键完整流程（agent 用）/ gen 自动生成 / circle 画圆 / evolve 进化');
  console.log('例：node innovation.js run "如何让AI拥有灵光一现的能力" 3 --out result.json --html 报告.html');
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
  // r41（GPT 审）：对齐 innovation_score.js 的边界保护——非法数值（非数字/NaN/越界）直接拒绝，防 NaN 评分
  var r = Number(sc.relevance || 0);
  var nv = Number(sc.novelty || 0);
  var ex = Number(sc.expressiveness || 0);
  var fe = Number(sc.feasibility || 0);
  for (var _kv of [['relevance', r], ['novelty', nv], ['expressiveness', ex], ['feasibility', fe]]) {
    if (Number.isNaN(_kv[1]) || !Number.isFinite(_kv[1]) || _kv[1] < 0 || _kv[1] > 10) {
      fail('评分非法：' + _kv[0] + '=' + _kv[1] + ' 必须是 0~10 的数字（GPT 审边界保护）');
    }
  }
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
  // r40：防重复入库（GPT 实测：连续 harvest 两次，同内容果实 1→2）。
  // 用 seed|form|idea 拼接键的 jaccard 相似度判定，>0.9 视为重复。
  var newKey = s.trunk + '|' + bestH.content.form + '|' + bestH.content.idea;
  var dupIdx = -1;
  for (var di = 0; di < h.length; di++) {
    var key = h[di].seed + '|' + h[di].form + '|' + h[di].idea;
    if (jaccard(newKey, key) > 0.9) { dupIdx = di; break; }
  }
  if (dupIdx >= 0) {
    console.log('🚫 重复果实已存在（与库中第 ' + (dupIdx + 1) + ' 颗几乎相同），不重复入库');
    console.log('  已有：[' + h[dupIdx].form + '] ' + h[dupIdx].idea);
    process.exit(0);
  }
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
} else if (cmd === 'gen') {
  /* 一键自动创新：种内核 + 自动生成方案分支 + 自动评分（替代 idea→forms→score 手工三步） */
  var genKernel = null, countOpt = null;
  for (var gi = 1; gi < cmdArgs.length; gi++) {
    var ga = cmdArgs[gi];
    if (ga === '--count' && cmdArgs[gi + 1]) { var cnG = parseInt(cmdArgs[gi + 1], 10); if (isFinite(cnG) && cnG >= 3 && cnG <= 12) countOpt = cnG; gi++; }
    else if (ga === '--mode' && cmdArgs[gi + 1]) { gi++; }
    else if (ga.indexOf('--') === 0) { /* 忽略其它 -- 参数 */ }
    else genKernel = genKernel ? genKernel + ' ' + ga : ga;
  }
  if (!genKernel) fail('gen 需要内核文本');
  var feats = loadConfig().features || {};
  if (feats.autoGenerate === false) fail('自动生成功能已在配置文件里关闭（features.autoGenerate=false，想开就改 innovation_config.json）');
  var g = generate(genKernel, countOpt ? { count: countOpt } : {});
  if (!g.ok) fail(g.error);
  s.trunk = genKernel;
  s.nodes = {};
  s.order = [];
  s.nextId = 2;
  s.nodes.n_1 = { id: 'n_1', parent: null, depth: 0, type: 'trunk',
                  content: genKernel, status: 'expanded', children: [],
                  pruned: false, score: null };
  s.order.push('n_1');
  g.branches.forEach(function (b) {
    var id = 'n_' + s.nextId++;
    s.nodes[id] = { id: id, parent: 'n_1', depth: 1, type: 'form',
                    content: { form: b.name, idea: b.note, why: b.conclusion, radius: b.radius, score: b.score },
                    status: 'scored', children: [], pruned: false, score: b.score };
    s.nodes.n_1.children.push(id);
    s.order.push(id);
  });
  saveState(s);
  var cfgG = loadConfig();
  var isDevG = cfgG.mode === 'dev';
  console.log('🌱 内核已种下并自动长出 ' + g.branches.length + ' 个方案分支（自动生成 + 自动评分）');
  g.branches.forEach(function (b, i) {
    console.log('  ' + (i + 1) + '. [' + b.name + '] 知识强度 ' + b.radius + ' · 综合 ' + b.score.total);
    if (isDevG) console.log('     适配' + b.score.relevance + ' 新颖' + b.score.novelty + ' 表达' + b.score.expressiveness + ' 可行' + b.score.feasibility);
    console.log('     ' + String(b.note).slice(0, 70));
  });
  console.log('下一步：circle 画圆找最特殊边，或 evolve 多轮进化');
} else if (cmd === 'circle') {
  /* 蝴蝶效应不规则圆：对当前已评分分支画圆 → 找最特殊边 → 创新度 */
  if (!s.trunk) fail('还没有内核——先 gen "<内核>"，或 idea + forms + score');
  var featsC = loadConfig().features || {};
  if (featsC.circle === false) fail('不规则圆功能已在配置文件里关闭（features.circle=false，想开就改 innovation_config.json）');
  var formNodes = s.order.filter(function (id) { return s.nodes[id].type === 'form' && s.nodes[id].score && !s.nodes[id].pruned; });
  if (formNodes.length < 3) fail('画圆至少需要 3 个已评分分支（现在 ' + formNodes.length + ' 个）——先 gen 或 forms + score');
  var branchesC = formNodes.map(function (id) {
    var n = s.nodes[id];
    return { name: n.content.form, radius: Number(n.content.radius) || Math.max(1, Math.min(10, n.score.total)), note: n.content.idea, conclusion: n.content.why };
  });
  var cfgC = loadConfig();
  var paramsC = cfgC.circle || {};
  var cr = circleAlgo.analyze(branchesC, paramsC);
  if (!cr.ok) fail(cr.error);
  var taskC = { currentRound: (cfgC.task && cfgC.task.currentRound) || 1, totalRounds: (cfgC.task && cfgC.task.totalRounds) || 1 };
  var outFileC = 'circle_result.json';
  var oiC = args.indexOf('--out');
  if (oiC >= 0 && args[oiC + 1]) outFileC = args[oiC + 1];
  var toSaveC = Object.assign({ task: taskC, ok: true }, cr);
  fs.writeFileSync(outFileC, JSON.stringify(toSaveC, null, 2), 'utf8');
  var isDevC = cfgC.mode === 'dev';
  var keyC = cr.conclusions.keyBranch;
  var innC = cr.innovation;
  console.log('🐛 不规则圆分析完成（' + cr.meta.N + ' 条边）→ 已存 ' + outFileC);
  console.log('  创新度：' + innC.score + '/100 · ' + innC.level);
  if (isDevC) console.log('  CV=' + innC.cv.toFixed(3) + ' · 锐化角 ' + innC.sharp + ' · z阈值=' + (paramsC.zThreshold || 1.2));
  console.log('  最特殊边：「' + keyC.name + '」（知识强度 ' + keyC.radius + '）');
  console.log('    → ' + keyC.note);
  console.log('  其他边（简略）：' + cr.conclusions.others.map(function (o) { return o.name; }).slice(0, 4).join('、') + '…');
  console.log('  整轮总结：' + cr.conclusions.summary);
  if ((cfgC.features || {}).exportHtml !== false) {
    console.log('  📄 导出报告页（任务快照，只读）：node innovation_circle_view.js ' + outFileC);
  }
} else if (cmd === 'evolve') {
  /* 多轮进化 = 创新组合引擎：每轮 gen→画圆→聚焦最特殊边，R 轮后重组最不规则圆 */
  if (!rest) fail('evolve 需要内核文本');
  var featsE = loadConfig().features || {};
  if (featsE.evolve === false) fail('多轮进化功能已在配置文件里关闭（features.evolve=false，想开就改 innovation_config.json）');
  var R = parseInt(cmdArgs[2], 10);
  if (!isFinite(R) || R < 1 || R > 50) R = (loadConfig().evolve || {}).rounds || 3;
  var kernelE = cmdArgs[1] || rest;
  var countE = null;
  for (var ei = 3; ei < cmdArgs.length; ei++) {
    if (cmdArgs[ei] === '--count' && cmdArgs[ei + 1]) { var cnE = parseInt(cmdArgs[ei + 1], 10); if (isFinite(cnE) && cnE >= 3 && cnE <= 12) countE = cnE; ei++; }
  }
  var prevName = null, repeatLockE = 0;
  var roundsE = [];
  for (var ri = 1; ri <= R; ri++) {
    var gE = generate(kernelE, Object.assign({ count: countE }, prevName ? { focus: prevName } : {}));
    if (!gE.ok) fail(gE.error);
    var bsE = gE.branches.map(function (b) { return { name: b.name, radius: b.radius, note: b.note, conclusion: b.conclusion }; });
    var cE = circleAlgo.analyze(bsE, (loadConfig().circle || {}));
    if (!cE.ok) fail(cE.error);
    var keyE = cE.conclusions.keyBranch;
    if (prevName === keyE.name) { repeatLockE++; } else { repeatLockE = 0; }
    if (repeatLockE >= 3) { prevName = null; repeatLockE = 0; } // 死锁保护：连续3轮同方向→强制自由探索
    else { prevName = keyE.name; }
    roundsE.push({ round: ri, kernel: kernelE, count: gE.count, special: keyE, innovation: cE.innovation, meta: cE.meta });
  }
  var isDevE = loadConfig().mode === 'dev';
  console.log('🦋 蝴蝶效应 · 多轮进化完成（' + R + ' 轮）');
  roundsE.forEach(function (r) {
    console.log('  第' + r.round + '轮 · ' + r.count + ' 条边 · 创新度 ' + r.innovation.score + (isDevE ? '（CV=' + r.innovation.cv.toFixed(3) + ' 锐角 ' + r.innovation.sharp + '）' : ''));
    console.log('    最特殊边：「' + r.special.name + '」（知识强度 ' + r.special.radius + '）→ ' + String(r.special.note).slice(0, 50));
  });
  console.log('\n🎯 最终「最不规则圆」= ' + R + ' 轮最特殊边重组：');
  roundsE.forEach(function (r, i) {
    console.log('  ' + (i + 1) + '. [' + r.special.name + '] 知识强度 ' + r.special.radius + ' — ' + String(r.special.note).slice(0, 60));
  });
  var avgScore = Math.round(roundsE.reduce(function (a, r) { return a + r.innovation.score; }, 0) / R);
  console.log('  平均创新度 ' + avgScore + '/100 —— 每轮聚焦上一轮最野的边，圆越来越不规整 = 创新持续放大 🐛');
  var outFileE = 'evolve_result.json';
  var oiE = args.indexOf('--out');
  if (oiE >= 0 && args[oiE + 1]) outFileE = args[oiE + 1];
  fs.writeFileSync(outFileE, JSON.stringify({ task: { currentRound: R, totalRounds: R }, ok: true, rounds: roundsE, avgInnovation: avgScore }, null, 2), 'utf8');
  console.log('  📦 已存 ' + outFileE);
  if ((loadConfig().features || {}).exportHtml !== false) {
    console.log('  📄 导出报告页：node innovation_circle_view.js ' + outFileE + '（想要单轮快照图就先跑 circle --out xx.json）');
  }
} else if (cmd === 'run') {
  /* agent 一键完整流程：生成 → 画圆 → 多轮进化 → 结构化结果（SKILL 主入口） */
  if (!rest) fail('run 需要内核文本');
  var cfgR = loadConfig();
  var kernelR = cmdArgs[1] || rest;
  var Rr = parseInt(cmdArgs[2], 10);
  if (!isFinite(Rr) || Rr < 1 || Rr > 50) Rr = (cfgR.evolve || {}).rounds || 3;
  var outJson = null, outHtml = null, modeR = cfgR.mode || 'simple', countR = null;
  for (var ri2 = 3; ri2 < cmdArgs.length; ri2++) {
    var aR = cmdArgs[ri2];
    if (aR === '--out' && cmdArgs[ri2 + 1]) { outJson = cmdArgs[++ri2]; }
    else if (aR === '--html' && cmdArgs[ri2 + 1]) { outHtml = cmdArgs[++ri2]; }
    else if (aR === '--mode' && cmdArgs[ri2 + 1]) { modeR = cmdArgs[++ri2]; }
    else if (aR === '--count' && cmdArgs[ri2 + 1]) { var cnR = parseInt(cmdArgs[++ri2], 10); if (isFinite(cnR) && cnR >= 3 && cnR <= 12) countR = cnR; }
  }
  var prevNameR = null, roundsR = [], repeatLockR = 0;
  for (var rri = 1; rri <= Rr; rri++) {
    var gR = generate(kernelR, { count: countR, focus: prevNameR });
    if (!gR.ok) fail(gR.error);
    var cR = circleAlgo.analyze(gR.branches, cfgR.circle || {});
    if (!cR.ok) fail(cR.error);
    var keyR = cR.conclusions.keyBranch;
    if (prevNameR === keyR.name) { repeatLockR++; } else { repeatLockR = 0; }
    if (repeatLockR >= 3) { prevNameR = null; repeatLockR = 0; } // 死锁保护：连续3轮同方向→强制自由探索
    else { prevNameR = keyR.name; }
    roundsR.push({ round: rri, kernel: kernelR, count: gR.count, branches: gR.branches, special: keyR, innovation: cR.innovation, circle: cR });
  }
  var avgR = Math.round(roundsR.reduce(function (a, r) { return a + r.innovation.score; }, 0) / Rr);
  var finalR = {
    specialEdges: roundsR.map(function (r) { return { name: r.special.name, radius: r.special.radius, note: r.special.note }; }),
    avgInnovation: avgR,
    summary: '共 ' + Rr + ' 轮进化，每轮聚焦上一轮最特殊边，最终最不规则圆 = ' + roundsR.map(function (r) { return r.special.name; }).join('、') + '。平均创新度 ' + avgR + '/100（偏离规则越多 = 创新越多）。'
  };
  var resultR = { ok: true, task: { currentRound: Rr, totalRounds: Rr }, kernel: kernelR, mode: modeR, rounds: roundsR, final: finalR };
  if (outJson) fs.writeFileSync(outJson, JSON.stringify(resultR, null, 2), 'utf8');
  if (modeR === 'dev') {
    console.log('🧠 开发者模式全量输出（agent 可读）：');
    console.log(JSON.stringify(resultR, null, 2));
  } else {
    console.log('🌱 创新引擎 · 完整流程完成（' + Rr + ' 轮进化）');
    roundsR.forEach(function (r) {
      console.log('  第' + r.round + '轮 · ' + r.count + ' 个方案 · 创新度 ' + r.innovation.score + ' · 最特殊边「' + r.special.name + '」（知识强度 ' + r.special.radius + '）');
    });
    console.log('🎯 最终最不规则圆（' + Rr + ' 轮最特殊边重组）：');
    finalR.specialEdges.forEach(function (s, i) {
      console.log('  ' + (i + 1) + '. [' + s.name + '] 知识强度 ' + s.radius + ' — ' + String(s.note).slice(0, 50));
    });
    console.log('  平均创新度 ' + avgR + '/100');
    console.log('  ' + finalR.summary);
    if (outJson) console.log('📦 结构化结果（agent 可读）→ ' + outJson);
  }
  if (outHtml) {
    var lastC = roundsR[Rr - 1].circle;
    var viewIn = path.join(__dirname, '.run_circle_tmp.json');
    fs.writeFileSync(viewIn, JSON.stringify(Object.assign({ task: resultR.task }, lastC), null, 2), 'utf8');
    var cp = require('child_process');
    cp.execFileSync('node', [path.join(__dirname, 'innovation_circle_view.js'), viewIn, outHtml], { stdio: 'pipe', encoding: 'utf8' });
    try { fs.unlinkSync(viewIn); } catch (e) {}
    console.log('📄 报告页（最后一轮快照）→ ' + outHtml);
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
