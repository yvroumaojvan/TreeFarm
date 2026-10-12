#!/usr/bin/env node
'use strict';

/* ============================================================
 * innovation_circle_view.js —— 不规则圆导出页（任务快照，只读）
 * 读创新引擎跑出的分析结果 → 生成自包含 HTML（手机浏览器直接看）
 * 用法：node innovation_circle_view.js <circleResult.json> [输出HTML路径]
 * 输出页 = 任务快照：第X轮/共Y轮 + 三图（SVG）+ 创新度 + 结论 + 分支数据，用户不可改
 * ============================================================ */

var fs = require('fs');
var path = require('path');

var inFile = process.argv[2];
var outFile = process.argv[3] || path.join(__dirname, 'circle_view.html');

if (!inFile) {
  console.log('🐛 不规则圆导出页 v1.0（任务快照，只读）');
  console.log('用法：node innovation_circle_view.js <circleResult.json> [输出HTML路径]');
  console.log('circleResult 由 innovation.js circle 命令 / evolve 命令产生');
  process.exit(0);
}

var res;
try {
  res = JSON.parse(fs.readFileSync(inFile, 'utf8'));
} catch (e) {
  console.error('❌ 读取分析结果失败：' + e.message);
  process.exit(1);
}
if (!res || res.ok !== true) {
  console.error('❌ 分析结果无效（ok !== true）');
  process.exit(1);
}

/* ---------- SVG 画圆 ---------- */
function polyPath(verts, cx, cy, scale) {
  scale = scale || 1;
  var pts = verts.map(function (v) {
    return (cx + v.ux * v.rpx * scale).toFixed(1) + ',' + (cy + v.uy * v.rpx * scale).toFixed(1);
  });
  return 'M' + pts.join(' L') + ' Z';
}

function circleSvg(id, title, irregular, regular, recombined, stroke1, stroke2, stroke3) {
  var cx = 160, cy = 160;
  var label = '';
  // 顶点名标注（只标不规则圆的）
  var labels = irregular.map(function (v) {
    var x = cx + v.ux * (v.rpx + 16), y = cy + v.uy * (v.rpx + 16);
    return '<text x="' + x.toFixed(1) + '" y="' + y.toFixed(1) + '" font-size="10" fill="' + stroke1 + '" text-anchor="middle">' +
      String(v.name || '').slice(0, 6) + '</text>';
  }).join('');
  var svg = '<svg viewBox="0 0 320 320" width="100%" style="max-width:320px;background:#0d1430;border-radius:12px">';
  svg += '<circle cx="' + cx + '" cy="' + cy + '" r="70" fill="none" stroke="#1d2747" stroke-width="1"/>';
  svg += '<circle cx="' + cx + '" cy="' + cy + '" r="100" fill="none" stroke="#1d2747" stroke-width="1"/>';
  svg += '<circle cx="' + cx + '" cy="' + cy + '" r="130" fill="none" stroke="#1d2747" stroke-width="1"/>';
  if (regular) svg += '<path d="' + polyPath(regular, cx, cy) + '" fill="none" stroke="' + stroke2 + '" stroke-width="1.5" stroke-dasharray="4 4" opacity="0.7"/>';
  if (recombined) svg += '<path d="' + polyPath(recombined, cx, cy) + '" fill="none" stroke="' + stroke3 + '" stroke-width="2" opacity="0.85"/>';
  svg += '<path d="' + polyPath(irregular, cx, cy) + '" fill="none" stroke="' + stroke1 + '" stroke-width="2.2"/>';
  svg += labels;
  svg += '</svg>';
  return '<div style="text-align:center"><h3 style="color:#8b98c8;font-size:12px;margin:6px 0">' + title + '</h3>' + svg + '</div>';
}

/* ---------- 组装 HTML ---------- */
var t = res.task || {};
var b = res.branches || [];
var key = res.conclusions.keyBranch || { name: '', radius: 0, note: '' };
var others = res.conclusions.others || [];
var specialNames = res.conclusions.specialNames || [];
var inn = res.innovation || {};

var otherHtml = others.map(function (o) {
  return '<li>' + o.name + '（知识强度 ' + o.radius + '）：' + String(o.note || '').slice(0, 50) + '</li>';
}).join('');

var html = `<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>蝴蝶效应 · 弱信号不规则圆（任务快照）</title>
<style>
  :root{--bg:#0b1020;--card:#141c38;--line:#243056;--mint:#34D399;--mint2:#5EEAD4;--purple:#E879F9;--purple2:#A78BFA;--text:#e2e8f0;--dim:#8b98c8;--amber:#fbbf24;}
  *{margin:0;padding:0;box-sizing:border-box}
  body{background:var(--bg);color:var(--text);font-family:system-ui,-apple-system,"PingFang SC","MiSans",sans-serif;padding:16px}
  .wrap{max-width:720px;margin:0 auto}
  .badge{display:inline-block;font-size:11px;color:var(--mint2);background:rgba(52,211,153,.12);border:1px solid rgba(52,211,153,.3);padding:3px 10px;border-radius:999px;margin-bottom:10px}
  h1{font-size:21px;font-weight:800;margin-bottom:4px}
  .sub{color:var(--dim);font-size:12px;margin-bottom:14px}
  .card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:14px;margin-bottom:12px}
  .card h2{font-size:13px;font-weight:700;margin-bottom:10px;color:var(--mint2)}
  .row{display:flex;gap:8px;flex-wrap:wrap;justify-content:center}
  .score{font-size:30px;font-weight:900;background:linear-gradient(90deg,var(--mint),var(--purple));-webkit-background-clip:text;background-clip:text;color:transparent}
  .lv{display:inline-block;font-size:11px;padding:3px 10px;border-radius:999px;font-weight:700;margin-top:4px}
  .lv-r{background:#243056;color:var(--dim)} .lv-m{background:rgba(251,191,36,.15);color:var(--amber)} .lv-b{background:rgba(232,121,249,.18);color:var(--purple)}
  .key-card{background:linear-gradient(135deg,rgba(232,121,249,.14),rgba(167,139,250,.10));border:1px solid rgba(232,121,249,.35);border-radius:12px;padding:12px;margin-bottom:10px}
  .tag{display:inline-block;font-size:10px;font-weight:700;color:var(--purple);background:rgba(232,121,249,.15);padding:2px 8px;border-radius:999px;margin-bottom:6px}
  .nm{font-size:15px;font-weight:800;margin-bottom:4px}
  .rk{font-size:12px;color:var(--mint2);margin-bottom:6px}
  .note{font-size:12px;line-height:1.7}
  .others{font-size:12px;color:var(--dim);line-height:1.8;padding-left:16px}
  .summary{background:#0d1430;border-left:3px solid var(--mint);border-radius:10px;padding:10px 12px;font-size:12.5px;line-height:1.8;margin-top:10px}
  .snapshot{font-size:11px;color:var(--dim);border:1px dashed var(--line);border-radius:8px;padding:6px 10px;margin-bottom:12px}
</style>
</head>
<body>
<div class="wrap">
  <div class="snapshot">📸 任务快照（只读）：第 ${t.currentRound || 1} 轮 / 共 ${t.totalRounds || 1} 轮 · 生成时间 ${new Date().toLocaleString('zh-CN')}</div>
  <div class="badge">🐛 蝴蝶效应 · 弱信号不规则圆</div>
  <h1>创新组合快照</h1>
  <div class="sub">每条边 = 一轮的弱信号分支 · 最不规整的圆 = 创新最多的圆</div>

  <div class="card">
    <h2>● 绘制 · 对比 · 重组</h2>
    <div class="row">
      ${circleSvg('g1', '不规则圆（实线）vs 规则圆（虚线）', res.irregular, res.regular, null, '#34D399', '#5EEAD4', '#E879F9')}
      ${circleSvg('g2', '最不规则圆（特殊边替换重组）', res.recombined, res.regular, null, '#E879F9', '#8b98c8', '#A78BFA')}
    </div>
  </div>

  <div class="card">
    <h2>● 创新度</h2>
    <div><span class="score">${inn.score || 0}</span><span style="font-size:13px;color:var(--dim)">/100</span></div>
    <span class="lv lv-${inn.lvKey || 'r'}">${inn.level || '规则'}</span>
    <div style="font-size:12px;color:var(--dim);margin-top:8px">半径离散度 CV=${(inn.cv || 0).toFixed(3)} · 锐化角 ${inn.sharp || 0} 个 · 特殊边 ${specialNames.length} 条（${specialNames.join('、')}）</div>
  </div>

  <div class="card">
    <h2>● 最特殊边（重点）</h2>
    <div class="key-card">
      <span class="tag">最特殊边</span>
      <div class="nm">${key.name || ''}</div>
      <div class="rk">知识强度 ${key.radius || 0} / 10</div>
      <div class="note">${key.note || ''}</div>
    </div>
    <div class="others">其他边（简略）：<ul>${otherHtml || '<li>（无）</li>'}</ul></div>
    <div class="summary">📝 整轮总结：${res.conclusions.summary || ''}</div>
  </div>

  <div class="card">
    <h2>● 分支数据（${b.length} 条）</h2>
    <div style="font-size:12px;color:var(--dim);line-height:2">
      ${b.map(function (x) { return '▸ ' + x.name + '（' + x.radius + '）：' + String(x.note || '').slice(0, 60); }).join('<br>')}
    </div>
  </div>
</div>
</body>
</html>`;

fs.writeFileSync(outFile, html, 'utf8');
console.log('✅ 导出完成 → ' + outFile);
console.log('   创新度 ' + inn.score + '/100 · 最特殊边「' + key.name + '」 · 共 ' + b.length + ' 条分支');
