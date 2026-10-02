#!/usr/bin/env node
/**
 * innovation_memory.js —— Innovation Memory 创新记忆库 V1（科研分支 research-v1.0）
 *
 * 背景（GPT 发展路线）：灵感果实库升级为「AI 自己做过的实验、失败过的方向、
 * 成功过的方法的长期记忆」。L4 可重复创新的核心基础设施。
 *
 * 记忆字段（比灵感果实多出实验维度）：
 *   seed / form / idea / why / score / time      ← 灵感（继承 innovation.js）
 *   hypothesis   假设
 *   code         实现代码（或代码文件路径）
 *   ran          是否真实运行
 *   results      多次实验指标数组 [..]
 *   baseline     baseline 对照指标
 *   failure_reason  失败原因（记坑，以后少走）
 *   improved     是否优于 baseline
 *   conditions   适用条件
 *   reproduced   是否换输入/种子重复验证
 *   innovation_score  创新能力评分（调 innovation_score.js 六维打分）
 *
 * 用法：
 *   node innovation_memory.js save '<json>'      # 存一条实验记忆（自动评创新分）
 *   node innovation_memory.js list               # 列出全部记忆
 *   node innovation_memory.js show <idx>         # 查看单条
 *   node innovation_memory.js stats              # 漏斗统计（Innovation Rate）
 *   node innovation_memory.js --file <path>      # 指定记忆库文件（默认 innovation_memory.json）
 */
'use strict';
const fs = require('fs');
const path = require('path');
const { scoreInnovation } = require('./innovation_score.js');

const DEFAULT_FILE = path.join(__dirname, 'innovation_memory.json');
let memFile = DEFAULT_FILE;

function loadMem() {
  try {
    const d = JSON.parse(fs.readFileSync(memFile, 'utf8'));
    if (Array.isArray(d)) return d;
  } catch (_) { /* 空库 → [] */ }
  return [];
}
function saveMem(m) {
  fs.writeFileSync(memFile, JSON.stringify(m, null, 2));
}
function fail(msg) { console.error('❌ ' + msg); process.exit(1); }

function toNumber(v) {
  if (v === undefined || v === null) return undefined;
  const n = Number(v);
  if (Number.isNaN(n) || !Number.isFinite(n)) return undefined;
  return n;
}

/** 存一条记忆：自动调用创新能力评分器六维打分 */
function cmdSave(raw) {
  let ev;
  try { ev = JSON.parse(raw); } catch (e) { fail('JSON 解析失败：' + e.message); }
  if (typeof ev !== 'object' || ev === null) fail('记忆必须是 JSON 对象');
  if (!ev.seed || !ev.form || !ev.idea) fail('记忆至少需要 seed/form/idea（灵感三要素）');
  // 评分器证据映射
  const scoreResult = scoreInnovation({
    scheme: ev.idea + '（内核：' + ev.seed + '）',
    different: ev.different,
    code: ev.code === true,
    ran: ev.ran === true,
    results: Array.isArray(ev.results) ? ev.results : undefined,
    baseline: toNumber(ev.baseline),
    inputs: ev.inputs, seeds: ev.seeds,
    novelty_check: ev.novelty_check,
    external_verified: ev.external_verified === true,
    // GPT 审（第五份）字段贯通：reproduced/variant_verified 是 L4 跨条件证据，
    // repeated_same_input 仅作为运行稳定参考（评分器门槛已收紧，不会单独进 L4）
    reproduced: ev.reproduced === true,
    variant_verified: ev.variant_verified === true,
    repeated_same_input: ev.repeated_same_input === true,
    // GPT 审（第六份）：L4 与「成功」绑定——improved 必须真实优于 baseline
    improved: ev.improved === true,
    // GPT 审（第七份）：重复实验证据链——repeats=每输入重复测量次数，
    // 评分器据此区分「3 输入×1 次」和「3 输入×5 次」（此前 repeat 执行了但没进证据）
    repeats: Number(ev.repeats) || 0,
    // v0.8（GPT 审第八份）：ms_all 原始测量数据传给评分器——证据验证器
    // 必须亲眼看到每输入 ≥3 次合法数字才认重复（否则 repeats 声明可被伪造）
    ms_all: Array.isArray(ev.ms_all) ? ev.ms_all : undefined,
  });
  const mem = {
    seed: String(ev.seed), form: String(ev.form), idea: String(ev.idea),
    why: ev.why !== undefined ? String(ev.why) : '',
    score: toNumber(ev.score) !== undefined ? ev.score : (ev.scoreH || 0),
    time: new Date().toISOString(),
    hypothesis: ev.hypothesis !== undefined ? String(ev.hypothesis) : '',
    code: ev.code !== undefined ? ev.code : false,
    ran: ev.ran === true,
    results: Array.isArray(ev.results) ? ev.results.map(Number) : [],
    baseline: toNumber(ev.baseline),
    failure_reason: ev.failure_reason !== undefined ? String(ev.failure_reason) : '',
    improved: ev.improved === true,
    conditions: ev.conditions !== undefined ? String(ev.conditions) : '',
    reproduced: ev.reproduced === true,
    // v0.7：重复实验证据全量保存（科研透明：不把原始测量压扁成中位数就丢了证据链）
    repeats: Number(ev.repeats) || 0,
    ms_all: Array.isArray(ev.ms_all) ? ev.ms_all : undefined,
    innovation: scoreResult.error ? { error: scoreResult.error } : scoreResult,
  };
  const m = loadMem();
  m.push(mem);
  saveMem(m);
  console.log('🧠 创新记忆已存档（第 ' + m.length + ' 条）');
  console.log('  灵感：[' + mem.form + '] ' + mem.idea);
  if (!scoreResult.error) {
    console.log('  创新评分：' + scoreResult.total + '/100 → 等级 ' + scoreResult.level);
    console.log('  六维：新想法' + scoreResult.dims.novelty + ' 组合' + scoreResult.dims.combination +
      ' 可执行' + scoreResult.dims.executable + ' 实验' + scoreResult.dims.experiment +
      ' 稳健' + scoreResult.dims.robustness + ' 证据' + scoreResult.dims.evidence);
  } else {
    console.log('  ⚠️ 创新评分拒绝：' + scoreResult.error);
  }
}

function cmdList() {
  const m = loadMem();
  if (!m.length) { console.log('🍂 记忆库还是空的——用 save 存第一条实验记忆吧'); return; }
  console.log('🧠 创新记忆库共 ' + m.length + ' 条：');
  m.forEach((x, i) => {
    const lv = x.innovation && !x.innovation.error ? x.innovation.level : '?';
    const ok = x.improved ? '✅优于baseline' : (x.ran ? '↩️运行过' : '💡仅灵感');
    console.log(`  [${i}] ${lv} ${ok} | ${String(x.form).slice(0, 18)} | ${String(x.idea).slice(0, 30)}`);
  });
}

function cmdShow(idx) {
  const m = loadMem();
  const i = Number(idx);
  if (!Number.isInteger(i) || i < 0 || i >= m.length) fail('索引越界：记忆库共 ' + m.length + ' 条');
  console.log(JSON.stringify(m[i], null, 2));
}

/** Innovation Rate 漏斗统计（GPT 定义：生成→初筛→可运行→有效→超baseline→重复成立→真新颖） */
function cmdStats() {
  const m = loadMem();
  if (!m.length) { console.log('🍂 记忆库空——先 save 实验记忆，stats 才能算 Innovation Rate'); return; }
  const total = m.length;
  const ran = m.filter((x) => x.ran).length;
  const hasBaseline = m.filter((x) => x.baseline !== undefined && x.baseline !== null).length;
  const improved = m.filter((x) => x.improved).length;
  const reproduced = m.filter((x) => x.reproduced).length;
  const l4 = m.filter((x) => x.innovation && !x.innovation.error && x.innovation.level === 'L4').length;
  const l3 = m.filter((x) => x.innovation && !x.innovation.error && x.innovation.level === 'L3').length;
  // 有效创新 = 重复验证成立 或 创新等级≥L4（去重，不重复计数）
  const effective = m.filter((x) => x.reproduced ||
    (x.innovation && !x.innovation.error && ['L4', 'L5'].indexOf(x.innovation.level) >= 0)).length;
  console.log('📊 Innovation Rate 漏斗（基于记忆库 ' + total + ' 条）：');
  console.log('  灵感候选   : ' + total);
  console.log('  ├ 真实运行 : ' + ran + '   (' + (total ? Math.round(ran / total * 1000) / 10 : 0) + '%)');
  console.log('  ├ 有baseline: ' + hasBaseline);
  console.log('  ├ 优于baseline: ' + improved);
  console.log('  ├ 重复验证 : ' + reproduced);
  console.log('  └ 创新等级 : L3×' + l3 + '  L4×' + l4);
  console.log('  Innovation Rate(有效创新/总数) = ' + effective + '/' + total +
    ' ≈ ' + (total ? Math.round(effective / total * 10000) / 100 : 0) + '%');
}

function main() {
  const args = process.argv.slice(2);
  // --file 可出现在任意位置（命令前/后都支持）
  const fileIdx = args.indexOf('--file');
  if (fileIdx >= 0) {
    memFile = args[fileIdx + 1];
    args.splice(fileIdx, 2);
  }
  if (args.length === 0) {
    console.log('🧠 Innovation Memory 创新记忆库 V1');
    console.log('用法：');
    console.log('  node innovation_memory.js save \'{json}\'   # 存记忆（自动六维创新评分）');
    console.log('  node innovation_memory.js list             # 全部记忆');
    console.log('  node innovation_memory.js show <idx>       # 单条详情');
    console.log('  node innovation_memory.js stats            # Innovation Rate 漏斗');
    console.log('  node innovation_memory.js --file <path>    # 指定记忆库文件');
    return;
  }
  const cmd = args[0];
  const rest = args.slice(1);
  if (cmd === 'save') {
    if (!rest.length) fail('save 需要记忆 JSON');
    cmdSave(rest.join(' '));
  } else if (cmd === 'list') cmdList();
  else if (cmd === 'show') {
    if (!rest.length) fail('show 需要索引');
    cmdShow(rest[0]);
  } else if (cmd === 'stats') cmdStats();
  else fail('未知命令：' + cmd + '（save/list/show/stats）');
}

main();
