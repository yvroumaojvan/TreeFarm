#!/usr/bin/env node
'use strict';

/* ============================================================
 * tree.js —— 思维树统一入口（抓虫树 + 灵感树 双模式）
 * 乖宝双分支打通：一个插件，两种思考方式
 *
 * 用法：
 *   node tree.js bug <tree-search.js 参数...>   抓虫树（抓 bug / 深度分析）
 *   node tree.js idea <innovation.js 参数...>   灵感树（灵光一现 / 创新）
 *   node tree.js --help                         帮助
 *
 * 例：
 *   node tree.js bug init "为什么进城掉帧" --beam 4
 *   node tree.js idea idea "如何让AI拥有灵光一现的能力"
 * ============================================================ */

var path = require('path');
var spawn = require('child_process').spawnSync;

var BUG_ENGINE = path.join(__dirname, 'tree-search.js');
var IDEA_ENGINE = path.join(__dirname, 'innovation.js');

var args = process.argv.slice(2);
var mode = args[0];

function usage() {
  console.log('🌳 思维树统一入口（抓虫树 + 灵感树）');
  console.log('');
  console.log('  node tree.js bug <参数>    抓虫树：抓 bug / 深度代码分析');
  console.log('     例：node tree.js bug init "为什么进城掉帧" --beam 4');
  console.log('  node tree.js idea <参数>   灵感树：灵光一现 / 创新');
  console.log('     例：node tree.js idea idea "如何让AI拥有灵光一现的能力"');
  console.log('');
  console.log('灵感树命令：idea → forms → score → converge → harvest → recall → render → status');
  console.log('抓虫树命令：init → expand → score → select → prune → signal → converge → render');
  process.exit(0);
}

if (!mode || mode === '--help' || mode === '-h') {
  usage();
}

var engine, engineArgs;
if (mode === 'bug') {
  engine = BUG_ENGINE;
  engineArgs = args.slice(1);
  console.log('🐛 抓虫树模式');
} else if (mode === 'idea') {
  engine = IDEA_ENGINE;
  engineArgs = args.slice(1);
  console.log('💡 灵感树模式');
} else {
  console.error('❌ 未知模式：' + mode + '（可用 bug / idea，--help 看帮助）');
  process.exit(1);
}

var r = spawn('node', [engine].concat(engineArgs), { stdio: 'inherit' });
process.exit(r.status || 0);
