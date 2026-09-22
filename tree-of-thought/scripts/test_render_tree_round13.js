#!/usr/bin/env node
'use strict';

/* ============================================================
 * render-tree.js 第 13 轮测试（node assert，零依赖）
 * 运行：node test_render_tree_round13.js
 * 覆盖：rounds 格式 / search 状态格式 / ascii / stdin / 空数据
 * ============================================================ */

var execFileSync = require('child_process').execFileSync;
var assert = require('assert');
var fs = require('fs');
var os = require('os');
var path = require('path');

var SCRIPT = path.join(__dirname, 'render-tree.js');

function renderFile(data, extra) {
  var f = path.join(os.tmpdir(), 'tf_r13_' + Date.now() + '_' +
    Math.floor(Math.random() * 1e6) + '.json');
  fs.writeFileSync(f, JSON.stringify(data));
  var args = [SCRIPT];
  if (extra) args = args.concat(extra);
  args.push(f);
  try {
    return execFileSync('node', args, { encoding: 'utf8' });
  } finally {
    try { fs.unlinkSync(f); } catch (e) {}
  }
}

function renderStdin(data) {
  var f = path.join(os.tmpdir(), 'tf_r13s_' + Date.now() + '.json');
  fs.writeFileSync(f, JSON.stringify(data));
  var args = [SCRIPT];
  var out = execFileSync('node', args, { encoding: 'utf8', input: fs.readFileSync(f) });
  try { fs.unlinkSync(f); } catch (e) {}
  return out;
}

var tests = [];
function test(name, fn) { tests.push({ name: name, fn: fn }); }

test('rounds 格式完整渲染', function () {
  var out = renderFile({
    trunk: '测试问题',
    rounds: [{
      round: 1,
      branches: [{ dimension: '维度A', hypothesis: '假设', reasoning: '推理', conclusion: '结论' }],
      consensus: '共识内容',
      weakSignals: ['弱信号1']
    }],
    answer: '最终答案',
    stopReason: 'converged'
  });
  assert.ok(out.indexOf('树干') >= 0, out);
  assert.ok(out.indexOf('维度A') >= 0, out);
  assert.ok(out.indexOf('共识') >= 0, out);
  assert.ok(out.indexOf('最终答案') >= 0, out);
  assert.ok(out.indexOf('弱信号') >= 0, out);
});

test('search 状态格式渲染（带徽章）', function () {
  var out = renderFile({
    version: 3,
    trunk: '问题',
    strategy: 'beam',
    beam_width: 4,
    max_depth: 2,
    order: ['n_1', 'n_2', 'n_3'],
    nodes: {
      n_1: { id: 'n_1', parent: null, depth: 0, type: 'trunk', content: '问题', status: 'expanded', children: ['n_2', 'n_3'], pruned: false, score: null },
      n_2: { id: 'n_2', parent: 'n_1', depth: 1, type: 'branch', content: { dimension: 'A', conclusion: 'ac' }, status: 'scored', children: [], pruned: false, score: { evidence: 0.9, relevance: 0.8, novelty: 0.5, verifiable: 0.7, total: 0.77 } },
      n_3: { id: 'n_3', parent: 'n_1', depth: 1, type: 'branch', content: { dimension: 'B', conclusion: 'bc' }, status: 'pruned', children: [], pruned: true, score: null }
    }
  });
  assert.ok(out.indexOf('BEAM') >= 0, out);
  assert.ok(out.indexOf('剪枝') >= 0, out);
  assert.ok(out.indexOf('评分') >= 0, out);
});

test('ascii 模式无 emoji', function () {
  var out = renderFile({ trunk: '问题', rounds: [], answer: '答案' }, ['--ascii']);
  assert.ok(out.indexOf('[树干]') >= 0, out);
  assert.ok(out.indexOf('🌳') < 0, 'ascii 模式不应含 emoji');
});

test('compact 模式分支只显示结论', function () {
  var out = renderFile({
    trunk: '问题',
    rounds: [{ round: 1, branches: [
      { dimension: 'A', hypothesis: '假设内容', reasoning: '推理内容', conclusion: '结论内容' }
    ] }],
    answer: '答案'
  }, ['--compact']);
  assert.ok(out.indexOf('结论内容') >= 0, out);
});

test('stdin 输入渲染', function () {
  var out = renderStdin({ trunk: 'stdin问题', rounds: [], answer: 'stdin答案' });
  assert.ok(out.indexOf('stdin问题') >= 0, out);
  assert.ok(out.indexOf('stdin答案') >= 0, out);
});

test('空 rounds（0 轮）仍渲染答案', function () {
  var out = renderFile({ trunk: '问题', rounds: [], answer: '答案永不失' });
  assert.ok(out.indexOf('答案永不失') >= 0, '0 轮也应渲染答案');
});

test('demo 数据渲染不崩且示范真思考', function () {
  var out = renderFile(null, ['--demo']);
  assert.ok(out.indexOf('创业') >= 0, 'demo 应渲染创业示例');
  assert.ok(out.indexOf('财务安全边际') >= 0, 'demo 应含按领域长的维度（非模板）');
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
