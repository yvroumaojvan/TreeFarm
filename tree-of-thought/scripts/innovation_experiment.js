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
function fail(msg) { console.error('❌ ' + msg); process.exit(2); } // 参数/环境错误 → exit 2

/**
 * v0.6 结果比较器（GPT 审第六份）：JSON.stringify 对 dict 键序敏感（{"a":1,"b":2} 与
 * {"b":2,"a":1} 语义等价但字符串不等），set 等也不能直接序列化。
 * 递归规范化：对象按键排序后再比较，数组递归，标量原样。
 */
function normalizeResult(v) {
  if (Array.isArray(v)) return v.map(normalizeResult);
  if (v !== null && typeof v === 'object') {
    const out = {};
    for (const k of Object.keys(v).sort()) out[k] = normalizeResult(v[k]);
    return out;
  }
  return v;
}
function resultsEqual(a, b) {
  return JSON.stringify(normalizeResult(a)) === JSON.stringify(normalizeResult(b));
}

/**
 * v0.9 条件唯一性（GPT 审第九份）：`inputs = [1, 1]` 只是同一条件 ×2，不是两个不同条件。
 * 用键序无关比较器去重，返回真实独立条件数——评分器只认这个自证数值，不认 inputs 计数。
 */
function countDistinctCondition(inputs) {
  const keys = new Set();
  for (const inp of inputs) {
    const args = (inp && typeof inp === 'object' && 'args' in inp) ? inp.args : inp;
    keys.add(JSON.stringify(normalizeResult(args)));
  }
  return keys.size;
}

/**
 * 多输入 workload 实验（GPT 审第五/六份）：换输入必须是真的——每组输入都喂给
 * baseline 和候选（完全一样的输入），输出必须一致，指标=每输入耗时中位数。
 * variant_verified 只有真实跑完 ≥2 组输入且全部公平比较时才算真。
 * repeat 真正生效：每输入 warmup 1 次 + repeat 次正式测量（输入维度×重复维度分开）。
 */
function runWorkloadExperiment(s, repeat, memFile) {
  const func = s.func || 'f';
  const inputs = s.inputs;
  const n = inputs.length;
  const avg = (a) => (a.length ? a.reduce((x, y) => x + y, 0) / a.length : 0);
  console.log('\n[1/4] 跑 baseline workload（真实执行 ' + func + '(输入) ×' + n + ' 组 ×' + repeat + ' 次测量）……');
  const base = runPython('--workload', JSON.stringify({ code: s.baseline_code, func, inputs, repeat }));
  if (!base.ok) fail('baseline 执行失败：' + base.error);
  const baseRows = base.per_input;

  console.log('[2/4] 跑候选 workload（同样 ' + n + ' 组输入 ×' + repeat + ' 次）……');
  let cand = null, candErr = '';
  try { cand = runPython('--workload', JSON.stringify({ code: s.code, func, inputs, repeat })); }
  catch (e) { candErr = e.message; }
  if (!cand || !cand.ok) {
    const err = candErr || (cand && cand.error) || '未知错误';
    console.log('   候选执行失败：' + err + ' —— 失败原因存入记忆库');
    const memArgs = ['save', JSON.stringify({
      seed: s.seed, form: s.form, idea: s.idea, why: s.why || '',
      code: true, ran: false, improved: false,
      failure_reason: '候选执行失败：' + err,
      results: [], baseline: undefined,
      inputs: n, seeds: 1,
      different: s.different !== undefined ? s.different : undefined,
    })];
    if (memFile) memArgs.push('--file', memFile);
    try { console.log(runMem(...memArgs)); } catch (e2) { console.error('⚠️ 失败记忆存档异常：' + e2.message); }
    console.log('\n❌ 实验中止（候选失败），失败原因已记录。');
    process.exit(1); // v0.6：实验失败 → exit 1（机器可识别，不被 CI 当成成功）
  }
  const candRows = cand.per_input;

  // 3) 公平对比：输出必须一致（结果相等，用键序无关比较器）+ 每输入耗时中位数
  console.log('[3/4] 公平对比（同一输入，输出必须一致，比耗时中位数）……');
  const details = [];
  let consistent = true, allRan = true;
  for (let i = 0; i < n; i++) {
    const b = baseRows[i], c = candRows[i];
    const bOk = !!(b && b.ok), cOk = !!(c && c.ok);
    if (bOk && cOk) {
      const same = resultsEqual(b.result, c.result);
      if (!same) consistent = false;
      const bMs = b.ms || 0, cMs = c.ms || 0;
      details.push({ i, same, bMs, cMs, better: cMs < bMs, cMsAll: c.ms_all, bMsAll: b.ms_all });
    } else {
      allRan = false; consistent = false;
      details.push({ i, same: false, note: (bOk ? '' : 'baseline执行失败 ') + (cOk ? '' : '候选执行失败') });
    }
  }
  const bAvg = Math.round(avg(details.filter((d) => d.bMs !== undefined).map((d) => d.bMs)) * 100) / 100;
  const cAvg = Math.round(avg(details.filter((d) => d.cMs !== undefined).map((d) => d.cMs)) * 100) / 100;
  const betterCount = details.filter((d) => d.better).length;
  // v0.7（GPT 审第七份）：成功判定统一——2/3 输入更快 且 总体均值也更小，
  // 否则"输入3极慢但 2/3 输入快"这种边界会被误判为超越（均值语义与逐输入语义分裂）
  const better = allRan && consistent && betterCount >= Math.ceil(n * 2 / 3) && cAvg < bAvg;
  details.forEach((d) => {
    if (d.bMs !== undefined) {
      console.log(`   输入${d.i + 1}：baseline ${Math.round(d.bMs * 100) / 100}ms vs 候选 ${Math.round(d.cMs * 100) / 100}ms  ${d.same ? (d.better ? '✅' : '↩️') : '❌输出不一致'}`);
    } else {
      console.log('   输入' + (d.i + 1) + '：⚠️ ' + (d.note || ''));
    }
  });
  console.log('   输出一致性：' + (consistent ? '✅ 全部一致' : '❌ 存在不一致'));
  if (allRan) {
    console.log(`   均值：baseline ${bAvg}ms vs 候选 ${cAvg}ms → ${better ? '✅ 优于 baseline（' + betterCount + '/' + n + ' 输入同向）' : '↩️ 未超越 baseline'}`);
  }
  if (!allRan || !consistent) {
    console.log('   ❌ 输出不一致/未跑全——不算超越 baseline（GPT 审第五份：公平输入+结果相等是实验前提）');
    const memArgs = ['save', JSON.stringify({
      seed: s.seed, form: s.form, idea: s.idea, why: s.why || '',
      code: true, ran: true, improved: false,
      failure_reason: '实验无效：' + (allRan ? '候选输出与 baseline 不一致' : '部分输入执行失败') +
        (allRan ? '' : '（' + details.filter((d) => d.note).map((d) => '输入' + (d.i + 1)).join('、') + '）'),
      results: details.filter((d) => d.cMs !== undefined).map((d) => d.cMs),
      baseline: bAvg,
      inputs: n, seeds: 1,
      different: s.different !== undefined ? s.different : undefined,
    })];
    if (memFile) memArgs.push('--file', memFile);
    try { console.log(runMem(...memArgs)); } catch (e2) { console.error('⚠️ 记忆存档异常：' + e2.message); }
    console.log('\n❌ 实验中止（公平性未满足），原因已记录。');
    process.exit(1); // v0.6：实验无效 → exit 1（机器可识别）
  }

  // 4) 存记忆（variant_verified = 真实跑完 ≥2 组输入且全部一致；improved = 真实超越 baseline）
  console.log('[4/4] 存入创新记忆库……');
  // v0.9：独立条件数必须由数据自证（[1,1] 算 1 个条件，不能冒充跨条件）
  const distinct = countDistinctCondition(inputs);
  if (distinct < n) {
    console.log('   ⚠️ 检测到重复条件：' + n + ' 个输入只有 ' + distinct + ' 个独立条件（跨条件证据按 ' + distinct + ' 算）');
  }
  const memArgs = ['save', JSON.stringify({
    seed: s.seed, form: s.form, idea: s.idea, why: s.why || '',
    code: true, ran: true,
    results: details.map((d) => d.cMs),
    baseline: bAvg,
    // GPT 审第五份：reproduced 需要跨条件重复声明；variant_verified 由真实多输入证明
    reproduced: false,
    repeated_same_input: false,
    variant_verified: n >= 2 && allRan && consistent,
    inputs: n, seeds: 1,
    // v0.9：真实独立条件数（[1,1]→1，[1,2]→2）——评分器只认这个
    distinct_inputs: distinct,
    repeats: repeat, // v0.6：每输入重复测量次数（输入维度 × 重复维度分开）
    // v0.7：原始重复测量全量保存（每输入 ms_all），不压扁证据链（GPT 审第七份）
    ms_all: details.map((d) => d.cMsAll),
    // v0.9（GPT 审第九份）：baseline 原始证据全量入库——审计时能复核 baseline 自身没偶然变慢
    baseline_ms_all: details.map((d) => d.bMsAll),
    baseline_ms: details.map((d) => d.bMs),
    candidate_mean: cAvg,
    novelty_check: s.novelty_check || '',
    improved: better, // v0.6：L4 与「成功」绑定——未超越 baseline 不得进 L4
    failure_reason: better ? '' : ('未超越 baseline（候选均值 ' + cAvg + ' vs baseline ' + bAvg + '）'),
    different: s.different !== undefined ? s.different : undefined,
  })];
  if (memFile) memArgs.push('--file', memFile);
  try { console.log(runMem(...memArgs)); } catch (e) { console.error('⚠️ 记忆存档失败（不影响实验结论）：' + e.message); }
  console.log('\n🎉 实验闭环完成——多输入公平实验（' + n + ' 组 / ' + distinct + ' 独立条件）已执行并存入记忆');
}

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

  // 0) 正确性验证（GPT 审 #6：print(999999) 冒充结果必须被拦）——
  //    scheme 提供 test_cases[{input,expected}] + func 时，指标比较前先验证候选正确性
  if (s.func && Array.isArray(s.test_cases) && s.test_cases.length) {
    console.log('\n[0/5] 验证候选正确性（test_cases ×' + s.test_cases.length + '）……');
    let v;
    try {
      v = runPython('--code', s.code, '--verify', JSON.stringify({ func: s.func, cases: s.test_cases }));
    } catch (e) {
      try { v = JSON.parse(e.stdout); } catch (_) { v = { ok: false, passed: 0, total: 0, error: e.message }; }
    }
    if (!v.ok) {
      console.log('   ❌ 候选正确性未通过：' + (v.passed || 0) + '/' + (v.total || 0) + ' 用例（错误用例：' + JSON.stringify(v.failures || []) + '）');
      console.log('   —— 错误方案比 baseline 再快也不算数，失败原因存入记忆库');
      const memArgs = ['save', JSON.stringify({
        seed: s.seed, form: s.form, idea: s.idea, why: s.why || '',
        code: true, ran: false, improved: false,
        failure_reason: '候选正确性未通过（' + (v.passed || 0) + '/' + (v.total || 0) + '）：' + JSON.stringify(v.failures || []),
        results: [], baseline: undefined,
        inputs: 0, seeds: 0,
        different: s.different !== undefined ? s.different : undefined,
      })];
      if (memFile) memArgs.push('--file', memFile);
      try { console.log(runMem(...memArgs)); } catch (e2) { console.error('⚠️ 记忆存档异常：' + e2.message); }
      console.log('\n❌ 实验中止（候选不正确），失败原因已记录。');
      process.exit(1); // v0.6：实验失败 → exit 1（机器可识别）
    }
    console.log('   ✅ 正确性通过：' + v.passed + '/' + v.total + ' 用例全部正确');
  }

  // 1) 多输入 workload 实验（GPT 审第五份：换输入必须真实执行，不再是数据字段）
  if (Array.isArray(s.inputs) && s.inputs.length) {
    runWorkloadExperiment(s, repeat, memFile);
    return;
  }

  // 1) baseline
  console.log('\n[1/4] 跑 baseline……');
  const base = runPython('--code', s.baseline_code, '--measure', measure, '--repeat', String(repeat));
  if (!base.ok) fail('baseline 执行失败：' + base.error);
  const baseArr = measure === 'time' ? base.times_ms : base.values;

  // 2) 候选（GPT 审：失败也必须存记忆——失败数据比成功更有价值）
  console.log('[2/4] 跑候选方案……');
  let cand = null, candErr = '';
  try {
    cand = runPython('--code', s.code, '--measure', measure, '--repeat', String(repeat));
  } catch (e) { candErr = e.message; }
  if (!cand || !cand.ok) {
    const err = candErr || (cand && cand.error) || '未知错误';
    console.log('   候选执行失败：' + err + ' —— 失败原因存入记忆库');
    const memArgs = ['save', JSON.stringify({
      seed: s.seed, form: s.form, idea: s.idea, why: s.why || '',
      code: true, ran: false, improved: false,
      failure_reason: '候选执行失败：' + err,
      results: [], baseline: undefined,
      inputs: 0, seeds: 0, novel: s.novel,
      different: s.different !== undefined ? s.different : undefined,
    })];
    if (memFile) memArgs.push('--file', memFile);
    try { console.log(runMem(...memArgs)); } catch (e2) { console.error('⚠️ 失败记忆存档异常：' + e2.message); }
    console.log('\n❌ 实验中止（候选失败），失败原因已记录。');
    process.exit(1); // v0.6：实验失败 → exit 1（机器可识别）
  }
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
    // GPT 审修复：reproduced 不再由 repeat>=2 冒充——科研重复 = 同输入多次稳定
    // + 换输入/换种子仍成立（variant_verified 必须由真实多变体实验证明）
    reproduced: false,
    repeated_same_input: repeat >= 3,
    variant_verified: Array.isArray(s.variant_results) && s.variant_results.length >= 2,
    inputs: Array.isArray(s.inputs) ? s.inputs.length : 1,
    seeds: s.seeds !== undefined ? s.seeds : 1,
    // v0.8（GPT 审第八份）：统一证据模型——老路径也输出 ms_all（1 组输入 × N 次测量）。
    // 无跨输入 → 证据完整性不满足 → 不能 L4（与 workload 路径同一套判定）
    distinct_inputs: 1, // v0.9：老路径 1 组输入 = 1 个独立条件
    ms_all: [candArr],
    baseline_ms_all: [baseArr], // v0.9：baseline 原始证据也全量入库
    baseline_ms: [baseAvg],
    candidate_mean: Math.round(candAvg * 100) / 100,
    novelty_check: s.novelty_check || '',
    improved: better, // v0.6：L4 与「成功」绑定
    failure_reason: better ? '' : ('未超越 baseline（候选均值 ' + Math.round(candAvg * 100) / 100 + ' vs baseline ' + Math.round(baseAvg * 100) / 100 + '）'),
    different: s.different !== undefined ? s.different : undefined,
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
