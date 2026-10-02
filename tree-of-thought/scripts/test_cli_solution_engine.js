#!/usr/bin/env node
/**
 * test_cli_solution_engine.js —— CLI 冒烟层测试（补单元测试盲区）
 * 覆盖：命令解析、坏 JSON 退出码、真实子进程运行
 * 零依赖：node:test + child_process
 */
'use strict';
const { test } = require('node:test');
const assert = require('node:assert');
const { execFileSync } = require('node:child_process');
const path = require('node:path');

const CLI = path.join(__dirname, 'solution_engine.js');

function run(args) {
  try {
    const out = execFileSync('node', [CLI].concat(args), { encoding: 'utf8', stdio: ['pipe', 'pipe', 'pipe'] });
    return { code: 0, out };
  } catch (e) {
    return { code: e.status === undefined ? -1 : e.status, out: (e.stdout || '') + (e.stderr || '') };
  }
}

test('CLI 无参数 → 打印用法（exit 0）', () => {
  const r = run([]);
  assert.equal(r.code, 0);
  assert.ok(r.out.includes('用法'), '应打印用法');
});

test('CLI essences → 列出 16 类原型', () => {
  const r = run(['essences']);
  assert.equal(r.code, 0);
  assert.ok(r.out.includes('16 类'), '应显示 16 类');
  assert.ok(r.out.includes('PARAMETERIZED') && r.out.includes('TRANSFORM') === false, '应包含 essence key');
});

test('CLI analyze 正常：count=5 输出 5 个 [N] 方案', () => {
  const r = run(['analyze', '{"bug_type":"command_injection","language":"python","victim":"os.system"}', '--count', '5']);
  assert.equal(r.code, 0);
  const marks = r.out.split('\n').filter((l) => /^\s*\[\d+\]/.test(l)).length;
  assert.equal(marks, 5, '应恰好 5 个方案，实际 ' + marks);
  assert.ok(r.out.includes('TRANSFORM'), '末位应含跳出函数层方案');
});

test('CLI analyze 坏 JSON → exit 2 且报解析失败', () => {
  const r = run(['analyze', 'not-json{{{']);
  assert.equal(r.code, 2, '坏 JSON 应退出码 2');
  assert.ok(r.out.includes('解析失败'), '应提示解析失败');
});

test('CLI expand → 输出 ToT 骨架 JSON（node/essence/children=5）', () => {
  const r = run(['expand', '{"label":"x","essence":"CACHING","pros":["a"],"cons":["b"],"impact":"i","compat":"c","impl_hint":"h"}']);
  assert.equal(r.code, 0);
  const d = JSON.parse(r.out);
  assert.ok(d.node && d.essence && Array.isArray(d.children));
  assert.equal(d.children.length, 5);
});

test('CLI levels → 1 个方案得 L1', () => {
  const r = run(['levels', '{"solutions":[{"essence":"A"}],"evidence":{}}']);
  assert.equal(r.code, 0);
  assert.ok(r.out.includes('L1'), '应输出 L1');
});

test('CLI 未知命令 → exit 2', () => {
  const r = run(['frobnicate']);
  assert.equal(r.code, 2);
});

test('CLI analyze count=2 → clamp 到 3 个方案', () => {
  const r = run(['analyze', '{"bug_type":"null_deref"}', '--count', '2']);
  assert.equal(r.code, 0);
  const marks = r.out.split('\n').filter((l) => /^\s*\[\d+\]/.test(l)).length;
  assert.equal(marks, 3, 'count=2 应 clamp 为 3，实际 ' + marks);
});