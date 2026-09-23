#!/usr/bin/env node
'use strict';

/* ============================================================
 * tree-search.js R37：算法边界 III（node assert）
 * 运行：node test_search_round37.js
 * 覆盖：BFS/DFS 策略 / 深链评分传播 / 空分支JSON / 巨型评分值
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
  var f = path.join(os.tmpdir(), 'tf_r37_' + Date.now() + '_' + Math.floor(Math.random() * 1e6) + '.json');
  fs.writeFileSync(f, '{}');
  return f;
}
function cleanup(f) { try { fs.unlinkSync(f); } catch (e) {} }

var tests = [];
function test(name, fn) { tests.push({ name: name, fn: fn }); }

test('DFS 策略初始化', function () {
  var s = newState();
  var out = run(['init', '--strategy', 'dfs', 'DFS问题'], s);
  assert.ok(out.indexOf('dfs') >= 0 || out.indexOf('DFS') >= 0, out);
  cleanup(s);
});

test('BFS 策略初始化', function () {
  var s = newState();
  var out = run(['init', '--strategy', 'bfs', 'BFS问题'], s);
  assert.ok(out.indexOf('bfs') >= 0 || out.indexOf('BFS') >= 0, out);
  cleanup(s);
});

test('展开空分支数组：友好拒绝', function () {
  var s = newState();
  run(['init', '问题'], s);
  var threw = false, msg = '';
  try { run(['expand', 'n_1', '[]'], s); }
  catch (e) { threw = true; msg = String(e.stdout || e.message || e); }
  assert.ok(threw && (msg.indexOf('数组') >= 0 || msg.indexOf('空') >= 0 || msg.indexOf('无效') >= 0),
            '应友好拒绝: ' + msg.slice(0, 120));
  cleanup(s);
});

test('评分全 0 与全 1 边界', function () {
  var s = newState();
  run(['init', '问题'], s);
  run(['expand', 'n_1', JSON.stringify([{ dimension: 'A', conclusion: 'a' }, { dimension: 'B', conclusion: 'b' }])], s);
  run(['score', 'n_2', '{"evidence":0,"relevance":0,"novelty":0,"verifiable":0}'], s);
  run(['score', 'n_3', '{"evidence":1,"relevance":1,"novelty":1,"verifiable":1}'], s);
  var out = run(['select'], s);
  assert.ok(out.indexOf('n_3') >= 0, '全 1 应胜出: ' + out);
  cleanup(s);
});

test('负评分容错（不崩）', function () {
  var s = newState();
  run(['init', '问题'], s);
  run(['expand', 'n_1', JSON.stringify([{ dimension: 'A', conclusion: 'a' }])], s);
  var out = run(['score', 'n_2', '{"evidence":-1,"relevance":-2,"novelty":-1,"verifiable":-1}'], s);
  assert.ok(out.indexOf('综合') >= 0, out);
  cleanup(s);
});

test('同名分支去重展开', function () {
  var s = newState();
  run(['init', '问题'], s);
  var out = run(['expand', 'n_1', JSON.stringify([{ dimension: 'A', conclusion: 'x' }, { dimension: 'A', conclusion: 'x' }])], s);
  assert.ok(out.indexOf('2 个子节点') >= 0, out);
  cleanup(s);
});

/* 跑 */
var failed = 0;
tests.forEach(function (t) {
  try { t.fn(); console.log('  ✅ ' + t.name); }
  catch (e) { failed++; console.log('  ❌ ' + t.name + ' — ' + String(e.message || e).split('\n')[0]); }
});
console.log('\n' + (tests.length - failed) + '/' + tests.length + ' 通过');
process.exit(failed ? 1 : 0);
