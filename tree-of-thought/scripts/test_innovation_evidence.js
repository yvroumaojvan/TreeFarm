#!/usr/bin/env node
/**
 * test_innovation_evidence.js —— v0.8 恶意反例测试（GPT 审第八份：证据完整性）
 *
 * 目的：评分器不能相信任何声明。伪造/不完整/矛盾的证据必须全部被拒绝——
 * 拿不到重复加分、进不了 L4。以下场景全部应该被系统拒绝。
 *
 * 用法：node test_innovation_evidence.js
 */
'use strict';
const assert = require('assert');
const { scoreInnovation } = require('./innovation_score.js');

// 完整证据基线：3 输入 × 5 重复 × 成功 → 应 L4
const good = {
  scheme: '跨输入×跨重复的真实验候选',
  different: true, code: true, ran: true,
  results: [0.02, 0.021, 0.019],
  baseline: 0.05,
  inputs: 3, variant_verified: true, improved: true,
  repeats: 5, distinct_inputs: 3, // v0.9：独立条件数由数据自证
  ms_all: [[0.02, 0.021, 0.019, 0.02, 0.022], [0.03, 0.031, 0.029, 0.03, 0.032], [0.025, 0.026, 0.024, 0.025, 0.027]],
  baseline_ms_all: [[0.05, 0.051, 0.049, 0.05, 0.052], [0.05, 0.05, 0.051, 0.049, 0.05], [0.051, 0.049, 0.05, 0.05, 0.05]],
  novelty_check: '检索未见同构方案，无先例，与已有 LSH 不是重复',
};

let passed = 0;
function t(name, fn) {
  try { fn(); passed++; console.log('  ✅ ' + name); }
  catch (e) { console.error('  ❌ ' + name + ' —— ' + e.message); process.exitCode = 1; }
}

t('基准：完整证据（3输入×5重复+成功）→ L4', () => {
  const r = scoreInnovation(good);
  assert.strictEqual(r.level, 'L4');
});

// GPT 第四优先级：伪造 repeats
t('恶意：repeats=5 但 ms_all 每组只有 1 次 → 拒（重复证据无效）', () => {
  const r = scoreInnovation({ ...good, ms_all: [[0.02], [0.03], [0.025]] });
  assert.strictEqual(r.level, 'L3');
  assert.ok(r.notes && r.notes.some((n) => n.includes('证据不完整')));
});
t('恶意：repeats=5 但 ms_all 缺失 → 拒', () => {
  const r = scoreInnovation({ ...good, ms_all: undefined });
  assert.strictEqual(r.level, 'L3');
  assert.ok(r.notes && r.notes.some((n) => n.includes('证据不完整')));
});
t('恶意：ms_all 含 NaN → 拒（非法数字）', () => {
  const r = scoreInnovation({ ...good, ms_all: [[0.02, NaN, 0.019, 0.02, 0.022], [0.03, 0.031, 0.029, 0.03, 0.032], [0.025, 0.026, 0.024, 0.025, 0.027]] });
  assert.strictEqual(r.level, 'L3');
});
t('恶意：ms_all 含字符串 "NaN" → 拒（非数字类型）', () => {
  const r = scoreInnovation({ ...good, ms_all: [[0.02, 'NaN', 0.019, 0.02, 0.022], [0.03, 0.031, 0.029, 0.03, 0.032], [0.025, 0.026, 0.024, 0.025, 0.027]] });
  assert.strictEqual(r.level, 'L3');
});
t('恶意：repeats=5 但 ms_all 组数 < inputs → 拒（有的输入没数据）', () => {
  const r = scoreInnovation({ ...good, ms_all: [[0.02, 0.021, 0.019, 0.02, 0.022], [0.03, 0.031, 0.029, 0.03, 0.032]] });
  assert.strictEqual(r.level, 'L3');
});

// GPT 第四优先级：repeat=1 试图进 L4
t('恶意：repeat=1 高分想进 L4 → 拒（L3，提示硬门槛）', () => {
  const r = scoreInnovation({ ...good, repeats: 1, ms_all: [[0.02], [0.03], [0.025]] });
  assert.strictEqual(r.level, 'L3');
  assert.ok(r.notes && r.notes.some((n) => n.includes('L4')));
});

// GPT 第四优先级：偶然性 / 矛盾 / 失败改写
t('可疑：结果波动巨大（单次偶然变快嫌疑）→ 拿不到稳定分', () => {
  const r = scoreInnovation({ ...good, results: [0.001, 0.5, 0.5], baseline: 0.4 });
  assert.ok(r.dims.robustness < 20);   // 稳定分缺失（波动 >30%）
});
t('恶意：候选平均更慢但 2/3 输入快（improved=false）→ 不能 L4', () => {
  const r = scoreInnovation({ ...good, improved: false });
  assert.strictEqual(r.level, 'L3');
  assert.ok(r.notes && r.notes.some((n) => n.includes('L4')));
});
t('恶意：输出不一致（improved 缺失）→ 不能 L4', () => {
  const r = scoreInnovation({ ...good, improved: undefined });
  assert.strictEqual(r.level, 'L3');
});
t('恶意：实验失败（ran=false）却改写结果字段 → 不能 L4', () => {
  const r = scoreInnovation({ ...good, ran: false, results: [0.02, 0.021, 0.019] });
  assert.strictEqual(r.level, 'L3');
});

// v0.9（GPT 审第九份）：条件唯一性 + 整数锁 + baseline 原始证据
t('恶意：[1,1] 两个相同输入 → 只算 1 个独立条件 → 不能 L4', () => {
  const r = scoreInnovation({ ...good, distinct_inputs: 1 });
  assert.strictEqual(r.level, 'L3');
  assert.ok(r.notes && r.notes.some((n) => n.includes('L4')));
});
t('恶意：[1,1,1] 三个相同输入 → 仍 1 个独立条件 → 不能 L4', () => {
  const r = scoreInnovation({ ...good, distinct_inputs: 1, results: [0.02, 0.021, 0.019] });
  assert.strictEqual(r.level, 'L3');
});
t('正常：[1,2] 两个不同条件 + 完整重复 → 可 L4', () => {
  const r = scoreInnovation({
    ...good,
    distinct_inputs: 2, results: [0.02, 0.021],
    ms_all: [[0.02, 0.021, 0.019, 0.02, 0.022], [0.03, 0.031, 0.029, 0.03, 0.032]],
    baseline_ms_all: [[0.05, 0.051, 0.049, 0.05, 0.052], [0.05, 0.05, 0.051, 0.049, 0.05]],
  });
  assert.strictEqual(r.level, 'L4');
});
t('恶意：repeats=3.5 非整数 → 拒（3.5 次实验无科研意义）', () => {
  const r = scoreInnovation({ ...good, repeats: 3.5 });
  assert.strictEqual(r.level, 'L3');
});
t('恶意：baseline 原始数据缺失 → 证据不完整 → 不能 L4', () => {
  const r = scoreInnovation({ ...good, baseline_ms_all: undefined });
  assert.strictEqual(r.level, 'L3');
  assert.ok(r.notes && r.notes.some((n) => n.includes('L4')));
});

console.log(`\n${passed}/16 恶意反例全拒`);
