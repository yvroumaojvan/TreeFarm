#!/usr/bin/env node
'use strict';

/* ============================================================
 * bridge.js —— 抓虫树 ↔ 灵感树 桥接引擎（创新分支 v1.1）
 * 乖宝理论落地：抓 bug 时剪掉的「弱信号分支」→ 灵感的种子
 * （灵感 = 把两个不相干的东西串起来：老 bug 的线索 + 新内核 = 新表达）
 *
 * 用法：
 *   node bridge.js signals <tree_state.json> [--seeds out.json]
 *     读取抓虫树（tree-search.js）状态，提取被剪枝分支（弱信号）
 *     → 输出灵感种子列表（seeds.json）
 *   node bridge.js harvest <seeds.json>
 *     把种子列表列出来（提示：对每个种子用 innovation.js idea 种树）
 *   node bridge.js fruits <innovation_harvest.json>
 *     列出灵感树果实库，提示可作抓虫树的「跨领域参考分支」
 *
 * 零依赖，手机可跑。
 * ============================================================ */

var fs = require('fs');
var path = require('path');

var args = process.argv.slice(2);
var cmd = args[0];

function fail(msg) { console.error('❌ ' + msg); process.exit(1); }

function loadJson(p) {
  try {
    return JSON.parse(fs.readFileSync(p, 'utf8'));
  } catch (e) {
    fail('读取 ' + p + ' 失败：' + e.message);
  }
}

if (!cmd) {
  console.log('🌉 bridge.js —— 抓虫树 ↔ 灵感树 桥接（v1.1）');
  console.log('用法：');
  console.log('  node bridge.js signals <tree_state.json> [--seeds out.json]');
  console.log('  node bridge.js harvest <seeds.json>');
  console.log('  node bridge.js fruits <innovation_harvest.json>');
  process.exit(0);
}

if (cmd === 'signals') {
  var stateFile = args[1];
  if (!stateFile) fail('需要 tree-search.js 状态文件（--state 参数指定的那个）');
  var outFile = null;
  var i = args.indexOf('--seeds');
  if (i >= 0 && args[i + 1]) outFile = args[i + 1];

  var s = loadJson(stateFile);
  var nodes = s.nodes || {};
  var pruned = [];
  Object.keys(nodes).forEach(function (id) {
    var n = nodes[id];
    if (!n.pruned) return;
    var c = n.content;
    var text = '';
    if (typeof c === 'string') text = c;
    else if (c && typeof c === 'object') {
      text = [c.dimension, c.hypothesis, c.reasoning, c.conclusion]
        .filter(Boolean).join(' | ');
    }
    if (text) pruned.push({ source: 'pruned:' + id, depth: n.depth || 0, signal: text });
  });

  if (!pruned.length) {
    console.log('🍂 该状态文件没有被剪枝的分支（弱信号为空）——需要先跑 tree-search.js 做一轮剪枝');
    process.exit(0);
  }

  // 去重（简单标题去重）
  var seen = {};
  var seeds = pruned.filter(function (p) {
    var k = p.signal.slice(0, 40);
    if (seen[k]) return false;
    seen[k] = true;
    return true;
  });

  if (outFile) {
    fs.writeFileSync(outFile, JSON.stringify(
      { count: seeds.length, seeds: seeds, note: '用创新.js 对每个 seed 种树：node innovation.js idea "<seed>"' },
      null, 2), 'utf8');
    console.log('🔗 已提取 ' + seeds.length + ' 颗弱信号种子 → ' + outFile);
  } else {
    console.log('🔗 抓到 ' + seeds.length + ' 条弱信号（剪枝分支）：');
    seeds.forEach(function (p) {
      console.log('  🧵 [' + (p.dimension || '线索') + '] ' + p.signal.slice(0, 80));
    });
    console.log('\n下一步：把这些种子逐个喂给灵感树种树——旧 bug 的线索 + 新内核 = 灵光一现');
  }
} else if (cmd === 'harvest') {
  var seedsFile = args[1];
  if (!seedsFile) fail('需要 seeds.json');
  var seeds = loadJson(seedsFile);
  var arr = Array.isArray(seeds) ? seeds : (seeds.seeds || []);
  if (!arr.length) { console.log('🍂 种子列表为空'); process.exit(0); }
  console.log('🧺 共 ' + arr.length + ' 颗灵感种子，逐个种树：');
  arr.forEach(function (p, idx) {
    var text = typeof p === 'string' ? p : (p.signal || '');
    console.log('  ' + (idx + 1) + '. node innovation.js idea "' + text.slice(0, 40) + '"');
  });
} else if (cmd === 'fruits') {
  var hf = args[1];
  if (!hf) fail('需要灵感树果实库（innovation_harvest.json）');
  var fruits = loadJson(hf);
  if (!Array.isArray(fruits) || !fruits.length) { console.log('🍂 果实库为空'); process.exit(0); }
  console.log('🍎 果实库 ' + fruits.length + ' 颗，可作抓虫树的跨领域参考：');
  fruits.forEach(function (f, idx) {
    console.log('  ' + (idx + 1) + '. [' + f.form + '] ' + String(f.idea || '').slice(0, 60));
  });
  console.log('\n提示：让 AI 在抓 bug 时参考果实库的「表达形式」，可能从不同角度找到根因');
} else {
  fail('未知命令：' + cmd + '（可用 signals/harvest/fruits）');
}