#!/usr/bin/env node
'use strict';

/* ============================================================
 * innovation_irregular_circle.js —— 弱信号不规则圆（创新引擎新功能）
 * 鱼肉猫卷 2026-10-12 原创设计工程化：
 *   每个弱信号分支 = 圆的一条边（半径 = 知识强度）
 *   规则圆 vs 不规则圆对比 → 提取最特殊边 → 替换重组 → 最不规则的圆
 *   「哪个圆最不规整、凹凸最多，就代表创新最多」—— 创新 = 偏离规则的量
 *
 * 纯算法模块（无依赖，Node 直接跑），供 innovation.js / 测试 / 导出页复用。
 *
 * 用法：
 *   node innovation_irregular_circle.js --sample          # 示例数据演示
 *   node innovation_irregular_circle.js branches.json     # 从 JSON 文件读分支
 *   node innovation_irregular_circle.js --json '{...}'    # 直接传 JSON 字符串
 *
 * 分支字段：{ name: 分支名, radius: 知识强度0~10, note: 知识摘要, conclusion: 分支结论 }
 * 参数：innovation_config.json 的 circle 键（zThreshold/sharpDelta/权重/等级阈值/像素范围）
 * ============================================================ */

var fs = require('fs');
var path = require('path');

/* ---------- 配置（优先 innovation_config.json，可被参数覆盖） ---------- */
var DEFAULT_CONFIG = path.join(__dirname, 'innovation_config.json');
function loadConfig() {
  try {
    return JSON.parse(fs.readFileSync(DEFAULT_CONFIG, 'utf8'));
  } catch (e) {
    return {}; // 配置文件缺失/损坏 → 用内置默认
  }
}

/* ============ 算法主体（纯函数，无 DOM 依赖） ============ */
var ButterflyAlgo = (function () {
  // --- 输入校验 ---
  function validate(branches) {
    if (!Array.isArray(branches) || branches.length < 3) {
      return { ok: false, error: '至少需要 3 个分支才能绘制出圆（当前 ' + (branches ? branches.length : 0) + ' 个）' };
    }
    for (var i = 0; i < branches.length; i++) {
      var b = branches[i];
      if (!b || typeof b.name !== 'string' || b.name.trim() === '') return { ok: false, error: '第 ' + (i + 1) + ' 个分支缺少名称' };
      var r = Number(b.radius);
      if (!isFinite(r) || r < 0) return { ok: false, error: '「' + b.name + '」的半径必须是非负数字' };
      if (typeof b.note !== 'string') return { ok: false, error: '「' + b.name + '」缺少知识摘要' };
    }
    return { ok: true };
  }

  // --- 均值 / 标准差 ---
  function meanStd(arr) {
    var n = arr.length;
    var mean = arr.reduce(function (s, v) { return s + v; }, 0) / n;
    var sq = arr.reduce(function (s, v) { return s + (v - mean) * (v - mean); }, 0) / n;
    return { mean: mean, std: Math.sqrt(sq) };
  }

  // --- 半径 → 像素 线性归一化（含全相等兜底） ---
  function normalizeRadii(radii, minPx, maxPx) {
    var min = Math.min.apply(null, radii);
    var max = Math.max.apply(null, radii);
    var range = max - min;
    if (range === 0) {
      return radii.map(function () { return (minPx + maxPx) / 2; });
    }
    return radii.map(function (r) { return minPx + (r - min) / range * (maxPx - minPx); });
  }

  // --- 顶点生成（方向：从顶部顺时针） ---
  function buildVertices(branches, radiiPx) {
    var N = branches.length;
    return branches.map(function (b, i) {
      var angle = -Math.PI / 2 + (2 * Math.PI * i) / N;
      return {
        name: b.name, radius: Number(b.radius), rpx: radiiPx[i],
        angle: angle, ux: Math.cos(angle), uy: Math.sin(angle)
      };
    });
  }

  // --- 顶点内角（度），环绕索引 ---
  function vertexAngle(verts, i) {
    var N = verts.length;
    var a = verts[(i - 1 + N) % N], b = verts[i], c = verts[(i + 1) % N];
    var ax = a.ux * a.rpx, ay = a.uy * a.rpx;
    var bx = b.ux * b.rpx, by = b.uy * b.rpx;
    var cx = c.ux * c.rpx, cy = c.uy * c.rpx;
    var ux = ax - bx, uy = ay - by;
    var vx = cx - bx, vy = cy - by;
    var dot = ux * vx + uy * vy;
    var len = Math.hypot(ux, uy) * Math.hypot(vx, vy);
    if (len === 0) return 180;
    var cos = Math.max(-1, Math.min(1, dot / len));
    return Math.acos(cos) * 180 / Math.PI;
  }

  // --- 特殊边检测（离群：偏离均值最大 / z 分数超阈值） ---
  function detectSpecialEdges(radii, zThreshold) {
    var ms = meanStd(radii);
    var devs = radii.map(function (r) { return Math.abs(r - ms.mean); });
    var maxDev = Math.max.apply(null, devs);
    var zs = devs.map(function (d) { return d / (ms.std || 1e-9); });
    var threshold = (zThreshold == null) ? 1.2 : zThreshold;
    var special = zs.map(function (z, i) { return { index: i, z: z, dev: devs[i] }; })
      .filter(function (s) { return s.z >= threshold; })
      .sort(function (a, b) { return b.dev - a.dev; });
    if (special.length === 0) {
      special = [{ index: devs.indexOf(maxDev), z: zs[devs.indexOf(maxDev)], dev: maxDev }];
    }
    return { mean: ms.mean, std: ms.std, devs: devs, zs: zs, special: special, maxIndex: devs.indexOf(maxDev) };
  }

  // --- 规则圆顶点（等半径 = 均值映射值） ---
  function regularVertices(branches, meanPx) {
    var rpx = branches.map(function () { return meanPx; });
    return buildVertices(branches, rpx);
  }

  // --- 最不规则圆（重组：特殊边用实际半径，其余用规则基线） ---
  function recombineVertices(branches, radiiPx, meanPx, special) {
    var rpx = branches.map(function (_, i) {
      return special.some(function (s) { return s.index === i; }) ? radiiPx[i] : meanPx;
    });
    return buildVertices(branches, rpx);
  }

  // --- 创新度：离散度(CV) + 锐化角数量 → 0~100 ---
  function computeInnovation(radii, verts, p) {
    p = p || {};
    var sharpDelta = (p.sharpDelta == null) ? 25 : p.sharpDelta;
    var weightCv = (p.weightCv == null) ? 0.6 : p.weightCv;
    var weightSharp = (p.weightSharp == null) ? 0.4 : p.weightSharp;
    var levelMid = (p.levelMid == null) ? 30 : p.levelMid;
    var levelHigh = (p.levelHigh == null) ? 65 : p.levelHigh;
    var N = radii.length;
    var regularAngle = (N - 2) * 180 / N; // 正多边形内角基准
    var angles = verts.map(function (_, i) { return vertexAngle(verts, i); });
    var sharp = angles.filter(function (a) { return a < regularAngle - sharpDelta; }).length;
    var ms = meanStd(radii);
    var cv = ms.mean > 0 ? ms.std / ms.mean : 0;
    var cvNorm = Math.min(cv / 0.5, 1);
    var sharpNorm = Math.min(sharp / Math.max(1, Math.floor(N / 2)), 1);
    var score = Math.round((weightCv * cvNorm + weightSharp * sharpNorm) * 100);
    var level, lvKey;
    if (score < levelMid) { level = '规则·平庸（无刺）'; lvKey = 'r'; }
    else if (score < levelHigh) { level = '渐进·钝角（温和改进）'; lvKey = 'm'; }
    else { level = '突破·锐角（极端创新）'; lvKey = 'b'; }
    return { cv: cv, sharp: sharp, score: score, level: level, lvKey: lvKey, angles: angles, regularAngle: regularAngle };
  }

  // --- 结论规则：重点边详细 + 其他边简略 + 整轮总结 ---
  function generateConclusions(branches, special, maxIndex, innovation, mean) {
    var key = branches[maxIndex];
    var others = branches.map(function (b, i) {
      return i === maxIndex ? null : { name: b.name, radius: Number(b.radius), note: b.note || '' };
    }).filter(Boolean);
    var specialNames = special.map(function (s) { return branches[s.index].name; });
    var summary = '本轮 ' + branches.length + ' 条弱信号分支拼出不规则圆，与规则圆对比后提取 ' + special.length +
      ' 条最特殊边（' + specialNames.join('、') + '），替换规则圆的平均边，组成最不规则圆。' +
      '创新度 ' + innovation.score + '/100（' + innovation.level + '）：偏离规则越多，创新越多。';
    return { keyBranch: key, others: others, specialNames: specialNames, summary: summary, mean: mean };
  }

  // --- 主入口：一次完整分析 ---
  function analyze(branches, params) {
    var v = validate(branches);
    if (!v.ok) return v;
    params = params || {};
    var N = branches.length;
    var radii = branches.map(function (b) { return Number(b.radius); });
    var ms = meanStd(radii);
    var MINPX = (params.minPx == null) ? 30 : params.minPx;
    var MAXPX = (params.maxPx == null) ? 140 : params.maxPx;
    var radiiPx = normalizeRadii(radii, MINPX, MAXPX);
    var minR = Math.min.apply(null, radii);
    var maxR = Math.max.apply(null, radii);
    var meanPx = MINPX + (ms.mean - minR) / ((maxR - minR) || 1) * (MAXPX - MINPX);
    var irregular = buildVertices(branches, radiiPx);
    var regular = regularVertices(branches, meanPx);
    var det = detectSpecialEdges(radii, params.zThreshold);
    var recombined = recombineVertices(branches, radiiPx, meanPx, det.special);
    var innovation = computeInnovation(radii, irregular, params);
    var concl = generateConclusions(branches, det.special, det.maxIndex, innovation, ms.mean);
    return {
      ok: true,
      branches: branches.map(function (b, i) { return { name: b.name, radius: Number(b.radius), note: b.note || '', conclusion: b.conclusion || '' }; }),
      regular: regular, irregular: irregular, recombined: recombined,
      special: det.special, maxIndex: det.maxIndex,
      innovation: innovation, conclusions: concl,
      meta: { N: N, mean: ms.mean, std: ms.std, meanPx: meanPx, radiiPx: radiiPx }
    };
  }

  // --- 绘制用：顶点 → 路径点（中心缩放） ---
  function toPoints(verts, cx, cy) {
    return verts.map(function (v) { return { x: cx + v.ux * v.rpx, y: cy + v.uy * v.rpx, name: v.name, radius: v.radius }; });
  }

  return {
    analyze: analyze,
    validate: validate,
    meanStd: meanStd,
    normalizeRadii: normalizeRadii,
    buildVertices: buildVertices,
    vertexAngle: vertexAngle,
    detectSpecialEdges: detectSpecialEdges,
    regularVertices: regularVertices,
    recombineVertices: recombineVertices,
    computeInnovation: computeInnovation,
    generateConclusions: generateConclusions,
    toPoints: toPoints
  };
})();

/* ---------- CLI 入口 ---------- */
function main() {
  var args = process.argv.slice(2);
  var config = loadConfig();
  var circleCfg = config.circle || {};
  var branches = null;

  if (args.length === 0 || args[0] === '--help' || args[0] === 'help') {
    console.log('🐛 弱信号不规则圆 v1.1（创新引擎新功能，鱼肉猫卷 2026-10-12 原创设计）');
    console.log('用法：node innovation_irregular_circle.js --sample');
    console.log('      node innovation_irregular_circle.js branches.json');
    console.log('      node innovation_irregular_circle.js --json \'{...}\'');
    console.log('分支字段：{ name, radius(0~10), note, conclusion }，至少 3 个');
    console.log('参数来自 innovation_config.json 的 circle 键（可被 --params JSON 覆盖）');
    process.exit(0);
  }
  try {
    if (args[0] === '--sample') {
      branches = [
        { name: '算法创新', radius: 8, note: '引入弱信号检测，从被剪枝的分支里回收线索，放大微小异常。', conclusion: '检测到被忽略的弱信号并成功放大' },
        { name: '性能优化', radius: 5, note: '缓存热点路径，减少重复计算，常规收益平稳。', conclusion: '常规优化，收益平稳' },
        { name: '架构设计', radius: 9, note: '树状分支 + 共识归纳 + 动态收敛的完整架构。', conclusion: '架构级突破，支撑多轮深度推理' },
        { name: '用户体验', radius: 3, note: '界面简化，降低小白使用门槛。', conclusion: '温和改进，门槛降低' },
        { name: '安全防护', radius: 6, note: '沙箱隔离执行，防止恶意代码污染环境。', conclusion: '常规安全加固' },
        { name: '可扩展性', radius: 7, note: '插件化接口设计，方便未来挂载新能力。', conclusion: '渐进增强，生态友好' }
      ];
    } else if (args[0] === '--json') {
      branches = JSON.parse(args[1]);
    } else {
      branches = JSON.parse(fs.readFileSync(args[0], 'utf8'));
    }
  } catch (err) {
    console.error('❌ 分支数据解析失败：' + err.message);
    process.exit(1);
  }

  var params = circleCfg;
  var pIdx = args.indexOf('--params');
  if (pIdx >= 0 && args[pIdx + 1]) {
    try { params = JSON.parse(args[pIdx + 1]); } catch (e) { /* 保持默认 */ }
  }

  var res = ButterflyAlgo.analyze(branches, params);
  if (!res.ok) {
    console.error('❌ ' + res.error);
    process.exit(2);
  }
  console.log(JSON.stringify(res, null, 2));
}

if (require.main === module) {
  main();
}

module.exports = ButterflyAlgo;
