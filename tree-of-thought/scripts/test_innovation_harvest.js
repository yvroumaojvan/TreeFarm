#!/usr/bin/env node
/**
 * test_innovation_harvest.js —— 灵感树 harvest 防重复入库测试
 * 覆盖：完整流程 idea→forms→score→converge→harvest 两次，
 * 第二次同一果实应被拒绝（r40 防重复），果实库长度保持 1。
 * 零依赖（node 原生 child_process），自动备份/恢复状态文件。
 */
'use strict';
const assert = require('assert');
const { execFileSync } = require('child_process');
const fs = require('fs');
const path = require('path');
const os = require('os');

const DIR = __dirname;
const STATE = path.join(DIR, '.tree_state.json');
const HARVEST = path.join(DIR, 'innovation_harvest.json');
const TMP = path.join(os.tmpdir(), `tf_harvest_test_${process.pid}.json`);

function run(...args) {
  return execFileSync('node', ['innovation.js', ...args], { cwd: DIR, encoding: 'utf8' });
}
function backup() {
  const b = {};
  for (const f of [STATE, HARVEST]) {
    try { b[f] = fs.readFileSync(f, 'utf8'); } catch (_) { b[f] = null; }
  }
  return b;
}
function restore(b) {
  for (const f of Object.keys(b)) {
    if (b[f] === null) { try { fs.unlinkSync(f); } catch (_) {} }
    else fs.writeFileSync(f, b[f]);
  }
}
function harvestCount(file) {
  const d = JSON.parse(fs.readFileSync(file, 'utf8'));
  return Array.isArray(d) ? d.length : 0;
}

let passed = 0;
function t(name, fn) {
  try { fn(); passed++; console.log('✅ ' + name); }
  catch (e) { console.error('❌ ' + name + '\n   ' + e.message); process.exitCode = 1; }
}

const saved = backup();
try {
  t('完整流程 harvest 两次 → 第二次拒绝且库不增长', () => {
    try { fs.unlinkSync(TMP); } catch (_) {}
    run('idea', '测试内核：如何让缓存自动适应访问模式');
    run('forms', JSON.stringify([{ form: '机制：滑动窗口', idea: '用滑动窗口统计热点并动态换缓存策略', why: '适配访问模式变化' }]));
    run('score', 'n_2', JSON.stringify({ relevance: 8, novelty: 9, expressiveness: 7, feasibility: 6 }));
    const out1 = run('harvest', '--harvest', TMP);
    assert.strictEqual(harvestCount(TMP), 1, '第一次 harvest 后应有 1 颗果实');
    assert.ok(out1.includes('已入库'), '第一次应成功入库');
    const out2 = run('harvest', '--harvest', TMP);
    assert.strictEqual(harvestCount(TMP), 1, '第二次 harvest 后库应仍是 1 颗（防重复）');
    assert.ok(out2.includes('重复果实'), '第二次应提示重复');
  });
} finally {
  restore(saved);
  try { fs.unlinkSync(TMP); } catch (_) {}
}

console.log(`\n${passed}/1 通过`);
