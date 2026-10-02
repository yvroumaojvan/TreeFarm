#!/usr/bin/env node
/**
 * test_deep_solution_engine.js —— v0.10 深水区测试（补单元/CLI 冒烟盲区）
 * 覆盖：TRANSFORM 随机分布 / 畸形输入磨损 / CLI 容错 / innovation_score 集成 / innovation.js 全链路联调
 * 零依赖：node:test + child_process + 临时目录隔离
 */
'use strict';
const { test } = require('node:test');
const assert = require('node:assert');
const { execFileSync } = require('node:child_process');
const path = require('node:path');
const fs = require('node:fs');
const os = require('node:os');

const DIR = __dirname;
const SE = require(path.join(DIR, 'solution_engine.js'));
const scoreMod = require(path.join(DIR, 'innovation_score.js'));

function runCli(file, args) {
  try {
    const out = execFileSync('node', [path.join(DIR, file)].concat(args), { encoding: 'utf8' });
    return { code: 0, out };
  } catch (e) {
    return { code: e.status === undefined ? -1 : e.status, out: (e.stdout || '') + (e.stderr || '') };
  }
}

const BASE = { bug_type: 'command_injection', language: 'python', victim: 'os.system' };

// ---------- 1) TRANSFORM 随机分布 ----------
test('深1. TRANSFORM：30 次调用恒 1 个、永远末尾、三个变体覆盖 ≥2 种', () => {
  const variants = new Set();
  for (let i = 0; i < 30; i++) {
    const sols = SE.buildSolutions(BASE, 5);
    assert.equal(sols.length, 5, '第 ' + i + ' 次长度应恒为 5');
    const t = sols.filter((s) => s.essence === 'TRANSFORM');
    assert.equal(t.length, 1, 'TRANSFORM 应恰好 1 个');
    assert.equal(sols[sols.length - 1].essence, 'TRANSFORM', 'TRANSFORM 永远在末尾');
    variants.add(t[0].label);
  }
  assert.ok(variants.size >= 2, '三个 TRANSFORM 变体应至少出现 2 种，实际 ' + variants.size);
});

// ---------- 2) 畸形输入磨损（内存 API） ----------
test('深2. 畸形输入：null/空/超大/中文/数字 bug_type/extreme count 全不崩且结构完整', () => {
  const weirdInputs = [
    null, undefined, {},
    { bug_type: 'whatever' },
    { bug_type: 123 },                       // 数字当类型
    { bug_type: 'race_condition', root_cause: 'x'.repeat(5000) }, // 超大根因
    { bug_type: 'xss', victim: '中文😀注入点', language: 'js' },  // 中文+emoji
    { bug_type: 'performance_n2', victim: 'search()', root_cause: '' },
  ];
  for (const [i, input] of weirdInputs.entries()) {
    const sols = SE.buildSolutions(input, 5);
    assert.ok(Array.isArray(sols) && sols.length === 5, '#' + i + ' 应返回 5 方案');
    for (const s of sols) {
      for (const f of ['order', 'label', 'essence', 'approach', 'pros', 'cons', 'impact', 'compat', 'impl_hint']) {
        assert.ok(s[f] !== undefined && s[f] !== null, '#' + i + ' 方案缺字段 ' + f);
      }
      assert.ok(Array.isArray(s.pros) && Array.isArray(s.cons), 'pros/cons 应为数组');
    }
  }
  // 极端 count 不崩
  for (const c of [-5, 0, 1, 1000, 'abc', NaN]) {
    const sols = SE.buildSolutions(BASE, c);
    assert.ok(sols.length >= 3 && sols.length <= 7, 'count=' + c + ' 应 clamp 3..7，实际 ' + sols.length);
  }
});

// ---------- 3) CLI 畸形容错 ----------
test('深3. CLI：畸形 JSON/空串/数组/count abc 全部合理退出', () => {
  // 坏 JSON → exit 2
  assert.equal(runCli('solution_engine.js', ['analyze', '{bad']).code, 2);
  // 空字符串参数 → exit 2（JSON.parse('') 抛错）
  assert.equal(runCli('solution_engine.js', ['analyze', '']).code, 2);
  // 合法 JSON 但畸形（数字） → 不崩
  const r1 = runCli('solution_engine.js', ['analyze', '{"bug_type":123}']);
  assert.equal(r1.code, 0);
  // 数组 JSON → 不崩
  const r2 = runCli('solution_engine.js', ['analyze', '[1,2,3]']);
  assert.equal(r2.code, 0);
  // --count abc → 回退默认 5
  const r3 = runCli('solution_engine.js', ['analyze', '{"bug_type":"xss"}', '--count', 'abc']);
  assert.equal(r3.code, 0);
  // expand 坏 JSON → exit 2
  assert.equal(runCli('solution_engine.js', ['expand', 'nope']).code, 2);
  // 没有参数值（--count 后面没数字）
  const r4 = runCli('solution_engine.js', ['analyze', '{"bug_type":"xss"}', '--count']);
  assert.equal(r4.code, 0, '--count 缺值也应回退默认');
});

// ---------- 4) innovation_score 集成 ----------
test('深4. scoreInnovation：满分证据→L4/L5，空壳→低分，非法值被拒', () => {
  // 完整证据链（对齐评分器契约：ms_all 为数组、显式 repeats/improved/reproduced）
  const full = {
    scheme: '用哈希索引替代线性查找',
    different: true,
    code: true,
    ran: true,
    improved: true,
    reproduced: true,
    repeats: 3,
    results: [102, 99, 101, 100, 100],
    baseline: 150,
    inputs: 3,                       // 契约：换输入次数=数字（数组会 Number() 成 NaN 静默退化）
    condition_keys: ['a', 'b', 'c'],
    ms_all: [[10, 11, 9], [8, 9, 9], [10, 10, 8]],
    baseline_ms_all: [[20, 21, 19], [18, 19, 20], [20, 19, 21]],
    seeds: 3,
    novelty_check: '检索无先例，排除已有方案，未见重复',
    external_verified: true,
  };
  const r = scoreMod.scoreInnovation(full);
  assert.ok(r.level === 'L4' || r.level === 'L5', '完整证据应 L4/L5，实际 ' + r.level + ' (total=' + r.total + ')');
  assert.ok(typeof r.total === 'number' && r.total >= 65);

  // 空壳 → L1
  const shell = scoreMod.scoreInnovation({ scheme: '一个想法' });
  assert.ok(['L1', 'L2'].includes(shell.level), '空壳应 L1/L2，实际 ' + shell.level);

  // 非法值被拒绝（results 塞字符串）
  const bad = scoreMod.scoreInnovation({ scheme: 'x', results: ['a', 'b'] });
  assert.ok(bad.error !== undefined || bad.level === 'L1' || bad.total === 0, '非法测量应被拒');
});

// ---------- 5) innovation.js 全链路联调（临时目录隔离） ----------
test('深5. 灵感树全链路：idea→forms→score→converge→harvest→recall 全程不崩', () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'tf-innov-'));
  const state = path.join(tmp, 'state.json');
  const hv = path.join(tmp, 'harvest.json');
  const cli = (args) => runCli('innovation.js', args.concat(['--state', state, '--harvest', hv]));
  try {
    assert.equal(cli(['idea', '如何让AI拥有灵光一现的能力']).code, 0, 'idea');
    const forms = JSON.stringify([
      { form: '诗', idea: '把内核写成意象', why: '抽象内核需要隐喻载体' },
      { form: '代码', idea: '把内核实现成接口', why: '可运行即验证' },
      { form: '比喻', idea: '用框架比喻内核', why: '降低理解门槛' },
    ]);
    assert.equal(cli(['forms', forms]).code, 0, 'forms');
    for (const id of ['n_2', 'n_3', 'n_4']) {
      assert.equal(cli(['score', id, '{"relevance":0.8,"novelty":0.9,"expressiveness":0.7,"feasibility":0.8}']).code, 0, 'score ' + id);
    }
    assert.equal(cli(['converge']).code, 0, 'converge');
    assert.equal(cli(['harvest']).code, 0, 'harvest');
    const hvExists = fs.existsSync(hv) && JSON.parse(fs.readFileSync(hv, 'utf8')).length > 0;
    assert.ok(hvExists, '果实库应生成且非空');
    assert.equal(cli(['recall', '让AI灵光一现']).code, 0, 'recall');
    assert.equal(cli(['status']).code, 0, 'status');
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

// ---------- 6) 全链路一致性 ----------
test('深6. 全链路：analyze 方案 → expand 子树 → levels 分级一致', () => {
  const analysis = { bug_type: 'memory_leak', language: 'c', victim: 'malloc(1)' };
  const sols = SE.buildSolutions(analysis, 5);
  assert.equal(sols.length, 5);
  for (const s of sols) {
    const t = SE.expandForTot(s);
    assert.equal(t.essence, s.essence, 'expand 应保留 essence');
    assert.equal(t.children.length, 5);
  }
  const lv = SE.classifyLevel(sols, {});
  assert.ok(lv.level === 'L2' || lv.level === 'L3', '5 个不同 essence 应 L2/L3，实际 ' + lv.level);
  assert.equal(lv.distinctEssences, 5);
});