#!/usr/bin/env node
/**
 * solution_engine.js —— v0.10 Innovation Engine（GPT 方向策略落地）
 *
 * 核心一件事：给定一个真实 Bug/性能问题/设计问题，让 TreeFarm 产生
 * 【多个本质不同】的解决思路——而不是同一种修复的不同措辞。
 *
 * 设计：
 *  - ESSENCES：16 种"解决思路原型"（本质不同的修复路径），保证跨方案概念级差别
 *  - buildSolutions：按 bug 类型挑适用 essence + 补一个"跳出函数层"的方案
 *  - 差异度保证：essence 全局唯一（本质不同 = 概念级，不靠措辞）
 *  - expandForTot：给每个方案生成 TreeOfThought 展开骨架（优点/缺点/影响/兼容/实现）
 *  - classifyLevel：产品化 4 级（L1 替代方案 / L2 组合方案 / L3 代码级创新 / L4 验证型创新）
 *  - 用户看不到 85/100、L5、evidence_score（GPT 策略：验证器藏在最后 Verification）
 *
 * 用法：
 *   node solution_engine.js analyze '{"bug_type":"command_injection","language":"python","victim":"os.system","root_cause":"用户输入直接拼进 shell"}' [--count 5]
 *   node solution_engine.js expand  '<方案JSON>'
 *   node solution_engine.js levels  '{"solutions":[...],"evidence":{...}}'
 */
'use strict';

// ---------------------------------------------------------------------------
// I. 解决思路原型库（essence）：本质不同的修复路径
// ---------------------------------------------------------------------------
const ESSENCES = [
  { key: 'INPUT_VALIDATION', name: '输入校验/白名单', desc: '在入口拦截非法输入', fix: '在入口对用户输入做白名单/格式/长度校验，非法输入直接拒绝（第一道闸）' },
  { key: 'PARAMETERIZED', name: '参数化执行', desc: '拼接改参数传递，阻断注入', fix: '用参数数组/预编译语句替代字符串拼接——外部输入永远作为数据而非代码' },
  { key: 'API_REPLACEMENT', name: '替换安全 API', desc: '用带防护的官方 API', fix: '弃用高风险接口，改用官方安全替代（自带转义/校验/沙箱职责）' },
  { key: 'BOUNDS_CHECK', name: '边界检查', desc: '越界/空值/负数三分校验', fix: '访问数组/容器前检查越界、判空、非负；外部尺寸一律 clamp' },
  { key: 'DATA_STRUCTURE', name: '换数据结构', desc: '哈希表/树/索引替代线性', fix: '把 O(n) 查找换成哈希/二叉索引/前缀树，以空间换时间' },
  { key: 'ALGORITHM_CHANGE', name: '换算法', desc: '双指针/分治/预处理替代朴素', fix: '识别热点后换复杂度更优的算法（双指针/分治/单调栈/滑动窗口）' },
  { key: 'CACHING', name: '缓存/记忆化', desc: '重复计算只算一次', fix: '对重复高热计算加缓存/记忆化/结果表，失效策略按数据特征定' },
  { key: 'PREPROCESS', name: '预处理/归一化', desc: '先把数据整理成好算的形状', fix: '计算前先归一化/排序/去重/索引构建，把多次查询变一次预处理' },
  { key: 'BATCH', name: '批处理/分块', desc: '大任务切小块逐批推进', fix: '把一次性大操作切成可暂停/可续作的批次，控制单次资源占用' },
  { key: 'PARALLEL', name: '并行化', desc: '独立任务并发执行', fix: '无重叠依赖的任务拆到多线程/多进程并行，注意合并开销' },
  { key: 'CONSISTENCY', name: '一致性/原子性', desc: '共享状态必须原子更新', fix: '对共享状态加事务/锁/原子操作，读-改-写必须成对且互斥' },
  { key: 'ARCHITECTURE', name: '调用链重构', desc: '让外部输入够不到执行节点', fix: '加防腐层/中间层，外部输入在到达危险节点前已被净化（最彻底）' },
  { key: 'STATE_MACHINE', name: '状态机/幂等', desc: '隐式时序变显式状态', fix: '把隐式顺序逻辑改成显式状态机，重复调用幂等无副作用' },
  { key: 'RESOURCE', name: '资源管理', desc: '连接/内存/句柄全周期', fix: '统一资源获取与释放（try-with-resources/连接池/懒加载/超时回收）' },
  { key: 'FALLBACK', name: '降级/兜底', desc: '主路径失败有安全退路', fix: '主方案失败降级到保守路径（默认值/只读/拒绝服务但不崩溃）' },
  { key: 'OBSERVABILITY', name: '可观测/守卫', desc: '出错当场记录不静默', fix: '关键路径加日志/断言/哨兵，异常带上下文可定位' },
];

// GPT 强调的"跳出函数层"：不是优化这个函数，而是改变整个调用方式
const TRANSFORM_LEVELS = [
  '改变调用方式：不修函数本体，改调用侧的数据组织/参数形态，让函数天然更快/更安全',
  '改变数据模型：换一种根本不产生该问题的数据形态（重新定义问题边界）',
  '移动边界：把计算/校验移到更早（写入时校验而非读取时）或更晚（延迟到确需时）',
];

// bug_type → 优先 essence（可行性预筛：命令注入不推"并行化"，性能问题不推"白名单"）
const BUG_MAP = {
  command_injection: ['PARAMETERIZED', 'INPUT_VALIDATION', 'API_REPLACEMENT', 'ARCHITECTURE', 'FALLBACK'],
  sql_injection: ['PARAMETERIZED', 'INPUT_VALIDATION', 'API_REPLACEMENT', 'ARCHITECTURE'],
  xss: ['INPUT_VALIDATION', 'API_REPLACEMENT', 'ARCHITECTURE', 'OBSERVABILITY'],
  path_traversal: ['INPUT_VALIDATION', 'BOUNDS_CHECK', 'ARCHITECTURE', 'FALLBACK'],
  null_deref: ['BOUNDS_CHECK', 'FALLBACK', 'OBSERVABILITY', 'STATE_MACHINE'],
  out_of_bounds: ['BOUNDS_CHECK', 'INPUT_VALIDATION', 'PREPROCESS', 'FALLBACK'],
  race_condition: ['CONSISTENCY', 'STATE_MACHINE', 'ARCHITECTURE', 'OBSERVABILITY'],
  performance_n2: ['ALGORITHM_CHANGE', 'DATA_STRUCTURE', 'CACHING', 'PREPROCESS', 'BATCH'],
  performance_io: ['CACHING', 'BATCH', 'PARALLEL', 'PREPROCESS', 'RESOURCE'],
  memory_leak: ['RESOURCE', 'BATCH', 'ARCHITECTURE', 'FALLBACK'],
  deadlock: ['CONSISTENCY', 'STATE_MACHINE', 'ARCHITECTURE', 'FALLBACK'],
  design_flaw: ['ARCHITECTURE', 'STATE_MACHINE', 'DATA_STRUCTURE', 'API_REPLACEMENT', 'OBSERVABILITY'],
};
const GENERIC_FALLBACK = ['INPUT_VALIDATION', 'BOUNDS_CHECK', 'OBSERVABILITY', 'FALLBACK'];

function pickEssenceKeys(bugType, preferCount) {
  const pre = BUG_MAP[bugType] || GENERIC_FALLBACK;
  const out = pre.slice(0, Math.max(2, preferCount - 1));
  for (const k of GENERIC_FALLBACK) if (out.length < preferCount && !out.includes(k)) out.push(k);
  for (const e of ESSENCES) if (out.length < preferCount && !out.includes(e.key)) out.push(e.key);
  return out.slice(0, preferCount);
}

// ---------------------------------------------------------------------------
// II. 方案生成（本质不同 = essence 全局唯一）
// ---------------------------------------------------------------------------
function buildSolutions(analysis, count) {
  const a = analysis || {};
  // k = 总方案数（含最后固定的 TRANSFORM），clamp 3..7；ESSENCE 占 k-1 个名额
  const k = Math.max(3, Math.min(7, Number.isInteger(count) && count >= 1 ? count : 5));
  const keys = pickEssenceKeys(String(a.bug_type || 'design_flaw'), k - 1);
  const solutions = keys.map((key, i) => {
    const e = ESSENCES.find((x) => x.key === key);
    if (!e) return null;
    return {
      order: i + 1,
      label: e.name,
      essence: e.key,
      essence_desc: e.desc,
      approach: e.fix + (a.victim ? ' ——针对「' + a.victim + '」' : '') + (a.root_cause ? '（根因：' + truncate(a.root_cause, 40) + '）' : ''),
      pros: [e.desc.replace('的', '，'), '改动面' + (['ARCHITECTURE', 'STATE_MACHINE', 'API_REPLACEMENT'].includes(e.key) ? '较大但根治' : '可控')],
      cons: ['需要' + (i === 0 ? '明确输入边界' : '针对性回归')],
      impact: i === 0 ? '核心路径' : '按调用点冒烟',
      compat: '低风险兼容' + (['ARCHITECTURE'].includes(e.key) ? '（接口族变化需适配）' : ''),
      impl_hint: '见 Host 填充：给出该 essence 在本代码上的最小实现改动',
    };
  }).filter(Boolean);
  // 补一个"跳出函数层"方案（GPT：甚至不是优化这个函数，而是改变整个调用方式）
  const tx = TRANSFORM_LEVELS[Math.floor(Math.random() * TRANSFORM_LEVELS.length)];
  solutions.push({
    order: k,
    label: '跳出函数层：' + tx.slice(0, 16) + '…',
    essence: 'TRANSFORM',
    essence_desc: '改变调用方式/数据模型/边界位置',
    approach: tx + (a.victim ? '（围绕「' + a.victim + '」重新组织调用侧）' : ''),
    pros: ['可能彻底消除一类问题，不止修这一个点', '方案空间最大'],
    cons: ['重构面广，需要回归整个调用链'],
    impact: '全局',
    compat: '高风险（需整体测试）',
    impl_hint: '见 Host 填充：描述调用侧/数据模型的重构方案',
  });
  autofill(analysis, solutions);
  return solutions;
}

// 差异度自检（概念级）：essence 必须全部唯一，重复即视为同一方案的不同措辞
function distinctCheck(solutions) {
  const seen = new Set();
  const dup = [];
  for (const s of solutions) {
    if (seen.has(s.essence)) dup.push(s.essence);
    seen.add(s.essence);
  }
  return {
    ok: dup.length === 0,
    distinctCount: seen.size,
    duplicates: dup,
    message: dup.length ? ('⚠️ 存在本质重复的方案（' + dup.join('、') + '）——同一修复换措辞，应合并') : '✅ 全部方案本质不同（essence ' + seen.size + ' 类）',
  };
}

// ---------------------------------------------------------------------------
// III. TreeOfThought 展开骨架（每个方案一棵子树）
// ---------------------------------------------------------------------------
function expandForTot(s) {
  return {
    node: s.label,
    essence: s.essence,
    children: [
      { aspect: '优点', seed: s.pros },
      { aspect: '缺点', seed: s.cons },
      { aspect: '影响范围', seed: [s.impact] },
      { aspect: '兼容性', seed: [s.compat] },
      { aspect: '实现', seed: [s.impl_hint], needCode: true },
    ],
  };
}

// ---------------------------------------------------------------------------
// IV. 产品化 4 级（GPT 策略：用户只看这个，不看 85/100 和 L5）
// ---------------------------------------------------------------------------
function classifyLevel(solutions, evidence) {
  const ev = evidence || {};
  const sols = Array.isArray(solutions) ? solutions : [];
  const distinct = new Set(sols.map((s) => s.essence)).size;
  let level = 1; // 至少有一个方案
  // L1 替代方案：还有什么修法？
  if (distinct >= 2 && distinctCheck(sols).ok) level = 2;
  // L2 组合方案：A+B 组合能否解决？（存在 >3 类 essence 即视为有组合空间）
  if (distinct >= 3) level = 3;
  // L3 代码级创新：生成代码并真正运行（沙箱验证过）
  if (ev.ran === true) level = 3;
  // L4 验证型创新：方案不仅能工作，而且在当前问题上确实改进（验证后端结论）
  if (ev.improved === true) level = 4;
  return {
    level: 'L' + level,
    meaning: ['L1 替代方案：还有什么修法', 'L2 组合方案：A+B 组合能不能解决', 'L3 代码级创新：生成代码并真正运行', 'L4 验证型创新：方案有效且确实改进'][level - 1],
    distinctEssences: distinct,
  };
}

function truncate(s, n) { return String(s).length > n ? String(s).slice(0, n) + '…' : String(s); }
function autofill(analysis, solutions) {
  for (const s of solutions) {
    if (analysis && analysis.language) s.language = analysis.language;
    if (analysis && analysis.problem) s.problem = analysis.problem;
  }
}

// ---------------------------------------------------------------------------
// V. CLI
// ---------------------------------------------------------------------------
function main() {
  const args = process.argv.slice(2);
  const cmd = args[0];
  const usage = () => {
    console.log('🧪 Solution Engine v0.10（Innovation Engine 骨架）');
    console.log('用法：');
    console.log('  node solution_engine.js analyze \'<分析JSON>\' [--count N]');
    console.log('  node solution_engine.js expand \'<方案JSON>\'');
    console.log('  node solution_engine.js levels \'{"solutions":[...],"evidence":{...}}\'');
    console.log('  node solution_engine.js essences');
  };
  if (!cmd) { usage(); return; }
  if (cmd === 'essences') {
    console.log('📚 解决思路原型库（' + ESSENCES.length + ' 类本质不同的修复路径）：');
    ESSENCES.forEach((e) => console.log('  ' + e.key + ' — ' + e.desc));
    return;
  }
  if (cmd === 'analyze') {
    const raw = args.slice(1).filter((x) => !x.startsWith('--'));
    let analysis = {};
    try { analysis = JSON.parse(raw[0]); } catch (e) { console.error('❌ 分析 JSON 解析失败：' + e.message); process.exit(2); }
    const ci = args.indexOf('--count');
    const n = ci >= 0 ? parseInt(args[ci + 1], 10) : 5;
    const sols = buildSolutions(analysis, n);
    const check = distinctCheck(sols);
    console.log('🎯 问题：' + (analysis.problem || analysis.bug_type || '(未命名)'));
    console.log('   ' + check.message);
    sols.forEach((s) => {
      console.log('  [' + s.order + '] ' + s.label + '（' + s.essence + '）');
      console.log('      ' + s.approach);
      console.log('      优点：' + s.pros.join('；') + ' | 缺点：' + s.cons.join('；'));
      console.log('      影响：' + s.impact + ' | 兼容：' + s.compat);
    });
    console.log('\n用 expand 展开任一方案为 ToT 子树，或用 levels 做产品化分级');
    return;
  }
  if (cmd === 'expand') {
    let s = {};
    try { s = JSON.parse(args.slice(1).filter((x) => !x.startsWith('--'))[0]); } catch (e) { console.error('❌ 方案 JSON 解析失败'); process.exit(2); }
    console.log(JSON.stringify(expandForTot(s), null, 2));
    return;
  }
  if (cmd === 'levels') {
    let d = {};
    try { d = JSON.parse(args.slice(1).filter((x) => !x.startsWith('--'))[0]); } catch (e) { console.error('❌ JSON 解析失败'); process.exit(2); }
    const r = classifyLevel(d.solutions, d.evidence);
    console.log('创新等级：' + r.level + ' —— ' + r.meaning + '（本质不同方案 ' + r.distinctEssences + ' 类）');
    return;
  }
  console.error('❌ 未知命令：' + cmd); process.exit(2);
}

module.exports = { ESSENCES, buildSolutions, distinctCheck, expandForTot, classifyLevel };
if (require.main === module) main();