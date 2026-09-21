#!/usr/bin/env node
'use strict';

/* ============================================================
 * tree-search.js 第 12 轮边界/健壮性测试（node assert，零依赖）
 * 运行：node test_tree_search_round12.js
 * 覆盖：极端参数 / 损坏状态 / 空状态 / 边界评分 / 大状态渲染
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
  var f = path.join(os.tmpdir(), 'tf_search_r12_' + Date.now() + '_' +
    Math.floor(Math.random() * 1e6) + '.json');
  fs.writeFileSync(f, '{}');
  return f;
}

function cleanup(f) { try { fs.unlinkSync(f); } catch (e) {} }

var tests = [];
function test(name, fn) { tests.push({ name: name, fn: fn }); }

/* ---------- 用例 ---------- */

test('init 无问题参数：用默认名不崩', function () {
  var s = newState();
  var out = run(['init'], s);
  assert.ok(out.indexOf('思维树已初始化') >= 0, out);
  cleanup(s);
});

test('beam=1 极端束宽：剪枝只留 1 个', function () {
  var s = newState();
  run(['init', '--beam', '1', '--depth', '1', '问题'], s);
  run(['expand', 'n_1', JSON.stringify([
    { dimension: 'A', conclusion: 'a1' },
    { dimension: 'B', conclusion: 'b1' },
    { dimension: 'C', conclusion: 'c1' }
  ])], s);
  run(['score', 'n_2', '{"evidence":0.9,"relevance":0.9,"novelty":0.5,"verifiable":0.8}'], s);
  run(['score', 'n_3', '{"evidence":0.5,"relevance":0.5,"novelty":0.5,"verifiable":0.5}'], s);
  run(['score', 'n_4', '{"evidence":0.2,"relevance":0.2,"novelty":0.2,"verifiable":0.2}'], s);
  var out = run(['prune'], s);
  assert.ok(/剪掉 2 个/.test(out), out);
  cleanup(s);
});

test('评分越界（>1）：综合分仍可算（不崩）', function () {
  var s = newState();
  run(['init', '问题'], s);
  run(['expand', 'n_1', JSON.stringify([{ dimension: 'A', conclusion: 'c' }])], s);
  var out = run(['score', 'n_2', '{"evidence":1.5,"relevance":0.9,"novelty":0.5,"verifiable":0.8}'], s);
  assert.ok(out.indexOf('综合评分') >= 0, out);
  cleanup(s);
});

test('损坏状态文件：自动回退新状态', function () {
  var f = path.join(os.tmpdir(), 'tf_r12_bad_' + Date.now() + '.json');
  fs.writeFileSync(f, 'not json {{{');
  var out = run(['status'], f);
  assert.ok(out.indexOf('状态') >= 0, out);
  cleanup(f);
});

test('非思维树 JSON（无 nodes）：status 不崩', function () {
  var f = path.join(os.tmpdir(), 'tf_r12_obj_' + Date.now() + '.json');
  fs.writeFileSync(f, JSON.stringify({ hello: 'world' }));
  var out = run(['status'], f);
  assert.ok(out.indexOf('状态') >= 0, out);
  cleanup(f);
});

test('expand 不存在的节点：报错退出码非 0', function () {
  var s = newState();
  var threw = false;
  try {
    run(['expand', 'n_999', '[{"dimension":"A"}]'], s);
  } catch (e) {
    threw = true;
  }
  assert.ok(threw, 'expand 不存在节点应报错');
  cleanup(s);
});

test('剪枝后禁止展开（token 保护）', function () {
  var s = newState();
  run(['init', '--beam', '1', '--depth', '2', '问题'], s);
  run(['expand', 'n_1', JSON.stringify([
    { dimension: 'A', conclusion: 'a' },
    { dimension: 'B', conclusion: 'b' }
  ])], s);
  run(['score', 'n_2', '{"evidence":0.9,"relevance":0.9,"novelty":0.5,"verifiable":0.8}'], s);
  run(['score', 'n_3', '{"evidence":0.1,"relevance":0.1,"novelty":0.1,"verifiable":0.1}'], s);
  run(['prune'], s);
  var threw = false;
  try {
    run(['expand', 'n_3', '[{"dimension":"C"}]'], s);
  } catch (e) {
    threw = true;
  }
  assert.ok(threw, '剪枝节点禁止展开');
  cleanup(s);
});

test('signal 无分支层：友好提示不崩', function () {
  var s = newState();
  run(['init', '问题'], s);
  var out = run(['signal', '5'], s);
  assert.ok(out.indexOf('没有可检测的分支') >= 0, out);
  cleanup(s);
});

test('converge 不足两层：友好提示不崩', function () {
  var s = newState();
  run(['init', '问题'], s);
  var out = run(['converge'], s);
  assert.ok(out.indexOf('不足两层') >= 0, out);
  cleanup(s);
});

test('大状态渲染（60 节点）不崩', function () {
  var s = newState();
  run(['init', '--beam', '8', '--depth', '3', '大问题'], s);
  // 铺两层：根 → 8 分支 → 每分支 6 子
  var branches = [];
  for (var i = 0; i < 8; i++) {
    branches.push({ dimension: '维' + i, hypothesis: 'h', reasoning: 'r', conclusion: 'c' + i });
  }
  run(['expand', 'n_1', JSON.stringify(branches)], s);
  for (var b = 0; b < 8; b++) {
    var kid = 'n_' + (2 + b);
    var subs = [];
    for (var j = 0; j < 6; j++) {
      subs.push({ dimension: '子' + b + '_' + j, conclusion: 'cc' + j });
    }
    run(['expand', kid, JSON.stringify(subs)], s);
  }
  var out = run(['render'], s);
  assert.ok(out.indexOf('思维树') >= 0, out);
  var total = (out.match(/n_/g) || []).length;
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
