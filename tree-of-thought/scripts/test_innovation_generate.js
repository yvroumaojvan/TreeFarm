#!/usr/bin/env node
'use strict';
// 方案生成器 · 单元测试（生成 + 评分 + 半径 + 与画圆模块联动）
// 用法: node test_innovation_generate.js
const path = require('path');
const gen = require(path.join(__dirname, 'innovation_generate.js'));
const circle = require(path.join(__dirname, 'innovation_irregular_circle.js'));

let pass = 0, fail = 0;
function check(name, cond, detail) {
  if (cond) { pass++; console.log('  ✅ ' + name); }
  else { fail++; console.log('  ❌ ' + name + (detail ? ' → ' + detail : '')); }
}

console.log('--- 模块可加载 ---');
check('generate 可调用', typeof gen.generate === 'function');
check('策略池有 12 个', Array.isArray(gen.STRATEGIES) && gen.STRATEGIES.length === 12);

console.log('--- 基本生成 ---');
const r1 = gen.generate('如何优化一个N平方复杂度的排序函数');
check('生成成功', r1.ok === true);
check('策略池全生成', r1.ok && r1.branches.filter(b => b._strategy).length === 12);
check('变异体存在', r1.ok && r1.branches.some(b => b._mutated), 'count=' + (r1.ok && r1.branches.length));
check('每条都有 radius', r1.ok && r1.branches.every(b => typeof b.radius === 'number'));
check('radius 在 1~10', r1.ok && r1.branches.every(b => b.radius >= 1 && b.radius <= 10));
check('每条都有 score 四维', r1.ok && r1.branches.every(b =>
  b.score && b.score.relevance >= 0 && b.score.relevance <= 10 &&
  b.score.novelty >= 0 && b.score.novelty <= 10 &&
  b.score.expressiveness >= 0 && b.score.expressiveness <= 10 &&
  b.score.feasibility >= 0 && b.score.feasibility <= 10));
check('结论非空', r1.ok && r1.branches.every(b => b.conclusion && b.conclusion.length > 0));

console.log('--- 边界情况 ---');
const r2 = gen.generate('');
check('空内核报错', r2.ok === false);
const r3 = gen.generate('啊');
check('短内核报错', r3.ok === false);

console.log('--- AI 底分影响 radius（作者钦定：AI打分+算法微调） ---');
const k1 = '同一个内核';
const low = gen.generate(k1, { aiRadius: 1 });
const high = gen.generate(k1, { aiRadius: 10 });
// 固定内核下，微调项相同 → 只比 AI 底分影响
const lowR = low.branches[0].radius, highR = high.branches[0].radius;
check('aiRadius 高分半径更高', low.ok && high.ok && highR >= lowR, 'low=' + lowR + ' high=' + highR);

console.log('--- 与画圆模块联动（生成 → 画圆） ---');
const g = gen.generate('如何让AI自己产生创新方案');
check('生成成功', g.ok === true);
if (g.ok) {
  const branches = g.branches.map(b => ({ name: b.name, radius: b.radius, note: b.note, conclusion: b.conclusion }));
  const c = circle.analyze(branches);
  check('生成产物可直接画圆', c.ok === true);
  check('圆的分支数 = 生成数', c.ok && c.meta.N === branches.length);
  check('创新度有效', c.ok && c.innovation.score >= 0 && c.innovation.score <= 100);
  check('最特殊边有效', c.ok && c.special.length >= 1);
}

console.log('--- 固定随机性（同内核同参数结果稳定） ---');
const a = gen.generate('稳定性测试内核');
const b = gen.generate('稳定性测试内核');
check('两次生成一致', JSON.stringify(a.branches.map(x => x.name)) === JSON.stringify(b.branches.map(x => x.name)));

console.log('\n========== 结果: ' + pass + ' 通过 / ' + fail + ' 失败 ==========');
process.exit(fail > 0 ? 1 : 0);
