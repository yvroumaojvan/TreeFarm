#!/usr/bin/env node
'use strict';

/* ============================================================
 * harvest_view.js —— 灵感果实库可视化（零依赖）
 * 读 innovation_harvest.json → 生成自包含 HTML（手机浏览器直接看）
 * 用法：node harvest_view.js [果实库路径] [输出HTML路径]
 * ============================================================ */

var fs = require('fs');
var path = require('path');
var sim = require('./similarity.js');

var harvestFile = process.argv[2] || path.join(__dirname, 'innovation_harvest.json');
var outFile = process.argv[3] || path.join(__dirname, 'harvest_view.html');

var fruits = [];
try {
  fruits = JSON.parse(fs.readFileSync(harvestFile, 'utf8'));
} catch (e) {
  fruits = [];
}

// 果实间相似度矩阵（联想面板用）
var pairs = [];
for (var i = 0; i < fruits.length; i++) {
  for (var j = i + 1; j < fruits.length; j++) {
    var sc = Math.round(sim.similarity(fruits[i].seed, fruits[j].seed) * 100);
    if (sc >= 10) pairs.push({ a: i, b: j, sim: sc });
  }
}
pairs.sort(function (x, y) { return y.sim - x.sim; });

var data = JSON.stringify({ fruits: fruits, pairs: pairs });

var html = `<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>灵感果实库 · TreeFarm 创新分支</title>
<style>
  :root { --bg:#0f1115; --card:#1a1e26; --line:#2a2f3a; --txt:#e6e8ec; --dim:#8a919e; --acc:#f3940e; }
  * { margin:0; padding:0; box-sizing:border-box; }
  body { background:var(--bg); color:var(--txt); font-family:system-ui,-apple-system,sans-serif; padding:16px; }
  h1 { font-size:20px; margin-bottom:4px; }
  .sub { color:var(--dim); font-size:13px; margin-bottom:16px; }
  .toolbar { display:flex; gap:8px; margin-bottom:16px; flex-wrap:wrap; }
  input { flex:1; min-width:200px; background:var(--card); border:1px solid var(--line);
          color:var(--txt); padding:10px 14px; border-radius:10px; font-size:14px; }
  .stats { color:var(--dim); font-size:13px; align-self:center; }
  .grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(280px,1fr)); gap:12px; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:14px; padding:14px; }
  .card .form { color:var(--acc); font-size:13px; font-weight:600; margin-bottom:8px; }
  .card .seed { font-size:14px; margin-bottom:6px; }
  .card .idea { color:var(--dim); font-size:13px; line-height:1.6; margin-bottom:8px; }
  .card .meta { display:flex; gap:8px; font-size:12px; color:var(--dim); flex-wrap:wrap; }
  .badge { background:rgba(243,148,14,.12); color:var(--acc); padding:2px 8px; border-radius:20px; }
  .empty { color:var(--dim); text-align:center; padding:40px 0; font-size:14px; }
  .links { margin-top:16px; }
  .link { display:inline-block; background:var(--card); border:1px solid var(--line);
          padding:6px 12px; border-radius:20px; font-size:12px; margin:4px 4px 0 0; color:var(--acc); }
</style>
</head>
<body>
<h1>🍎 灵感果实库</h1>
<div class="sub">灵感存下来、整理好、以后还能再用 —— 选一颗果实，看它能和谁串起来</div>
<div class="toolbar">
  <input id="q" placeholder="搜索内核 / 表达形式 / 内容…" oninput="render()">
  <span class="stats" id="stats"></span>
</div>
<div class="grid" id="grid"></div>
<div id="empty" class="empty" style="display:none"></div>
<script>
var DATA = ${data};

function esc(s) {
  return String(s || '').replace(/[&<>"']/g, function (c) {
    return { '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;' }[c];
  });
}

function render() {
  var q = document.getElementById('q').value.trim().toLowerCase();
  var grid = document.getElementById('grid');
  var empty = document.getElementById('empty');
  grid.innerHTML = '';
  var fs = DATA.fruits;
  if (!fs.length) {
    empty.style.display = 'block';
    empty.textContent = '🍂 果实库还是空的 —— 用 innovation.js 走一遍 idea → forms → score → converge → harvest 结出第一颗果实吧';
    document.getElementById('stats').textContent = '0 颗果实';
    return;
  }
  var shown = fs.filter(function (f) {
    if (!q) return true;
    return (f.seed + ' ' + f.form + ' ' + f.idea + ' ' + (f.why || '')).toLowerCase().indexOf(q) >= 0;
  });
  document.getElementById('stats').textContent = shown.length + ' / ' + fs.length + ' 颗果实';
  if (!shown.length) {
    empty.style.display = 'block';
    empty.textContent = '没有匹配的果实，换个关键词试试';
    return;
  }
  empty.style.display = 'none';
  shown.forEach(function (f, i) {
    var card = document.createElement('div');
    card.className = 'card';
    var time = (f.time || '').slice(0, 10);
    var links = DATA.pairs
      .filter(function (p) { return p.a === i || p.b === i; })
      .map(function (p) {
        var other = p.a === i ? fs[p.b] : fs[p.a];
        return '<span class="link">🔗 ' + esc(other.form) + '（相似 ' + p.sim + '%）</span>';
      }).join('');
    card.innerHTML =
      '<div class="form">' + esc(f.form) + ' · ⭐' + f.score + '</div>' +
      '<div class="seed">内核：' + esc(f.seed) + '</div>' +
      '<div class="idea">' + esc(f.idea) + '</div>' +
      '<div class="meta"><span class="badge">' + time + '</span></div>' +
      (links ? '<div class="links">' + links + '</div>' : '');
    grid.appendChild(card);
  });
}

render();
</script>
</body>
</html>`;

fs.writeFileSync(outFile, html, 'utf8');
console.log('🍎 果实库可视化已生成：' + outFile);
console.log('  果实 ' + fruits.length + ' 颗 · 相似联想 ' + pairs.length + ' 对');
console.log('  手机浏览器直接打开这个 HTML 即可查看');
