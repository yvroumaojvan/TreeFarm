#!/usr/bin/env node
'use strict';
// 问题自评器 · 单元测试（AI 自我评估：要不要搜索？作者 2026-10-13 钦定）
// 用法: node test_innovation_assess.js
const path = require('path');
const mod = require(path.join(__dirname, 'innovation_assess.js'));

let pass = 0, fail = 0;
function check(name, cond, detail) {
  if (cond) { pass++; console.log('  ✅ ' + name); }
  else { fail++; console.log('  ❌ ' + name + (detail ? ' → ' + detail : '')); }
}

console.log('--- 模块可加载 ---');
check('assess 可调用', typeof mod.assess === 'function');

console.log('--- 三档阈值（作者钦定 20%/60%） ---');
// 预测未来 → 必须搜索（≥60）
const r1 = mod.assess('帮我预测未来10年内整个世界局势最大的变故以及AI发展的方向');
check('预测类必须搜索', r1.ok && r1.needSearch >= 60 && r1.verdict.action === 'search', 'score=' + (r1.ok && r1.needSearch));
// 最新消息 → 建议搜索（20~60）
const r2 = mod.assess('最新消息：美伊战争现在什么状态了？');
check('最新消息建议搜索', r2.ok && r2.needSearch >= 20 && r2.needSearch < 60 && r2.verdict.action === 'maybe', 'score=' + (r2.ok && r2.needSearch));
// 编程基础 → 直接回答（≤20）
const r3 = mod.assess('Python里print函数怎么用？');
check('编程基础直接回答', r3.ok && r3.needSearch <= 20 && r3.verdict.action === 'answer', 'score=' + (r3.ok && r3.needSearch));
// 简单常识 → 直接回答
const r4 = mod.assess('数学里加减乘除是什么？');
check('简单常识直接回答', r4.ok && r4.verdict.action === 'answer');

console.log('--- 边界情况 ---');
const r5 = mod.assess('');
check('空问题报错', r5.ok === false);
const r6 = mod.assess('啊');
check('短问题报错', r6.ok === false);

console.log('--- 维度输出 ---');
const r7 = mod.assess('世界局势的最新预测');
check('四维度齐全', r7.ok &&
  typeof r7.timeSensitive === 'number' && typeof r7.complexity === 'number' &&
  typeof r7.knowledge === 'number' && typeof r7.factual === 'number');
check('needSearch 0-100', r7.ok && r7.needSearch >= 0 && r7.needSearch <= 100);
check('verdict 有行动指引', r7.ok && ['search', 'maybe', 'answer'].indexOf(r7.verdict.action) >= 0);

console.log('\n========== 结果: ' + pass + ' 通过 / ' + fail + ' 失败 ==========');
process.exit(fail > 0 ? 1 : 0);
