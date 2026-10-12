#!/usr/bin/env node
'use strict';
// 弱信号不规则圆 · 算法单元测试（从 HTML 版 32 用例移植 + 模块化用例）
// 用法: node test_innovation_irregular_circle.js
const path = require('path');
const ButterflyAlgo = require(path.join(__dirname, 'innovation_irregular_circle.js'));

let pass = 0, fail = 0;
function check(name, cond, detail) {
  if (cond) { pass++; console.log('  ✅ ' + name); }
  else { fail++; console.log('  ❌ ' + name + (detail ? ' → ' + detail : '')); }
}

console.log('--- 模块可加载 ---');
check('require 返回对象', typeof ButterflyAlgo === 'object' && ButterflyAlgo !== null);
check('analyze 可调用', typeof ButterflyAlgo.analyze === 'function');

console.log('--- 基础功能 ---');
// 1. 示例数据
const sample = [
  { name: '算法创新', radius: 8, note: '弱信号检测', conclusion: '放大弱信号' },
  { name: '性能优化', radius: 5, note: '缓存', conclusion: '平稳' },
  { name: '架构设计', radius: 9, note: '树状架构', conclusion: '突破' },
  { name: '用户体验', radius: 3, note: '简化', conclusion: '温和' },
  { name: '安全防护', radius: 6, note: '沙箱', conclusion: '加固' },
  { name: '可扩展性', radius: 7, note: '插件化', conclusion: '渐进' }
];
const r1 = ButterflyAlgo.analyze(sample);
check('示例6分支分析成功', r1.ok === true);
check('创新度 0-100', r1.ok && r1.innovation.score >= 0 && r1.innovation.score <= 100);
check('特殊边至少1条', r1.ok && r1.special.length >= 1);
check('最特殊边=偏离最大', r1.ok && (r1.special[0].index === r1.maxIndex));
check('重组圆顶点数=N', r1.ok && r1.recombined.length === 6);
check('结论含重点边', r1.ok && r1.conclusions.keyBranch.name === '用户体验');
check('整轮总结非空', r1.ok && r1.conclusions.summary.length > 10);
check('规则圆全等半径', r1.ok && r1.regular.every(v => v.rpx === r1.meta.meanPx));

console.log('--- 边界情况 ---');
// 2. 空数据
const r2 = ButterflyAlgo.analyze([]);
check('空数据报错不崩', r2.ok === false && /至少需要 3/.test(r2.error));
// 3. 2个分支
const r3 = ButterflyAlgo.analyze([{ name: 'a', radius: 1, note: 'x' }, { name: 'b', radius: 2, note: 'y' }]);
check('2分支报错不崩', r3.ok === false && /至少需要 3/.test(r3.error));
// 4. 全相等半径
const eq = [
  { name: 'a', radius: 5, note: 'x' }, { name: 'b', radius: 5, note: 'y' }, { name: 'c', radius: 5, note: 'z' }
];
const r4 = ButterflyAlgo.analyze(eq);
check('全相等不崩', r4.ok === true);
check('全相等创新度低(<30)', r4.ok && r4.innovation.score < 30, 'score=' + (r4.ok && r4.innovation.score));
check('全相等特殊边兜底1条', r4.ok && r4.special.length === 1);
check('全相等CV=0', r4.ok && r4.innovation.cv === 0);
// 5. 半径含0
const z0 = [
  { name: 'a', radius: 0, note: 'x' }, { name: 'b', radius: 5, note: 'y' }, { name: 'c', radius: 10, note: 'z' }
];
const r5 = ButterflyAlgo.analyze(z0);
check('半径0不崩', r5.ok === true);
check('半径0归一化OK', r5.ok && r5.irregular[0].rpx === 30 && r5.irregular[2].rpx === 140);
// 6. 负数
const neg = [
  { name: 'a', radius: -1, note: 'x' }, { name: 'b', radius: 5, note: 'y' }, { name: 'c', radius: 5, note: 'z' }
];
const r6 = ButterflyAlgo.analyze(neg);
check('负数半径报错', r6.ok === false && /非负数字/.test(r6.error));
// 7. 非数字
const nan = [
  { name: 'a', radius: 'abc', note: 'x' }, { name: 'b', radius: 5, note: 'y' }, { name: 'c', radius: 5, note: 'z' }
];
const r7 = ButterflyAlgo.analyze(nan);
check('非数字半径报错', r7.ok === false);
// 8. 名称缺失
const noname = [
  { name: '', radius: 1, note: 'x' }, { name: 'b', radius: 2, note: 'y' }, { name: 'c', radius: 3, note: 'z' }
];
const r8 = ButterflyAlgo.analyze(noname);
check('空名称报错', r8.ok === false && /名称/.test(r8.error));

console.log('--- 算法正确性 ---');
// 9. 特殊边：最大最小偏离都被识别（z>=1.2）
const sp = [
  { name: 'a', radius: 2, note: 'x' }, { name: 'b', radius: 8, note: 'y' }, { name: 'c', radius: 5, note: 'z' },
  { name: 'd', radius: 9, note: 'w' }, { name: 'e', radius: 5, note: 'v' }, { name: 'f', radius: 5, note: 'u' }
];
const r9 = ButterflyAlgo.analyze(sp);
check('特殊边包含最大值9', r9.ok && r9.special.some(s => sp[s.index].radius === 9));
check('特殊边包含最小值2', r9.ok && r9.special.some(s => sp[s.index].radius === 2));
// 10. 锐角检测：大半径顶点 → 内角变锐
const sh = [
  { name: 'a', radius: 1, note: 'x' }, { name: 'b', radius: 1, note: 'y' }, { name: 'c', radius: 1, note: 'z' },
  { name: 'd', radius: 10, note: 'w' }, { name: 'e', radius: 1, note: 'v' }, { name: 'f', radius: 1, note: 'u' }
];
const r10 = ButterflyAlgo.analyze(sh);
check('锐化角≥1', r10.ok && r10.innovation.sharp >= 1, 'sharp=' + (r10.ok && r10.innovation.sharp));
check('大半径离群被选中', r10.ok && r10.special[0].index === 3);
// 11. 重组圆：特殊边=实际半径，普通边=均值
const r11 = ButterflyAlgo.analyze(sample);
const spIdx = r11.special.map(s => s.index);
check('重组特殊边用实际半径', r11.ok && r11.recombined[spIdx[0]].radius === Number(sample[spIdx[0]].radius));
check('重组普通边用均值', r11.ok && Math.abs(r11.recombined.filter((_, i) => !spIdx.includes(i))[0].rpx - r11.meta.meanPx) < 1e-9);
// 12. 3分支三角形不误报大量锐角
const tri = [
  { name: 'a', radius: 5, note: 'x' }, { name: 'b', radius: 5, note: 'y' }, { name: 'c', radius: 5, note: 'z' }
];
const r12 = ButterflyAlgo.analyze(tri);
check('等边三角形无锐化误报', r12.ok && r12.innovation.sharp === 0, 'sharp=' + (r12.ok && r12.innovation.sharp));
// 13. 全等+等边 → 规则等级
check('全等判定为规则·平庸', r12.ok && r12.innovation.lvKey === 'r');
// 14. 极端差异 → 突破等级
const ext = [
  { name: 'a', radius: 0, note: 'x' }, { name: 'b', radius: 10, note: 'y' }, { name: 'c', radius: 10, note: 'z' },
  { name: 'd', radius: 10, note: 'w' }, { name: 'e', radius: 10, note: 'v' }, { name: 'f', radius: 10, note: 'u' }
];
const r14 = ButterflyAlgo.analyze(ext);
check('极端差异为突破等级', r14.ok && r14.innovation.lvKey === 'b', 'score=' + (r14.ok && r14.innovation.score));
// 15. 规则圆（正多边形，无凹点）内角和 ≈ (N-2)*180；不规则圆夹角均在 0~180 且含凹点不适用内角和
function totalAngles(verts) {
  return verts.map((_, i) => ButterflyAlgo.vertexAngle(verts, i)).reduce((s, a) => s + a, 0);
}
const r15 = ButterflyAlgo.analyze(sample);
const tReg = totalAngles(r15.regular);
check('规则圆内角和≈(N-2)*180', Math.abs(tReg - 720) < 1, 'sum=' + tReg.toFixed(1));
const anglesIrr = r15.irregular.map((_, i) => ButterflyAlgo.vertexAngle(r15.irregular, i));
check('不规则圆夹角均在0~180', anglesIrr.every(a => a > 0 && a <= 180));
// 16. normalizeRadii 全相等兜底
const nr = ButterflyAlgo.normalizeRadii([4, 4, 4], 30, 140);
check('全相等归一化=中值', nr.every(v => v === 85));
// 17. meanStd 正确
const ms = ButterflyAlgo.meanStd([1, 2, 3]);
check('mean=2 std≈0.816', Math.abs(ms.mean - 2) < 1e-9 && Math.abs(ms.std - Math.sqrt(2 / 3)) < 1e-9);

console.log('\n========== 结果: ' + pass + ' 通过 / ' + fail + ' 失败 ==========');
process.exit(fail > 0 ? 1 : 0);
