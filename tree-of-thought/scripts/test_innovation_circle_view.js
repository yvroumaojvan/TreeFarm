#!/usr/bin/env node
'use strict';
// 不规则圆导出页 · 单元测试（任务快照：只读、SVG、创新度、结论）
// 用法: node test_innovation_circle_view.js
const path = require('path');
const fs = require('fs');
const os = require('os');

const view = path.join(__dirname, 'innovation_circle_view.js');
const circle = require(path.join(__dirname, 'innovation_irregular_circle.js'));

let pass = 0, fail = 0;
function check(name, cond, detail) {
  if (cond) { pass++; console.log('  ✅ ' + name); }
  else { fail++; console.log('  ❌ ' + name + (detail ? ' → ' + detail : '')); }
}

// 构造一份 circle 分析结果（模拟 innovation.js circle 命令的产物）
const branches = [
  { name: '算法创新', radius: 8, note: '弱信号检测', conclusion: '放大弱信号' },
  { name: '性能优化', radius: 5, note: '缓存', conclusion: '平稳' },
  { name: '架构设计', radius: 9, note: '树状架构', conclusion: '突破' },
  { name: '用户体验', radius: 3, note: '简化', conclusion: '温和' },
  { name: '安全防护', radius: 6, note: '沙箱', conclusion: '加固' },
  { name: '可扩展性', radius: 7, note: '插件化', conclusion: '渐进' }
];
const cr = circle.analyze(branches);
const result = Object.assign({ task: { currentRound: 2, totalRounds: 3 }, ok: true }, cr);

const tmpDir = os.tmpdir();
const inFile = path.join(tmpDir, 'circle_view_test_in.json');
const outFile = path.join(tmpDir, 'circle_view_test_out.html');
fs.writeFileSync(inFile, JSON.stringify(result, null, 2));

// 跑导出（子进程）
const { execFileSync } = require('child_process');
let ok = true, stderr = '';
try {
  execFileSync('node', [view, inFile, outFile], { stdio: 'pipe', encoding: 'utf8' });
} catch (e) {
  ok = false;
  stderr = String(e.stderr || e.message);
}
check('导出成功', ok, stderr.slice(0, 120));

if (ok) {
  const html = fs.readFileSync(outFile, 'utf8');
  check('HTML 非空', html.length > 5000, 'len=' + html.length);
  check('含标题', html.indexOf('蝴蝶效应') >= 0);
  check('含任务快照 第2轮/共3轮', /第 2 轮 \/ 共 3 轮/.test(html));
  check('含 SVG 图形', (html.match(/<svg/g) || []).length >= 2);
  check('含创新度', html.indexOf('创新度') >= 0);
  check('含最特殊边', html.indexOf('最特殊边') >= 0);
  check('含分支数据', html.indexOf('分支数据') >= 0);
  check('无外部依赖（自包含）', html.indexOf('http://') < 0 && html.indexOf('https://') < 0);
  check('用户不可改（快照无输入框）', html.indexOf('<input') < 0);
}

// 清理
try { fs.unlinkSync(inFile); fs.unlinkSync(outFile); } catch (e) {}

console.log('\n========== 结果: ' + pass + ' 通过 / ' + fail + ' 失败 ==========');
process.exit(fail > 0 ? 1 : 0);
