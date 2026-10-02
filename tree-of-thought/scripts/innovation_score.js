#!/usr/bin/env node
/**
 * innovation_score.js —— 创新能力评分器 V1（科研分支 research-v1.0）
 *
 * 背景：外部 AI（GPT）给 TreeFarm 创新能力定制了 100 分制六维评分 + L1~L5 等级门槛。
 * 本模块把该标准落地为可执行评分器：给一次"创新实验的证据"打分，
 * 防止"GPT 觉得创新就算创新"——证据到哪一级，就算哪一级。
 *
 * 用法：
 *   node innovation_score.js <evidence.json>          # 从 JSON 文件读证据
 *   node innovation_score.js --json '{...}'          # 直接传 JSON 字符串
 *
 * 输入 evidence 字段（全部可选，缺失按 0/默认处理）：
 *   scheme:           方案描述（必填才有分）
 *   different:        是否与 baseline 方案有实质不同（true=不同方案，false=换措辞）
 *   code:             是否已实现为真实可运行代码（boolean）
 *   ran:              是否真实运行过（boolean）
 *   results:          多次实验的指标数组，如 [102, 99, 101, 100]（重复实验证据）
 *   baseline:         baseline 对照指标（数字）
 *   inputs:           换输入次数（数字）
 *   seeds:            换随机种子次数（数字）
 *   novelty_check:    新颖性检查结论（字符串；含"检索/排除/无先例/已有/重复"等证据才有分）
 *   external_verified:是否经外部独立验证（boolean）
 *
 * 六维分值（合计 100）：
 *   ① 新想法生成  15   ② 方案组合与变异 15   ③ 可执行化 15
 *   ④ 实验与验证  20   ⑤ 重复性与稳健性 20   ⑥ 新知识证据 15
 *
 * 等级（硬门槛，需达到前一级）：
 *   0-19  L1 表达新颖 | 20-39 L2 组合创新 | 40-64 L3 可执行
 *   65-84 L4 可重复  | 85-100 L5 可复现新知识
 *
 * 边界保护（GPT 实测挖的坑）：每维 clamp 0~上限；非法输入（非数字/NaN）直接拒绝。
 */
'use strict';

const MAX = {
  novelty: 15,        // ① 新想法生成
  combination: 15,    // ② 方案组合与变异
  executable: 15,     // ③ 可执行化
  experiment: 20,     // ④ 实验与验证
  robustness: 20,     // ⑤ 重复性与稳健性
  evidence: 15,       // ⑥ 新知识证据
};
const TOTAL_MAX = 100;

function clamp(v, lo, hi) {
  const n = Number(v);
  if (Number.isNaN(n) || !Number.isFinite(n)) return null; // 非法输入拒绝
  return Math.max(lo, Math.min(hi, n));
}

function clampTo(n, hi) {
  const v = clamp(n, 0, hi);
  return v === null ? null : Math.round(v * 100) / 100;
}

/** ① 新想法生成（15）：有方案 + 与 baseline 实质不同 */
function scoreNovelty(e) {
  if (!e.scheme || String(e.scheme).trim().length < 2) return 0;
  if (e.different === true) return 15;          // 明确不同方案
  if (e.different === false) return 5;           // 只是换措辞
  return 10;                                      // 未声明差异，给中间值
}

/** ② 方案组合与变异（15）：复用/重组/变形已有方法 */
function scoreCombination(e) {
  const s = String(e.scheme || '').toLowerCase();
  const comboHints = ['组合', '结合', '融合', '混合', '重组', '拼接', '改造', '变形', '变异', '扩展'];
  const hit = comboHints.filter((k) => s.includes(k));
  if (hit.length >= 2) return 15;                 // 明确多方法组合
  if (hit.length === 1) return 10;                // 单处组合/变异痕迹
  return 5;                                       // 单一方案无组合
}

/** ③ 可执行化（15）：真实代码 + 可运行（"跑过"但没有代码证据只给少量分） */
function scoreExecutable(e) {
  if (e.code === true) return 15;
  if (e.ran === true) return 6;                   // 跑过但无代码证据（不算真实现）
  return 0;
}

/** ④ 实验与验证（20）：运行 + 指标 + baseline 对比（"AI说更好"不算） */
function scoreExperiment(e) {
  if (e.ran !== true) return 0;
  const hasBaseline = e.baseline !== undefined && e.baseline !== null && Number.isFinite(Number(e.baseline));
  const hasResults = Array.isArray(e.results) && e.results.length > 0;
  if (hasResults && hasBaseline) return 20;       // 完整：跑过+指标+对照
  if (hasResults || hasBaseline) return 14;       // 跑过+单边证据
  return 8;                                       // 只跑过
}

/** ⑤ 重复性与稳健性（20）：多次实验稳定 + 换输入/换种子仍成立 */
function scoreRobustness(e) {
  let score = 0;
  const res = Array.isArray(e.results) ? e.results.filter((x) => Number.isFinite(Number(x))) : [];
  if (res.length >= 3) {
    score += 8;                                   // ≥3 次实验记录
    const min = Math.min(...res);
    const max = Math.max(...res);
    const avg = res.reduce((a, b) => a + Number(b), 0) / res.length;
    if (avg > 0 && (max - min) / avg < 0.3) score += 7; // 相对波动 <30% = 稳定
  } else if (res.length === 2) {
    score += 5;
  } else if (res.length === 1) {
    score += 2;                                   // 只有一次 = 可能偶然
  }
  // GPT 审修复：换输入/换种子分必须由真实多变体实验证明（variant_verified），
  // 纯数字声明（inputs/seeds 计数）不再给分——否则"同代码跑三次"就能冒充可重复。
  if (e.variant_verified === true) score += 5;
  // v0.7（GPT 审第七份）：重复实验证据链——每输入 ≥3 次真实重复测量才给重复分。
  // 此前 repeat 执行了但没进评分，repeat=1/2/5 分数全一样（证据被压扁成中位数）。
  if (Number(e.repeats) >= 3) score += 5;
  return clampTo(score, 20);
}

/** ⑥ 新知识证据（15）：排除已知/偶然/误判 + 外部验证 */
function scoreEvidence(e) {
  let score = 0;
  const nc = String(e.novelty_check || '');
  if (nc.trim().length >= 8) {
    score += 6;                                   // 有实质新颖性检查
    const excludeHints = ['无先例', '未见', '检索', '排除', '不是已有', '无重复', '新发现', '独立'];
    if (excludeHints.some((k) => nc.includes(k))) score += 4; // 有排除已知的证据
  }
  if (e.external_verified === true) score += 5;   // 外部独立验证
  return clampTo(score, 15);
}

/** 主评分入口 */
function scoreInnovation(evidence) {
  if (typeof evidence !== 'object' || evidence === null) {
    return { error: 'evidence 必须是 JSON 对象', total: 0, level: 'L0' };
  }
  // 边界保护：results 若存在必须是数组且元素全为有限数字（非法输入直接拒绝，防 NaN）
  if (evidence.results !== undefined && evidence.results !== null) {
    if (!Array.isArray(evidence.results)) {
      return { error: 'results 必须是数组', total: 0, level: 'L0' };
    }
    for (const x of evidence.results) {
      if (Number.isNaN(Number(x)) || !Number.isFinite(Number(x))) {
        return { error: 'results 含非法数值元素（非数字/NaN），已拒绝评分（GPT 边界保护要求）', total: 0, level: 'L0' };
      }
    }
  }
  const dims = {
    novelty: scoreNovelty(evidence),
    combination: scoreCombination(evidence),
    executable: scoreExecutable(evidence),
    experiment: scoreExperiment(evidence),
    robustness: scoreRobustness(evidence),
    evidence: scoreEvidence(evidence),
  };
  // 边界保护复查：任一维为 null（非法输入）→ 整单拒绝
  for (const k of Object.keys(MAX)) {
    if (dims[k] === null) {
      return {
        error: `维度 ${k} 收到非法数值输入（非数字/NaN/越界），已拒绝评分（GPT 边界保护要求）`,
        total: 0, level: 'L0', dims,
      };
    }
  }
  const total = Math.round(
    (dims.novelty + dims.combination + dims.executable +
     dims.experiment + dims.robustness + dims.evidence) * 100
  ) / 100;
  let level;
  // GPT 审（第五份）修复：L4 硬门槛 = 真正的跨条件可重复证据——
  // reproduced（明确声明的跨条件重复验证）或 variant_verified（真实换输入/种子实验）。
  // repeated_same_input（同输入 ≥3 次）只证明「运行稳定」，不证明「换条件仍成立」，
  // 因此不再单独作为 L4 门槛（GPT：同输入跑三次 → 系统直接回答「不够」）。
  const reproducible = (evidence.reproduced === true ||
                        evidence.variant_verified === true);
  // GPT 审（第六份）修复：L4 还要真实优于 baseline——「可重复」和「成功」必须绑定，
  // 否则一个没超过 baseline 的候选（improved=false）也能靠跨输入证据混进 L4。
  const successful = evidence.improved === true;
  if (total >= 85) {
    // L5 硬门槛：可复现的新知识必须经过外部独立验证（GPT：其他人也能得到类似结论）
    if (evidence.external_verified === true && successful) level = 'L5';
    else if (successful) level = 'L4'; // 高分但缺外部验证 → 按 L4 计（硬门槛机制）
    else level = 'L3'; // 高分但未优于 baseline → 不算可复现新知识
  } else if (total >= 65) {
    level = (reproducible && successful) ? 'L4' : 'L3'; // 缺跨条件证据或未超越 → 降 L3
  } else if (total >= 40) level = 'L3';
  else if (total >= 20) level = 'L2';
  else level = 'L1';
  const notes = [];
  if (total >= 85 && evidence.external_verified !== true) {
    notes.push('总分达到 L5 区间但缺少外部独立验证（external_verified），按硬门槛降为 L4');
  }
  if (total >= 85 && !successful) {
    notes.push('总分达到 L5 区间但未优于 baseline（improved=false），按硬门槛降为 L3');
  }
  if (total >= 65 && !(reproducible && successful)) {
    const why = !reproducible ? '缺少跨条件可重复证据（reproduced/variant_verified）' : '未优于 baseline（improved=false）';
    notes.push('总分达到 L4 区间但' + why + '，按硬门槛降为 L3');
  }
  return { dims, total, level, ...(notes.length ? { notes } : {}) };
}

/** CLI 入口 */
function main() {
  const args = process.argv.slice(2);
  let evidence;
  if (args.length === 0) {
    console.log('创新能力评分器 V1（100 分制六维，L1~L5 硬门槛）');
    console.log('用法：node innovation_score.js <evidence.json>');
    console.log('      node innovation_score.js --json \'{...}\'');
    console.log('字段：scheme/different/code/ran/results/baseline/inputs/seeds/novelty_check/external_verified');
    console.log('等级：0-19 L1 | 20-39 L2 | 40-64 L3 | 65-84 L4 | 85-100 L5');
    return;
  }
  try {
    if (args[0] === '--json') {
      evidence = JSON.parse(args[1]);
    } else {
      const fs = require('fs');
      evidence = JSON.parse(fs.readFileSync(args[0], 'utf8'));
    }
  } catch (err) {
    console.error('❌ evidence 解析失败：' + err.message);
    process.exit(1);
  }
  const result = scoreInnovation(evidence);
  if (result.error) {
    console.error('❌ ' + result.error);
    process.exit(2);
  }
  console.log(JSON.stringify(result, null, 2));
}

if (require.main === module) {
  main();
}

module.exports = { scoreInnovation, MAX, TOTAL_MAX };
