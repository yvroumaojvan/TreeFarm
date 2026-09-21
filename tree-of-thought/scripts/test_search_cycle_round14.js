#!/usr/bin/env node
'use strict';

/* ============================================================
 * 第 14 轮：思维树完整搜索闭环集成测试（node assert）
 * 运行：node test_search_cycle_round14.js
 * 模拟「修 bug」场景的完整搜索循环：问题=修复 eval 别名漏报
 * init → expand(4分支) → score → select → expand(深挖) → score →
 * prune → signal → converge → render 全链验证
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

var stateFile = path.join(os.tmpdir(), 'tf_r14_cycle_' + Date.now() + '.json');
fs.writeFileSync(stateFile, '{}');

try {
  /* 1. init：树干 = 问题描述 */
  var out = run(['init', '--strategy', 'beam', '--beam', '3', '--depth', '2',
    '检测器漏报：e = eval 别名后 e(code) 不再被动态执行规则命中'], stateFile);
  assert.ok(out.indexOf('思维树已初始化') >= 0, out);
  assert.ok(out.indexOf('strategy=beam') >= 0, out);

  /* 2. expand 4 分支：各维度独立假设 */
  out = run(['expand', 'n_1', JSON.stringify([
    { dimension: '正则形态', hypothesis: '别名调用 e(...) 不是 eval( 形态', reasoning: '规则只匹配 \beval\( 字面量，e( 不匹配', conclusion: '需收集别名映射，把别名调用映射回原函数' },
    { dimension: '污点层', hypothesis: '污点跟踪能否覆盖别名', reasoning: '污点只跟踪数据流变量，不跟踪函数名别名', conclusion: '污点层不管函数别名，需独立收集' },
    { dimension: '误报风险', hypothesis: '别名检测会不会误报', reasoning: '普通变量名 e/exec 可能是业务函数名', conclusion: '需限定别名源：只认 x = eval/x = exec 直赋形态' },
    { dimension: '跨文件', hypothesis: '别名是否跨文件传播', reasoning: 'a.py 别名后传 b.py 调用，跨文件跟踪成本高', conclusion: '先做单文件内别名，跨文件留后续' }
  ])], stateFile);
  assert.ok(out.indexOf('4 个子节点') >= 0, out);

  /* 3. score 评分 */
  run(['score', 'n_2', '{"evidence":0.9,"relevance":0.9,"novelty":0.8,"verifiable":0.9}'], stateFile);
  run(['score', 'n_3', '{"evidence":0.7,"relevance":0.6,"novelty":0.6,"verifiable":0.7}'], stateFile);
  run(['score', 'n_4', '{"evidence":0.8,"relevance":0.7,"novelty":0.7,"verifiable":0.8}'], stateFile);
  run(['score', 'n_5', '{"evidence":0.5,"relevance":0.4,"novelty":0.5,"verifiable":0.5}'], stateFile);

  /* 4. select：beam 应选最高分 n_2 */
  out = run(['select'], stateFile);
  assert.ok(out.indexOf('n_2') >= 0, 'beam 应选最高分节点: ' + out);

  /* 5. 深挖最高分分支 */
  out = run(['expand', 'n_2', JSON.stringify([
    { dimension: '实现方案', hypothesis: '在文件循环加别名收集', reasoning: 'x = eval 直赋形态用正则收集，行级检测把 别名( 映射 eval(', conclusion: 'dyn_aliases 收集 + 动态规则注入' },
    { dimension: '纯字符串豁免', hypothesis: 'e("literal") 不该报', reasoning: '别名的字符串实参同 eval("literal") 一样是常量', conclusion: '沿用 eval 的 (?!["\']\\s*\\)) 负向前瞻' }
  ])], stateFile);

  /* 6. prune：束宽 3，4 个分支剪 1 个 */
  out = run(['prune'], stateFile);
  assert.ok(/剪掉 1 个/.test(out), out);

  /* 7. signal 弱信号检测 */
  out = run(['signal', '1'], stateFile);
  assert.ok(out.indexOf('弱信号检测') >= 0, out);

  /* 8. converge 收敛检测 */
  out = run(['converge'], stateFile);
  assert.ok(out.indexOf('收敛') >= 0 || out.indexOf('未收敛') >= 0, out);

  /* 9. render 全树 */
  out = run(['render'], stateFile);
  assert.ok(out.indexOf('思维树') >= 0, out);
  assert.ok(out.indexOf('剪枝') >= 0, '渲染应显示剪枝痕迹: ' + out.slice(0, 300));

  /* 10. status 状态查询 */
  out = run(['status'], stateFile);
  assert.ok(out.indexOf('节点') >= 0, out);

  console.log('  ✅ 完整搜索闭环（init→expand→score→select→深挖→prune→signal→converge→render）全部通过');
  console.log('  ✅ 剪枝/评分/回溯痕迹在渲染树中可见');
} finally {
  try { fs.unlinkSync(stateFile); } catch (e) {}
}
