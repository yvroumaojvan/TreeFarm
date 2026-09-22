#!/usr/bin/env node
'use strict';

/* ============================================================
 * tree-search.js 测试（node assert，零依赖）
 * 运行：node test_tree_search.js
 * 用真实 CLI 子进程 + 独立状态文件，测的是真实行为。
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
  var f = path.join(os.tmpdir(), 'tf_search_test_' + Date.now() + '_' +
    Math.floor(Math.random() * 1e6) + '.json');
  fs.writeFileSync(f, '{}');
  return f;
}

function cleanup(f) { try { fs.unlinkSync(f); } catch (e) {} }

var tests = [];
function test(name, fn) { tests.push({ name: name, fn: fn }); }

/* ---------- 用例 ---------- */

test('init 初始化树', function () {
  var s = newState();
  var out = run(['init', '--strategy', 'beam', '--beam', '3', '--depth', '2', '测试问题'], s);
  assert.ok(out.indexOf('思维树已初始化') >= 0, out);
  assert.ok(out.indexOf('strategy=beam') >= 0, out);
  assert.ok(fs.existsSync(s), '状态文件应自动持久化');
  cleanup(s);
});

test('expand + score 综合分计算（权重 35/30/20/15）', function () {
  var s = newState();
  run(['init', '--strategy', 'beam', '--beam', '3', '--depth', '2', '问题'], s);
  run(['expand', 'n_1', JSON.stringify([
    { dimension: 'A', hypothesis: 'h', reasoning: 'r', conclusion: 'c' }
  ])], s);
  var out = run(['score', 'n_2', '{"evidence":0.9,"relevance":0.9,"novelty":0.4,"verifiable":0.8}'], s);
  // 0.9*0.35 + 0.9*0.30 + 0.4*0.20 + 0.8*0.15 = 0.785 → 0.79
  assert.ok(out.indexOf('0.79') >= 0, out);
  cleanup(s);
});

test('select：beam 选最高分', function () {
  var s = newState();
  run(['init', '--strategy', 'beam', '--beam', '3', '--depth', '2', '问题'], s);
  run(['expand', 'n_1', JSON.stringify([
    { dimension: 'A', conclusion: 'x' },
    { dimension: 'B', conclusion: 'y' }
  ])], s);
  run(['score', 'n_2', '{"evidence":0.5,"relevance":0.5,"novelty":0.5,"verifiable":0.5}'], s);
  run(['score', 'n_3', '{"evidence":1,"relevance":1,"novelty":1,"verifiable":1}'], s);
  var out = run(['select'], s);
  assert.ok(out.indexOf('n_3') >= 0, '应选高分分支: ' + out);
  cleanup(s);
});

test('select：bfs 选深度最小，dfs 选深度最大', function () {
  var s1 = newState();
  run(['init', '--strategy', 'bfs', '--beam', '3', '--depth', '3', '问题'], s1);
  run(['expand', 'n_1', JSON.stringify([{ dimension: 'A', conclusion: 'x' }])], s1);
  run(['expand', 'n_2', JSON.stringify([{ dimension: 'A1', conclusion: 'x1' }])], s1);
  // n_2 已展开，n_3 是 deep 分支；剩余 open：n_3(深)
  var out = run(['select'], s1);
  assert.ok(out.indexOf('n_3') >= 0 || out.indexOf('n_1') >= 0, out); // bfs 按序
  cleanup(s1);

  var s2 = newState();
  run(['init', '--strategy', 'dfs', '--beam', '3', '--depth', '3', '问题'], s2);
  run(['expand', 'n_1', JSON.stringify([{ dimension: 'A', conclusion: 'x' }])], s2);
  run(['expand', 'n_2', JSON.stringify([{ dimension: 'A1', conclusion: 'x1' }])], s2);
  var out2 = run(['select'], s2);
  // dfs 应先选最深的分支
  assert.ok(out2.indexOf('n_3') >= 0, 'dfs 应选最深: ' + out2);
  cleanup(s2);
});

test('prune：束宽剪掉低分分支', function () {
  var s = newState();
  run(['init', '--strategy', 'beam', '--beam', '2', '--depth', '2', '问题'], s);
  run(['expand', 'n_1', JSON.stringify([
    { dimension: 'A', conclusion: 'a' },
    { dimension: 'B', conclusion: 'b' },
    { dimension: 'C', conclusion: 'c' }
  ])], s);
  run(['score', 'n_2', '{"evidence":1,"relevance":1,"novelty":1,"verifiable":1}'], s);
  run(['score', 'n_3', '{"evidence":0.8,"relevance":0.8,"novelty":0.8,"verifiable":0.8}'], s);
  run(['score', 'n_4', '{"evidence":0.1,"relevance":0.1,"novelty":0.1,"verifiable":0.1}'], s);
  var out = run(['prune'], s);
  assert.ok(out.indexOf('剪掉 1 个') >= 0, out);
  var st = JSON.parse(fs.readFileSync(s, 'utf8'));
  var pruned = Object.keys(st.nodes).filter(function (id) { return st.nodes[id].pruned; });
  assert.strictEqual(pruned.length, 1, '应剪 1 个分支');
  cleanup(s);
});

test('backtrack：死路标记 + 回退到有待展开兄弟的祖先', function () {
  var s = newState();
  run(['init', '--strategy', 'beam', '--beam', '3', '--depth', '3', '问题'], s);
  run(['expand', 'n_1', JSON.stringify([
    { dimension: 'A', conclusion: 'a' },
    { dimension: 'B', conclusion: 'b' }
  ])], s);
  run(['expand', 'n_2', JSON.stringify([{ dimension: 'A1', conclusion: 'x' }])], s);
  // 死掉最深的分支 n_4：n_2 的唯一子节点死了，应回退到 n_1（其下 n_3 还活着）
  var out = run(['backtrack', 'n_4', '推理无进展'], s);
  assert.ok(out.indexOf('死路') >= 0, out);
  assert.ok(out.indexOf('n_1') >= 0, '应回退到 n_1（n_3 待展开）: ' + out);
  var st = JSON.parse(fs.readFileSync(s, 'utf8'));
  assert.strictEqual(st.nodes.n_4.status, 'dead_end');
  assert.ok(st.nodes.n_4.dead_reason);
  cleanup(s);
});

test('signal：相似观点聚类，少数派=弱信号', function () {
  var s = newState();
  run(['init', '--strategy', 'beam', '--beam', '4', '--depth', '2', '问题'], s);
  run(['expand', 'n_1', JSON.stringify([
    { dimension: 'A', conclusion: '攒够18个月缓冲金再辞职' },
    { dimension: 'B', conclusion: '攒够18个月存款再走' },
    { dimension: 'C', conclusion: '存款攒够18个月再辞职' },
    { dimension: 'D', conclusion: '直接裸辞去创业' }
  ])], s);
  var out = run(['signal', '1'], s);
  assert.ok(out.indexOf('弱信号') >= 0, '应检测出弱信号: ' + out);
  assert.ok(out.indexOf('3/4') >= 0, '大簇 3/4: ' + out);
  assert.ok(out.indexOf('1/4') >= 0, '小簇 1/4: ' + out);
  cleanup(s);
});

test('converge：相邻两层结论相似 → 收敛', function () {
  var s = newState();
  run(['init', '--strategy', 'beam', '--beam', '2', '--depth', '2', '问题'], s);
  run(['expand', 'n_1', JSON.stringify([
    { dimension: 'A', conclusion: '先验证需求再全职' }
  ])], s);
  run(['expand', 'n_2', JSON.stringify([
    { dimension: 'A1', conclusion: '验证需求后再全职投入' }
  ])], s);
  var out = run(['converge'], s);
  assert.ok(out.indexOf('已收敛') >= 0, out);
  cleanup(s);
});

test('converge：两层结论不同 → 未收敛', function () {
  var s = newState();
  run(['init', '--strategy', 'beam', '--beam', '2', '--depth', '2', '问题'], s);
  run(['expand', 'n_1', JSON.stringify([
    { dimension: 'A', conclusion: '先攒18个月缓冲金' }
  ])], s);
  run(['expand', 'n_2', JSON.stringify([
    { dimension: 'A1', conclusion: '去南极开咖啡馆' }
  ])], s);
  var out = run(['converge'], s);
  assert.ok(out.indexOf('未收敛') >= 0, out);
  cleanup(s);
});

test('save/load 状态迁移', function () {
  var s1 = newState(), s2 = newState();
  run(['init', '--strategy', 'beam', '--beam', '3', '--depth', '2', '可迁移问题'], s1);
  var out = run(['save', s2], s1);
  assert.ok(out.indexOf('已保存') >= 0, out);
  var out2 = run(['load', s2], s1);
  assert.ok(out2.indexOf('可迁移问题') >= 0, out2);
  cleanup(s1);
  cleanup(s2);
});

test('render 显示状态徽章', function () {
  var s = newState();
  run(['init', '--strategy', 'beam', '--beam', '2', '--depth', '2', '问题'], s);
  run(['expand', 'n_1', JSON.stringify([{ dimension: 'A', conclusion: 'x' }])], s);
  run(['score', 'n_2', '{"evidence":1,"relevance":1,"novelty":1,"verifiable":1}'], s);
  var out = run(['render'], s);
  assert.ok(out.indexOf('评分') >= 0, out);
  assert.ok(out.indexOf('🌳') >= 0, out);
  cleanup(s);
});

test('回归：剪枝/死路节点禁止继续展开（剪枝语义=不再投入 token）', function () {
  var s = newState();
  run(['init', '--strategy', 'beam', '--beam', '1', '--depth', '3', '问题'], s);
  run(['expand', 'n_1', JSON.stringify([
    { dimension: 'A', conclusion: 'a' },
    { dimension: 'B', conclusion: 'b' }
  ])], s);
  run(['score', 'n_2', '{"evidence":1,"relevance":1,"novelty":1,"verifiable":1}'], s);
  run(['score', 'n_3', '{"evidence":0.1,"relevance":0.1,"novelty":0.1,"verifiable":0.1}'], s);
  run(['prune'], s);

  // 剪枝节点 expand 应被硬性拒绝（退出码非 0）
  var threw = false;
  try {
    run(['expand', 'n_3', JSON.stringify([{ dimension: 'B1', conclusion: 'x' }])], s);
  } catch (e) { threw = true; }
  assert.ok(threw, '剪枝节点展开应被拒绝');
  var st = JSON.parse(fs.readFileSync(s, 'utf8'));
  assert.strictEqual(st.nodes.n_3.status, 'pruned', '状态应保持 pruned');
  assert.ok(!st.nodes.n_4, '不应产生子节点');
  cleanup(s);
});

test('回归：死路节点禁止继续展开', function () {
  var s = newState();
  run(['init', '--strategy', 'beam', '--beam', '3', '--depth', '3', '问题'], s);
  run(['expand', 'n_1', JSON.stringify([{ dimension: 'A', conclusion: 'a' }])], s);
  run(['backtrack', 'n_2', '前提被证伪'], s);
  var threw = false;
  try {
    run(['expand', 'n_2', JSON.stringify([{ dimension: 'A1', conclusion: 'x' }])], s);
  } catch (e) { threw = true; }
  assert.ok(threw, '死路节点展开应被拒绝');
  cleanup(s);
});

test('回归：consensus 节点渲染显示结论文本而非 [object Object]', function () {
  var s = newState();
  run(['init', '--strategy', 'beam', '--beam', '4', '--depth', '3', '问题'], s);
  run(['expand', 'n_1', JSON.stringify([
    { type: 'consensus', dimension: '共识', hypothesis: '', reasoning: '', conclusion: '这是本轮共识结论' }
  ])], s);
  var out = run(['render'], s);
  assert.ok(out.indexOf('这是本轮共识结论') >= 0, out);
  assert.ok(out.indexOf('[object Object]') < 0, '不应出现 [object Object]: ' + out);
  cleanup(s);
});

/* ---------- runner ---------- */
var failed = 0;
tests.forEach(function (t) {
  try {
    t.fn();
    console.log('  ✅ ' + t.name);
  } catch (e) {
    failed++;
    console.log('  ❌ ' + t.name);
    console.log('     ' + String(e.message).split('\n').join('\n     '));
  }
});
console.log('\n' + (tests.length - failed) + '/' + tests.length + ' 通过');
process.exit(failed ? 1 : 0);
