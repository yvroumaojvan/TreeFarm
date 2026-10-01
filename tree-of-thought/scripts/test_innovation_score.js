#!/usr/bin/env node
/**
 * test_innovation_score.js —— 创新能力评分器 V1 测试套件（零依赖，Node 原生断言）
 * 覆盖：L2/L3/L4 分级、边界保护（非法输入拒绝）、换措辞降分、一次性结果不给满分。
 */
'use strict';
const assert = require('assert');
const { scoreInnovation } = require('./innovation_score.js');

let passed = 0;
function t(name, fn) {
  try { fn(); passed++; console.log('✅ ' + name); }
  catch (e) { console.error('❌ ' + name + '\n   ' + e.message); process.exitCode = 1; }
}

// 1) L2：组合候选，未实现未运行 → 应落 L2（20-39）
t('L2 组合候选', () => {
  const r = scoreInnovation({
    scheme: '将哈希索引与滑动窗口组合，改造为缓存淘汰策略',
    different: true,
    code: false, ran: false,
  });
  assert.strictEqual(r.level, 'L2');
  assert.strictEqual(r.dims.novelty, 15);
  assert.ok(r.total >= 20 && r.total <= 39);
});

// 2) L3：已实现 + 运行 + baseline 对比（无重复实验）→ L3（40-64）
t('L3 可执行创新', () => {
  const r = scoreInnovation({
    scheme: '用二分跳表替代线性扫描，改造检索路径',
    different: true,
    code: true, ran: true,
    results: [41], baseline: 53,
  });
  assert.strictEqual(r.level, 'L3');
  assert.strictEqual(r.dims.executable, 15);
  assert.strictEqual(r.dims.experiment, 20);
});

// 3) L4：多次实验稳定 + 换输入/种子 + 新颖性证据 → L4（65-84）
t('L4 可重复创新', () => {
  const r = scoreInnovation({
    scheme: '融合马尔可夫与指纹检索的混合索引',
    different: true,
    code: true, ran: true,
    results: [68.2, 66.9, 67.4, 67.1, 66.8],
    baseline: 80.1,
    inputs: 3, seeds: 2, variant_verified: true,
    novelty_check: '检索未见同构方案，无先例，与已有 LSH 不是重复',
  });
  assert.strictEqual(r.level, 'L4');
  assert.ok(r.dims.robustness >= 15);
  assert.ok(r.dims.evidence >= 6);
  assert.ok(r.note);   // 总分达到 L5 区间但缺外部验证 → 硬门槛降 L4 并给提示
});

// 3b) GPT 审修复：伪 L4 复现——只跑一次 + 写句 novelty_check → 不得进 L4
t('伪L4拦截：单次实验不得进L4（GPT审案例）', () => {
  const r = scoreInnovation({
    scheme: '候选比 baseline 好一点点',
    different: true,
    code: true, ran: true,
    results: [11],   // 只跑 1 次
    baseline: 10,
    novelty_check: '检索未见同构方案',
    inputs: 1, seeds: 1,
  });
  assert.strictEqual(r.level, 'L3');   // 必须降级（缺可重复证据）
  assert.ok(r.notes && r.notes.some((n) => n.includes('L4')));
});

// 4) 边界保护：非法数值输入 → 拒绝评分（防 NaN，GPT 挖的坑）
t('边界保护：非法输入拒绝', () => {
  const r = scoreInnovation({
    scheme: '任意方案',
    different: true,
    results: ['x'],   // 非法
  });
  assert.ok(r.error);
  assert.strictEqual(r.total, 0);
});

// 5) 换措辞降分：different=false → novelty 只给 5，总分低
t('换措辞不给高分', () => {
  const r = scoreInnovation({
    scheme: '把扫描算法换个说法重写',
    different: false,
    ran: true, results: [5], baseline: 5,
  });
  assert.strictEqual(r.dims.novelty, 5);
  assert.ok(r.total < 40);   // 落不到 L3
});

// 6) 一次性结果不给稳健分：results 只有 1 条 → robustness ≤ 7（不给 20 满分）
t('单次结果不给重复性满分', () => {
  const r = scoreInnovation({
    scheme: '某一次性实验方案',
    different: true,
    ran: true, results: [99], baseline: 90,
    novelty_check: '无先例检索通过',
  });
  assert.ok(r.dims.robustness <= 7);
});

// 7) 空证据 → 拒绝
t('空证据拒绝', () => {
  const r = scoreInnovation(null);
  assert.ok(r.error);
});

console.log(`\n${passed}/7 通过`);
