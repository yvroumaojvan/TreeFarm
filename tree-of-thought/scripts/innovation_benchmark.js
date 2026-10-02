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
function fail(msg) { console.error('❌ ' + msg); process.exit(2); } // 参数/环境错误 → exit 2

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

  // 1) baseline 基准：真实执行 workload（测 func(输入) 的真性能，而非定义时间）
  console.log('  跑 baseline workload（真实执行 ' + b.func + '(输入) ×' + b.inputs.length + ' 组）……');
  const base = runPython('--workload', JSON.stringify({ code: b.baseline_code, func: b.func, inputs: b.inputs }));
  if (!base.ok) fail('baseline 执行失败：' + base.error);
  base.per_input.forEach((r, i) => {
    const ok = r.ok ? '✅' : '❌';
    const extra = r.ms !== null ? r.ms + ' ms' : (r.error || '');
    const res = r.result !== null && r.result !== undefined ? ' → ' + JSON.stringify(r.result).slice(0, 50) : '';
    console.log('   输入' + (i + 1) + '：' + ok + ' ' + extra + res);
  });
  const times = base.per_input.filter((r) => r.ms !== null).map((r) => r.ms);
  if (!times.length) fail('baseline 所有输入都未产出耗时（无有效基准）');
  const avg = (a) => a.reduce((x, y) => x + y, 0) / a.length;
  console.log('   baseline 均值：' + Math.round(avg(times) * 100) / 100 + ' ms');

  // 2) 无候选 → 到此为止（基准校准）
  if (!opts.candidateCode) {
    console.log('\n✅ baseline workload 基准完成（用 --candidate-code 接候选即可跑完整实验）');
    return;
  }

  // 3) 有候选 → 走完整实验闭环（workload 公平比较 + 输出一致校验 + 存记忆）
  const scheme = {
    seed: b.seed, form: 'Benchmark:' + id, idea: opts.idea || ('针对 ' + id + ' 的候选方案'),
    code: opts.candidateCode, baseline_code: b.baseline_code,
    measure, func: b.func, inputs: b.inputs, test_cases: opts.testCases,
    different: true, novelty_check: b.note || '',
  };
  const expArgs = ['--scheme', JSON.stringify(scheme), '--repeat', String(repeat)];
  if (opts.memory) expArgs.push('--memory', opts.memory);
  try {
    console.log(runExp(...expArgs));
  } catch (e) {
    // v0.6 机器语义（GPT 审第六份）：成功→0，实验失败→1，参数/环境错误→2。
    // 用户友好中文已在 stderr，这里只透传退出码，不让 CI 把失败当成功。
    if (e.stderr) console.error(e.stderr.trim());
    process.exit(e.status === 2 ? 2 : 1);
  }
}

function main() {
  const args = process.argv.slice(2);
  const cmd = args[0];
  const opts = { repeat: 1 };
  for (let i = 0; i < args.length; i++) {
    if (args[i] === '--candidate-code') opts.candidateCode = args[++i].replace(/\\n/g, '\n');
    else if (args[i] === '--func') opts.func = args[++i];
    else if (args[i] === '--test-cases') opts.testCases = JSON.parse(args[++i]);
    else if (args[i] === '--repeat') {
      const raw = args[++i];
      // v0.9.1：--repeat 必须正整数（3.5/abc/0 → 参数错误 exit 2，不静默取整）
      if (!/^\d+$/.test(raw) || parseInt(raw, 10) < 1) {
        console.error('❌ --repeat 必须是正整数（收到：' + raw + '）——3.5 次/0 次实验没有科研意义');
        process.exit(2);
      }
      opts.repeat = parseInt(raw, 10);
    }
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
