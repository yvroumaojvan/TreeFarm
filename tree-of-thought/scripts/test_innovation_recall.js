#!/usr/bin/env node
/**
 * test_innovation_recall.js —— 灵感树 recall 联想旧果实测试
 * 覆盖：果实库有相似果实 → recall 能找到并给出灵感提示；
 *      无相似果实 → 给出空提示。
 * 零依赖，用 --harvest 指定临时果实库，不动真实库。
 */
'use strict';
const assert = require('assert');
const { execFileSync } = require('child_process');
const fs = require('fs');
const path = require('path');
const os = require('os');

const DIR = __dirname;
const STATE = path.join(DIR, '.tree_state.json');
const TMP = path.join(os.tmpdir(), `tf_recall_test_${process.pid}.json`);

function run(...args) {
  return execFileSync('node', ['innovation.js', ...args], { cwd: DIR, encoding: 'utf8' });
}
function backup() {
  let b = null;
  try { b = fs.readFileSync(STATE, 'utf8'); } catch (_) {}
  return b;
}
function restore(b) {
  if (b === null) { try { fs.unlinkSync(STATE); } catch (_) {} }
  else fs.writeFileSync(STATE, b);
}

let passed = 0;
function t(name, fn) {
  try { fn(); passed++; console.log('✅ ' + name); }
  catch (e) { console.error('❌ ' + name + '\n   ' + e.message); process.exitCode = 1; }
}

const saved = backup();
try {
  t('recall 找到相似果实并给出灵感提示', () => {
    fs.writeFileSync(TMP, JSON.stringify([
      { seed: '如何让缓存自动适应访问模式', form: '机制：滑动窗口', idea: '用滑动窗口统计热点动态换策略', why: '适配模式', score: 8.6, time: '2026-10-01T00:00:00Z' },
      { seed: '如何让树学会举一反三', form: '机制：递归联想', idea: '旧果实套新内核', why: '举一反三', score: 8.9, time: '2026-10-01T00:00:00Z' },
    ]));
    const out = run('recall', '缓存怎么自动适配访问模式', '--harvest', TMP);
    assert.ok(out.includes('灵光一现'), '应找到相似果实');
    assert.ok(out.includes('滑动窗口'), '应命中最相似果实');
  });
  t('recall 无相似果实给空提示', () => {
    fs.writeFileSync(TMP, JSON.stringify([
      { seed: '如何让树学会举一反三', form: '机制：递归联想', idea: '旧果实套新内核', why: '举一反三', score: 8.9, time: '2026-10-01T00:00:00Z' },
    ]));
    // 用与中文果实几乎零 trigram 重叠的英文串，避免中文高频词（如何/让/的）虚增相似度
    const out = run('recall', 'quantum coffee brewing temperature zz', '--harvest', TMP);
    assert.ok(out.includes('没有相似的旧果实'), '应提示无相似，实际：' + out.split('\n')[0]);
  });
} finally {
  restore(saved);
  try { fs.unlinkSync(TMP); } catch (_) {}
}

console.log(`\n${passed}/2 通过`);
