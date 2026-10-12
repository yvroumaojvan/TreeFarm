#!/usr/bin/env node
'use strict';

/* ============================================================
 * innovation_assess.js —— 问题自评器（AI 自我评估：要不要搜索？）
 * 作者 2026-10-13 钦定功能：让 AI 评估每个问题——
 *   "自己脑子里的资料能不能跟得上？问题复不复杂？能不能自己解决？"
 * 输出「需要搜索的程度」0~100%，三档阈值：
 *   ≤20%   → 直接回答（自己的资料足够）
 *   20~60% → 建议搜索（部分资料可能过时/不足）
 *   ≥60%   → 必须搜索（涉及最新动态/复杂领域，资料跟不上）
 *
 * 零依赖启发式（轻量、手机可跑），四个维度加权：
 *   ① 时间敏感性  0.35（问题含"最新/现在/2026/预测/趋势"等 → 要搜索）
 *   ② 复杂度      0.25（长度 + 多主题连接词 + 开放性问题）
 *   ③ 知识缺口    0.25（1 - 与内置常识库的匹配度）
 *   ④ 事实性      0.15（"多少/何时/是否"等需查证）
 *
 * 用法：
 *   node innovation_assess.js "<问题>"       # 评估一个问题
 *   node innovation_assess.js --json '{...}' # 批量评估
 * 模块：require('./innovation_assess.js').assess(question) → {needSearch, verdict, ...}
 * ============================================================ */

function clamp01(v) { return Math.max(0, Math.min(1, v)); }

function assess(question) {
  if (!question || String(question).trim().length < 2) {
    return { ok: false, error: '问题太短（至少 2 个字）' };
  }
  var q = String(question).trim();

  /* ① 时间敏感性：涉及最新动态/未来趋势 → 必须搜索 */
  var timeWords = ['最新', '现在', '当前', '今年', '2026', '2025', '最近', '趋势', '未来', '预测',
                   '前瞻', '热点', '动态', '现状', '进展', '当下', '前沿', '新发布', '新进展',
                   'latest', 'fresh', 'news', 'update', 'trend'];
  var t = 0;
  timeWords.forEach(function (w) { if (q.indexOf(w) >= 0) t += 0.15; });
  if (/预测|未来/.test(q)) t += 0.1; // 预测类：即使有资料也要最新情报打底
  var timeSensitive = clamp01(t);

  /* ② 复杂度：长度 + 多主题 + 开放性问题 */
  var complexity = 0.3;
  if (q.length >= 10) complexity += 0.15;
  if (q.length >= 25) complexity += 0.2;
  if (q.length >= 50) complexity += 0.2;
  if (/[和与以及、，,vs跟]/.test(q)) complexity += 0.15; // 多主题拼接
  if (/为什么|如何|预测|会不会|怎样|影响|怎么看|机制|原理|对比/.test(q)) complexity += 0.15;
  complexity = clamp01(complexity);

  /* ③ 知识缺口：与内置常识库的匹配度（低匹配 → 资料可能不足 → 要搜索） */
  var commonHi = ['print', '循环', '数组', '列表', '函数', '变量', '字符串', 'python', 'if', 'for', 'while', 'def', 'return', '字典', '运算符', '是什么', '是什么意思', '什么区别'];
  var commonLo = ['你好', '谢谢', '再见', '数学', '加减乘除', '为什么', '怎么写', '怎么用'];
  var maxSim = 0;
  commonHi.forEach(function (k) { if (q.indexOf(k) >= 0) maxSim = Math.max(maxSim, 0.8); });
  commonLo.forEach(function (k) { if (q.indexOf(k) >= 0) maxSim = Math.max(maxSim, 0.5); });
  var knowledge = clamp01(0.15 + maxSim);

  /* ④ 事实性：明确事实问 → 需查证 */
  var factual = /多少|几个|几岁|什么时候|何时|谁|哪里|是否|是不是|多少钱|多长/.test(q) ? 0.7 : 0.4;

  /* 加权 → 需要搜索的程度 0~100 */
  var needSearch = Math.round((timeSensitive * 0.35 + complexity * 0.25 + (1 - knowledge) * 0.25 + factual * 0.15) * 100);

  /* 三档阈值（作者钦定：20% 和 60%） */
  var verdict;
  if (needSearch >= 60) {
    verdict = { action: 'search', label: '🔴 必须搜索', msg: '这问题涉及最新动态/复杂领域，脑子里的资料跟不上——先搜索最新资料再回答' };
  } else if (needSearch >= 20) {
    verdict = { action: 'maybe', label: '🟡 建议搜索', msg: '部分资料可能过时或不足——建议先搜索补充，再决定直接答还是深推' };
  } else {
    verdict = { action: 'answer', label: '🟢 直接回答', msg: '脑子里的资料足够，可以直接回答' };
  }

  return {
    ok: true, question: q,
    needSearch: needSearch,
    timeSensitive: Math.round(timeSensitive * 100) / 100,
    complexity: Math.round(complexity * 100) / 100,
    knowledge: Math.round(knowledge * 100) / 100,
    factual: factual,
    verdict: verdict
  };
}

/* ---------- CLI ---------- */
function main() {
  var args = process.argv.slice(2);
  if (args.length === 0 || args[0] === '--help' || args[0] === 'help') {
    console.log('🧠 问题自评器 v1.0（AI 自我评估：要不要搜索？作者 2026-10-13 钦定）');
    console.log('用法：node innovation_assess.js "<问题>"');
    console.log('输出：需要搜索程度 0~100% + 三档判断（≤20 直接答 / 20-60 建议搜 / ≥60 必须搜）');
    process.exit(0);
  }
  var q = args.join(' ');
  if (args[0] === '--json' && args[1]) {
    try { var o = JSON.parse(args[1]); q = o.question || args[1]; } catch (e) { q = args[1]; }
  }
  var r = assess(q);
  if (!r.ok) { console.error('❌ ' + r.error); process.exit(1); }
  console.log('🧠 问题自评：「' + r.question.slice(0, 60) + (r.question.length > 60 ? '…' : '') + '」');
  console.log('  需要搜索程度：' + r.needSearch + '/100');
  console.log('  维度：时间敏感 ' + Math.round(r.timeSensitive * 100) + '% · 复杂度 ' + Math.round(r.complexity * 100) + '% · 知识缺口 ' + Math.round((1 - r.knowledge) * 100) + '% · 事实性 ' + Math.round(r.factual * 100) + '%');
  console.log('  判断：' + r.verdict.label + ' — ' + r.verdict.msg);
}

if (require.main === module) {
  main();
}

module.exports = { assess: assess };
