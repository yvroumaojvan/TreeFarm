#!/usr/bin/env node
/**
 * innovation_benchmark.js —— Innovation Benchmark 引擎 V1（科研分支）
 *
 * 背景（GPT 审 #7）：innovation_benchmark.json 目前只是「数据集」（任务数据+baseline_code+说明），
 * 本文件把它升级为可自动运行的「引擎骨架」：
 *   自动读取 benchmark → 跑 baseline → 输出基准表 → 接入候选（验证+对比+存档记忆）
 *
 * 用法：
 *   node innovation_benchmark.js list
 *   node innovation_benchmark.js run <id> [--candidate-code '<代码>']
 *       [--func f --test-cases '[{"input":[...],"expected":..}]']
 *       [--repeat N] [--memory <file>] [--measure time|score]
 * 说明：run 不带候选 = 只跑 baseline 基准（校准用）；带候选 = 完整实验闭环。
 */
'use strict';
const { execFileSync } = require('child_process');
const fs = require('fs');
const path = require('path');

const DIR = __dirname;
const BENCH = path.join(DIR, 'innovation_benchmark.json');
const PY = process.env.PY || 'python3';

function runPython(...args) {
  return JSON.parse(execFileSync(PY, [path.join(DIR, 'experiment_runner.py'), ...args], { encoding: 'utf8' }));
}
function runExp(...args) {
  return execFileSync('node', [path.join(DIR, 'innovation_experiment.js'), ...args], { encoding: 'utf8' });
}
function fail(msg) { console.error('❌ ' + msg); process.exit(1); }

function loadBench() {
  let d;
  try { d = JSON.parse(fs.readFileSync(BENCH, 'utf8')); } catch (e) { fail('benchmark 读取失败：' + e.message); }
  return d.benchmarks || [];
}

function listAll() {
  const bs = loadBench();
  console.log('📋 Innovation Benchmark 数据集（' + bs.length + ' 项）：');
  bs.forEach((b) => {
    console.log(`  [${b.id}] ${b.cat}类 | ${b.seed}${b.note ? ' —— ' + b.note : ''}`);
  });
  console.log('\n跑基准：node innovation_benchmark.js run <id>');
  console.log('完整实验：node innovation_benchmark.js run <id> --candidate-code \'<代码>\' [--func f --test-cases \'[...]\']');
}

function runOne(id, opts) {
  const b = loadBench().find((x) => x.id === id);
  if (!b) fail('找不到 benchmark：' + id + '（list 查看）');
  const measure = b.measure || opts.measure || 'time';
  const repeat = opts.repeat || 1;
  console.log('🧪 Benchmark ' + id + '：' + b.seed + '（' + b.cat + '类，' + measure + '）');

  // 1) baseline 基准
  console.log('  跑 baseline 基准……');
  const base = runPython('--code', b.baseline_code, '--measure', measure, '--repeat', String(repeat));
  if (!base.ok) fail('baseline 执行失败：' + base.error);
  const baseArr = measure === 'time' ? base.times_ms : base.values;
  const avg = (a) => a.reduce((x, y) => x + y, 0) / a.length;
  console.log('  baseline：' + baseArr.map((x) => Math.round(x * 100) / 100).join(', ') +
    '（均值 ' + Math.round(avg(baseArr) * 100) / 100 + '）');

  // 2) 无候选 → 到此为止（基准校准）
  if (!opts.candidateCode) {
    console.log('\n✅ baseline 基准完成（用 --candidate-code 接候选即可跑完整实验）');
    return;
  }

  // 3) 有候选 → 走完整实验闭环（正确性验证 + 指标对比 + 存记忆）
  const scheme = {
    seed: b.seed, form: 'Benchmark:' + id, idea: opts.idea || ('针对 ' + id + ' 的候选方案'),
    code: opts.candidateCode, baseline_code: b.baseline_code,
    measure, func: opts.func, test_cases: opts.testCases,
    different: true, novelty_check: b.note || '',
  };
  const expArgs = ['--scheme', JSON.stringify(scheme), '--repeat', String(repeat)];
  if (opts.memory) expArgs.push('--memory', opts.memory);
  try {
    console.log(runExp(...expArgs));
  } catch (e) {
    console.error('⚠️ 实验桥异常：' + e.message);
    process.exit(1);
  }
}

function main() {
  const args = process.argv.slice(2);
  const cmd = args[0];
  const opts = { repeat: 1 };
  for (let i = 0; i < args.length; i++) {
    if (args[i] === '--candidate-code') opts.candidateCode = args[++i];
    else if (args[i] === '--func') opts.func = args[++i];
    else if (args[i] === '--test-cases') opts.testCases = JSON.parse(args[++i]);
    else if (args[i] === '--repeat') opts.repeat = Math.max(1, parseInt(args[++i], 10) || 1);
    else if (args[i] === '--memory') opts.memory = args[++i];
    else if (args[i] === '--measure') opts.measure = args[++i];
    else if (args[i] === '--idea') opts.idea = args[++i];
  }
  if (!cmd) { console.log('🧪 Innovation Benchmark 引擎 V1\n用法：list | run <id> [--candidate-code ...]'); return; }
  if (cmd === 'list') listAll();
  else if (cmd === 'run') {
    const id = args[1];
    if (!id) fail('run 需要 benchmark id');
    runOne(id, opts);
  } else fail('未知命令：' + cmd + '（list/run）');
}

main();
