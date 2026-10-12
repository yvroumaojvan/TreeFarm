#!/usr/bin/env node
/**
 * test_innovation_memory.js —— Innovation Memory 创新记忆库测试
 * 覆盖：save 自动六维评分、缺失必填拒绝、stats 漏斗、list 列表。
 * 零依赖，用 --file 指定临时记忆库，不动真实库。
 */
'use strict';
const assert = require('assert');
const { execFileSync } = require('child_process');
const fs = require('fs');
const path = require('path');
const os = require('os');

const DIR = __dirname;
const TMP = path.join(os.tmpdir(), `tf_mem_test_${process.pid}.json`);

function run(...args) {
  return execFileSync('node', ['innovation_memory.js', '--file', TMP, ...args], { cwd: DIR, encoding: 'utf8' });
}

let passed = 0;
function t(name, fn) {
  try { fn(); passed++; console.log('✅ ' + name); }
  catch (e) { console.error('❌ ' + name + '\n   ' + e.message); process.exitCode = 1; }
}

try { fs.unlinkSync(TMP); } catch (_) {}
try {
  t('save 完整实验记忆 → 自动六维创新评分', () => {
    const out = run('save', JSON.stringify({
      seed: '如何让缓存自动适应访问模式', form: '机制：滑动窗口+哈希', idea: '融合滑动窗口与哈希的动态缓存',
      hypothesis: '热点滑动窗口预测能减少缓存失效', code: true, ran: true,
      results: [68.2, 66.9, 67.4, 67.1, 66.8], baseline: 80.1,
      inputs: 3, seeds: 2, improved: true, reproduced: true,
      novelty_check: '检索未见同构方案', failure_reason: '',
    }));
    assert.ok(out.includes('创新评分'), '应输出创新评分');
    assert.ok(out.includes('L4') || out.includes('L3'), '应评出 L3/L4 等级');
    const mem = JSON.parse(fs.readFileSync(TMP, 'utf8'));
    assert.strictEqual(mem.length, 1);
    assert.ok(mem[0].innovation && mem[0].innovation.total >= 40, '评分应 ≥40（可执行级）');
  });
  t('save 缺灵感三要素 → 拒绝', () => {
    assert.throws(() => run('save', JSON.stringify({ idea: '只有想法没有内核' })));
  });
  t('save 记失败记忆（failure_reason）', () => {
    run('save', JSON.stringify({
      seed: '如何让树学会举一反三', form: '机制：递归联想', idea: '直接递归会导致发散',
      ran: true, results: [5], baseline: 10, improved: false,
      failure_reason: '递归无终止条件，结果发散——下次加深度上限',
    }));
    const mem = JSON.parse(fs.readFileSync(TMP, 'utf8'));
    assert.strictEqual(mem.length, 2);
    assert.ok(mem[1].failure_reason.includes('发散'), '失败原因应被记录');
  });
  t('stats 输出 Innovation Rate 漏斗', () => {
    const out = run('stats');
    assert.ok(out.includes('Innovation Rate'), '应输出漏斗统计');
    assert.ok(out.includes('优于baseline'), '应统计优于 baseline 数');
  });
  t('list 列出全部记忆', () => {
    const out = run('list');
    assert.ok(out.includes('创新记忆库共 2 条'), '应列出 2 条');
  });
} finally {
  try { fs.unlinkSync(TMP); } catch (_) {}
}

console.log(`\n${passed}/5 通过`);
