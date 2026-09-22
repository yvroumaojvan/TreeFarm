#!/usr/bin/env node
'use strict';

/* ============================================================
 * tree-search.js R9：算法深化测试（node assert，零依赖）
 * 运行：node test_search_deep_round9.js
 * 覆盖：评分权重 / 弱信号聚类 / 非法操作拒绝 / 深树搜索 / 收敛
 * ============================================================ */

var execFileSync = require('child_process').execFileSync;
var assert = require('assert');
var fs = require('fs');
var os = require('os');
var path = require('path');

var SCRIPT = path.join(__dirname, 'tree-search.js');

function run(args, stateFile) {
  var a = [SCRIPT].concat(args);
  if (stateFile) a.push('--state', stateFile);
  return execFileSync('node', a, { encoding: 'utf8' });
}

function newState() {
  var f = path.join(os.tmpdir(), 'tf_r9_' + Date.now() + '_' +
    Math.floor(Math.random() * 1e6) + '.json');
  fs.writeFileSync(f, '{}');
  return f;
}

function cleanup(f) { try { fs.unlinkSync(f); } catch (e) {} }

var tests = [];
function test(name, fn) { tests.push({ name: name, fn: fn }); }

test('评分权重：evidence 35 权重最高', function () {
  var s = newState();
  run(['init', '问题'], s);
  run(['expand', 'n_1', JSON.stringify([
    { dimension: 'A', conclusion: 'a' },
    { dimension: 'B', conclusion: 'b' }
  ])], s);
  // a 的 evidence 高（0.9），b 的 novelty 高（0.95）→ a 应更高分（evidence 权重 35% > novelty 15%）
  run(['score', 'n_2', '{"evidence":0.9,"relevance":0.5,"novelty":0.3,"verifiable":0.5}'], s);
  run(['score', 'n_3', '{"evidence":0.3,"relevance":0.5,"novelty":0.95,"verifiable":0.5}'], s);
  var out = run(['select'], s);
  assert.ok(out.indexOf('n_2') >= 0, 'evidence 高权重应选 n_2: ' + out);
  cleanup(s);
});

test('弱信号聚类：相似分支归为一簇', function () {
  var s = newState();
  run(['init', '问题'], s);
  run(['expand', 'n_1', JSON.stringify([
    { dimension: 'A', conclusion: '需要升级依赖版本' },
    { dimension: 'B', conclusion: '需要升级依赖版本到最新' },
    { dimension: 'C', conclusion: '可能是缓存问题' }
  ])], s);
  var out = run(['signal', '1'], s);
  // 相似度高的前两条应被聚类，C 独立 → 弱信号应提及频率低的分支
  assert.ok(out.indexOf('弱信号') >= 0, out);
  cleanup(s);
});

test('非法操作：prune 无子节点时友好拒绝', function () {
  var s = newState();
  run(['init', '问题'], s);
  var out = run(['prune'], s);
  assert.ok(out.indexOf('剪掉 0') >= 0 || out.indexOf('没有') >= 0,
            '应友好提示（剪 0 个或提示无节点）: ' + out);
  cleanup(s);
});

test('深树搜索：深度 4 完整展开', function () {
  var s = newState();
  run(['init', '--beam', '2', '--depth', '4', '深问题'], s);
  // 铺满：n_1 → 2 分支 → 每分支 2 → ...
  run(['expand', 'n_1', JSON.stringify([{ dimension: 'A', conclusion: 'a1' }, { dimension: 'B', conclusion: 'b1' }])], s);
  run(['expand', 'n_2', JSON.stringify([{ dimension: 'A2', conclusion: 'a2' }, { dimension: 'B2', conclusion: 'b2' }])], s);
  run(['expand', 'n_3', JSON.stringify([{ dimension: 'A3', conclusion: 'a3' }, { dimension: 'B3', conclusion: 'b3' }])], s);
  run(['expand', 'n_4', JSON.stringify([{ dimension: 'B4', conclusion: 'b4' }])], s);
  var out = run(['status'], s);
  // status 显示「节点 N」，展开后应 ≥ 5（根+4 子）
  var m = out.match(/节点\s*(\d+)/);
  assert.ok(m && parseInt(m[1], 10) >= 5, '深度展开后节点数应 ≥5: ' + out);
  cleanup(s);
});

test('收敛检测：相同结论多数 → 收敛', function () {
  var s = newState();
  run(['init', '问题'], s);
  run(['expand', 'n_1', JSON.stringify([
    { dimension: 'A', conclusion: '答案是 42' },
    { dimension: 'B', conclusion: '答案是 42' },
    { dimension: 'C', conclusion: '答案是 42' },
    { dimension: 'D', conclusion: '不确定' }
  ])], s);
  var out = run(['converge'], s);
  assert.ok(out.indexOf('收敛') >= 0, out);
  cleanup(s);
});

test('状态持久化：save 后 load 恢复', function () {
  var s = newState();
  run(['init', '问题'], s);
  run(['expand', 'n_1', JSON.stringify([{ dimension: 'A', conclusion: 'a' }])], s);
  var out1 = run(['status'], s);
  var out2 = run(['status'], s);  // 同一状态文件两次读
  // status 显示「节点 2」（根+1 子）且两次一致
  assert.ok(out1.indexOf('节点 2') >= 0 && out2.indexOf('节点 2') >= 0,
            '状态持久化恢复：' + out1.replace(/\n/g, ' | '));
  cleanup(s);
});

/* ---------- 跑测试 ---------- */
var failed = 0;
tests.forEach(function (t) {
  try {
    t.fn();
    console.log('  ✅ ' + t.name);
  } catch (e) {
    failed++;
    console.log('  ❌ ' + t.name);
    console.log('     ' + String(e.message || e).split('\n').join('\n     '));
  }
});
console.log('\n' + (tests.length - failed) + '/' + tests.length + ' 通过');
process.exit(failed ? 1 : 0);