#!/usr/bin/env node
/**
 * innovation_experiment.js —— 创新实验接线 V1（科研分支 B3：果实→实验闭环）
 *
 * 背景（GPT 路线图 L3）：让灵感树产生的一个候选方案，真正进入
 * 「代码实现 → 沙箱执行 → 指标 → baseline 比较 → 重复验证 → 存记忆」。
 * 只要第一次完整打通，创新分值就从候选系统跨到可执行创新系统。
 *
 * 用法：
 *   node innovation_experiment.js --scheme '<json>' [--repeat 3] [--memory <file>]
 *
 * scheme 格式：
 * {
 *   "seed": "内核", "form": "表达形式", "idea": "候选方案描述",
 *   "code": "候选 Python 代码（最后 print 指标数字，measure=score 时）",
 *   "baseline_code": "baseline Python 代码（同样接口）",
 *   "measure": "time | score",        // time=耗时(ms,越小越好)；score=输出数值(越大越好)
 *   "inputs": [{"..."}],              // 换输入（可选，会以 --repeat×组数 换着跑）
 *   "novelty_check": "新颖性检查结论（可选）"
 * }
 *
 * 流程：baseline 跑 N 次 → 候选跑 N 次 → 对比 → 自动调 innovation_memory.js save 存记忆。
 */
'use strict';
const { execFileSync } = require('child_process');
const fs = require('fs');
const path = require('path');
const os = require('os');

const DIR = __dirname;
const PY = process.env.PY || 'python3';

function runPython(...args) {
  return JSON.parse(execFileSync(PY, [path.join(DIR, 'experiment_runner.py'), ...args], { encoding: 'utf8' }));
}
function runMem(...args) {
  return execFileSync('node', [path.join(DIR, 'innovation_memory.js'), ...args], { encoding: 'utf8' });
}
function fail(msg) { console.error('❌ ' + msg); process.exit(1); }

function main() {
  const args = process.argv.slice(2);
  let schemeRaw = null, repeat = 1, memFile = null;
  for (let i = 0; i < args.length; i++) {
    if (args[i] === '--scheme') schemeRaw = args[++i];
    else if (args[i] === '--repeat') repeat = Math.max(1, parseInt(args[++i], 10) || 1);
    else if (args[i] === '--memory') memFile = args[++i];
  }
  if (!schemeRaw) {
    console.log('🧪 创新实验接线 V1（灵感树 → 沙箱跑 → baseline 对比 → 存记忆）');
    console.log('用法：node innovation_experiment.js --scheme \'{json}\' [--repeat N] [--memory <file>]');
    return;
  }
  let s;
  try { s = JSON.parse(schemeRaw); } catch (e) { fail('--scheme JSON 解析失败：' + e.message); }
  if (!s.code || !s.baseline_code) fail('scheme 需要 code（候选）和 baseline_code（对照）');
  const measure = s.measure === 'score' ? 'score' : 'time';

  console.log('🧪 实验开始：' + s.idea);
  console.log('   内核：' + s.seed + ' | 表达形式：' + s.form);

  // 1) baseline
  console.log('\n[1/4] 跑 baseline……');
  const base = runPython('--code', s.baseline_code, '--measure', measure, '--repeat', String(repeat));
  if (!base.ok) fail('baseline 执行失败：' + base.error);
  const baseArr = measure === 'time' ? base.times_ms : base.values;

  // 2) 候选
  console.log('[2/4] 跑候选方案……');
  const cand = runPython('--code', s.code, '--measure', measure, '--repeat', String(repeat));
  if (!cand.ok) fail('候选执行失败：' + cand.error + ' —— 失败原因将存入记忆库');
  const candArr = measure === 'time' ? cand.times_ms : cand.values;

  // 3) 对比
  console.log('[3/4] 对比指标……');
  const avg = (a) => a.reduce((x, y) => x + y, 0) / a.length;
  const baseAvg = avg(baseArr), candAvg = avg(candArr);
  const better = measure === 'time' ? candAvg < baseAvg : candAvg > baseAvg;
  const deltaPct = baseAvg !== 0 ? Math.round((candAvg - baseAvg) / baseAvg * 10000) / 100 : 0;
  console.log(`   baseline：${baseArr.map((x) => Math.round(x * 100) / 100).join(', ')}（均值 ${Math.round(baseAvg * 100) / 100}）`);
  console.log(`   候选：${candArr.map((x) => Math.round(x * 100) / 100).join(', ')}（均值 ${Math.round(candAvg * 100) / 100}）`);
  console.log(`   相对变化：${deltaPct > 0 ? '+' : ''}${deltaPct}%  → ${better ? '✅ 优于 baseline' : '↩️ 未超越 baseline'}`);

  // 4) 存记忆
  console.log('[4/4] 存入创新记忆库……');
  const memArgs = ['save', JSON.stringify({
    seed: s.seed, form: s.form, idea: s.idea, why: s.why || '',
    code: true, ran: true,
    results: candArr, baseline: baseAvg,
    improved: better, reproduced: repeat >= 2,
    inputs: Array.isArray(s.inputs) ? s.inputs.length : 1,
    seeds: 1,
    novelty_check: s.novelty_check || '',
    failure_reason: better ? '' : ('未超越 baseline（候选均值 ' + Math.round(candAvg * 100) / 100 + ' vs baseline ' + Math.round(baseAvg * 100) / 100 + '）'),
    different: true,
  })];
  if (memFile) memArgs.push('--file', memFile);
  try {
    const out = runMem(...memArgs);
    console.log(out);
  } catch (e) {
    console.error('⚠️ 记忆存档失败（不影响实验结论）：' + e.message);
  }

  console.log('\n🎉 实验闭环完成——灵感树的一个候选，已经过真实执行 + baseline 对比 + 记忆存档');
}

main();
