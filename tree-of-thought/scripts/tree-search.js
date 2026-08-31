#!/usr/bin/env node
'use strict';

/* ============================================================
 * tree-search.js — 思维树搜索管理器（v3.0 专业版核心）
 *
 * 把「思维树」从纯提示词工程升级为真正的树搜索：
 *   · 搜索策略：BFS（广度优先）/ DFS（深度优先）/ Beam（束搜索，默认）
 *   · 节点状态机：open → expanded → scored → pruned | dead_end
 *   · 四维评分：evidence(证据) + relevance(相关) + novelty(新颖) + verifiable(可验证)
 *   · 剪枝：beam 束宽保留 Top-K，其余剪掉
 *   · 回溯：死路标记 + 回退到最近可展开祖先（DFS 精神）
 *   · 弱信号检测：分支文本聚类，出现频率 ≤30% 的观点 = 弱信号（用户原创增量，算法化）
 *   · 收敛检测：相邻两轮分支结论集合 Jaccard ≥ 阈值
 *   · 状态持久化：每次命令自动写 .tree_state.json，支持中断续跑
 *
 * AI 协作模式：AI 负责「生成内容 + 打分」（决策），本工具负责「搜索结构」
 * （算法），两者结合 = 对标 Princeton ToT 论文的 LLM+搜索 架构。
 *
 * 用法（任意子命令，状态自动持久化）：
 *   node tree-search.js init --strategy beam --beam 4 --depth 3 "问题"
 *   node tree-search.js expand <节点id> '[{"dimension":"...","hypothesis":"...","reasoning":"...","conclusion":"..."}]'
 *   node tree-search.js score <节点id> '{"evidence":0.9,"relevance":0.8,"novelty":0.6,"verifiable":0.7}'
 *   node tree-search.js select            # 按策略选下一个要展开的节点
 *   node tree-search.js prune             # 束宽剪枝
 *   node tree-search.js backtrack <节点id> "死路原因"
 *   node tree-search.js signal [深度]     # 弱信号检测
 *   node tree-search.js converge [阈值]   # 收敛检测
 *   node tree-search.js render [--compact]  # 渲染带状态徽章的树
 *   node tree-search.js save <文件> | load <文件> | status
 *   通用参数：--state <文件> 指定状态文件（默认 .tree_state.json）
 * ============================================================ */

var fs = require('fs');

/* ---------------- 常量 ---------------- */
var DEFAULT_STATE = '.tree_state.json';
var DEFAULT_STRATEGY = 'beam';
var DEFAULT_BEAM = 4;
var DEFAULT_DEPTH = 3;
var SCORE_WEIGHTS = { evidence: 0.35, relevance: 0.30, novelty: 0.20, verifiable: 0.15 };
var CLUSTER_THRESHOLD = 0.40;   // 分支文本混合相似度 > 此值 = 同一观点簇（工具给候选，AI 复核语义）
var WEAK_RATIO = 0.30;          // 出现频率 ≤30% 的观点 = 弱信号
var CONVERGE_THRESHOLD = 0.9;   // 相邻两轮结论集合相似度 ≥ 此值 = 收敛

/* ---------------- 状态 ---------------- */
function newState() {
  return {
    version: 3,
    trunk: '',
    strategy: DEFAULT_STRATEGY,
    beam_width: DEFAULT_BEAM,
    max_depth: DEFAULT_DEPTH,
    nodes: {},          // id → node
    order: [],          // 创建顺序（BFS 用）
    seq: 0,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    last_action: '',
  };
}

function loadState(file) {
  try {
    var raw = JSON.parse(fs.readFileSync(file, 'utf8'));
    if (!raw.nodes) throw new Error('不是合法的思维树状态文件');
    return raw;
  } catch (e) {
    return newState();
  }
}

function saveState(state, file) {
  state.updated_at = new Date().toISOString();
  fs.writeFileSync(file, JSON.stringify(state, null, 2));
}

/* ---------------- 节点工具 ---------------- */
function nextId(state) {
  state.seq += 1;
  return 'n_' + state.seq;
}

function newNode(state, parent, type, content) {
  var n = {
    id: nextId(state),
    parent: parent,
    depth: parent ? state.nodes[parent].depth + 1 : 0,
    type: type,               // trunk | branch | consensus
    content: content,
    score: null,              // {evidence, relevance, novelty, verifiable, total}
    status: parent ? 'open' : 'expanded',   // open | expanded | scored | pruned | dead_end
    children: [],
    pruned: false,
    dead_reason: null,
    created_at: Date.now(),
  };
  state.nodes[n.id] = n;
  state.order.push(n.id);
  if (parent) state.nodes[parent].children.push(n.id);
  return n;
}

function openNodes(state) {
  var out = [];
  state.order.forEach(function (id) {
    var n = state.nodes[id];
    // 可展开候选：open（未评分）或 scored（已评分待展开）；剪枝/死路除外
    if (n.status === 'open' || n.status === 'scored') out.push(n);
  });
  return out;
}

function totalScore(score) {
  if (!score) return null;
  var s = 0;
  Object.keys(SCORE_WEIGHTS).forEach(function (k) {
    s += (score[k] || 0) * SCORE_WEIGHTS[k];
  });
  return Math.round(s * 100) / 100;
}

/* ---------------- 文本相似度（弱信号聚类用） ----------------
 * 单一 n-gram 对中文短句不鲁棒（"先攒钱再辞职" vs "攒够钱再走"
 * 的 3-gram 几乎不重合）。用混合相似度：
 *   mixed = max(trigram Jaccard, bigram 余弦)
 * 英文/代码靠 trigram，中文短句靠 bigram 余弦，取两者最优。 */
function ngrams(text, n) {
  var t = String(text).toLowerCase().replace(/\s+/g, ' ');
  var out = [];
  for (var i = 0; i <= t.length - n; i++) out.push(t.substr(i, n));
  return out;
}

function jaccard(a, b) {
  var ga = ngrams(a, 3), gb = ngrams(b, 3);
  if (!ga.length || !gb.length) return 0;
  var sa = {}, sb = {}, inter = 0;
  ga.forEach(function (g) { sa[g] = true; });
  gb.forEach(function (g) { sb[g] = true; });
  Object.keys(sa).forEach(function (g) { if (sb[g]) inter++; });
  var union = Object.keys(sa).length + Object.keys(sb).length - inter;
  return union ? inter / union : 0;
}

function bigramCosine(a, b) {
  var ga = ngrams(a, 2), gb = ngrams(b, 2);
  if (!ga.length || !gb.length) return 0;
  var sa = {}, sb = {}, inter = 0;
  ga.forEach(function (g) { sa[g] = (sa[g] || 0) + 1; });
  gb.forEach(function (g) { sb[g] = (sb[g] || 0) + 1; });
  Object.keys(sa).forEach(function (g) {
    if (sb[g]) inter += Math.min(sa[g], sb[g]);
  });
  var norm = Math.sqrt(ga.length) * Math.sqrt(gb.length);
  return norm ? inter / norm : 0;
}

function mixedSimilarity(a, b) {
  return Math.max(jaccard(a, b), bigramCosine(a, b));
}

/* ---------------- 策略选择 ---------------- */
function select(state) {
  var opens = openNodes(state);
  if (!opens.length) return null;
  if (state.strategy === 'bfs') {
    // 广度优先：深度最小的先展开（BFS 队列语义 = 创建顺序）
    opens.sort(function (a, b) {
      return a.depth - b.depth || state.order.indexOf(a.id) - state.order.indexOf(b.id);
    });
  } else if (state.strategy === 'dfs') {
    // 深度优先：深度最大的先展开（DFS 栈语义）
    opens.sort(function (a, b) { return b.depth - a.depth; });
  } else {
    // beam：评分最高的优先（束搜索按质量扩展）；未评分节点排最后
    opens.sort(function (a, b) {
      var sa = totalScore(a.score), sb = totalScore(b.score);
      if (sa === null && sb === null) return state.order.indexOf(a.id) - state.order.indexOf(b.id);
      if (sa === null) return 1;
      if (sb === null) return -1;
      if (sa !== sb) return sb - sa;
      return state.order.indexOf(a.id) - state.order.indexOf(b.id);
    });
  }
  return opens[0];
}

/* ---------------- 剪枝（beam 束宽） ---------------- */
function prune(state) {
  var pruned = 0;
  Object.keys(state.nodes).forEach(function (id) {
    var n = state.nodes[id];
    if (!n.children.length || n.status === 'dead_end') return;
    var kids = n.children.map(function (cid) { return state.nodes[cid]; })
      .filter(function (k) { return k.status !== 'pruned'; });
    if (kids.length <= state.beam_width) return;
    kids.sort(function (a, b) {
      var sa = totalScore(a.score), sb = totalScore(b.score);
      if (sa !== null && sb !== null && sa !== sb) return sb - sa;
      return state.order.indexOf(a.id) - state.order.indexOf(b.id);
    });
    kids.slice(state.beam_width).forEach(function (k) {
      markPruned(state, k.id);
      pruned++;
    });
  });
  return pruned;
}

function markPruned(state, id) {
  var n = state.nodes[id];
  if (!n || n.pruned) return;
  n.pruned = true;
  n.status = 'pruned';
  n.children.forEach(function (cid) { markPruned(state, cid); });
}

/* ---------------- 回溯 ---------------- */
function backtrack(state, id, reason) {
  var n = state.nodes[id];
  if (!n) return { ok: false, error: '节点不存在: ' + id };
  // 标记死路（含子树）
  markDead(state, id, reason || '推理无进展');
  // 向上找最近的有待展开子节点（open 或 scored）的祖先
  var cur = n.parent;
  while (cur) {
    var p = state.nodes[cur];
    var hasOpen = p.children.some(function (cid) {
      var c = state.nodes[cid];
      return (c.status === 'open' || c.status === 'scored') && !c.pruned;
    });
    if (hasOpen) {
      return { ok: true, back_to: cur, note: '已回溯到节点 ' + cur + '，其下仍有待展开分支' };
    }
    cur = p.parent;
  }
  // 回到树根：整条路径都死了，从根重新生长
  return { ok: true, back_to: null, note: '整条路径均为死路，需从树干重新分支' };
}

function markDead(state, id, reason) {
  var n = state.nodes[id];
  if (!n) return;
  n.status = 'dead_end';
  n.dead_reason = reason;
  n.pruned = true;
  n.children.forEach(function (cid) { markDead(state, cid, reason); });
}

/* ---------------- 弱信号检测（用户原创增量，算法化） ---------------- */
function signal(state, depth) {
  var targetDepth = depth !== undefined ? depth : maxBranchDepth(state);
  // 收集该深度的分支文本（结论优先，其次推理）
  var branches = Object.keys(state.nodes).map(function (id) { return state.nodes[id]; })
    .filter(function (n) { return n.type === 'branch' && n.depth === targetDepth
                            && n.status !== 'pruned'; });
  var texts = branches.map(function (n) {
    var c = n.content || {};
    return c.conclusion || c.reasoning || c.hypothesis || '';
  });

  // 聚类：混合相似度 > 阈值 → 同一观点簇（中文短句用 bigram 余弦，英文/代码用 trigram）
  var clusters = [];
  branches.forEach(function (n, i) {
    var placed = false;
    for (var ci = 0; ci < clusters.length; ci++) {
      var rep = clusters[ci].texts[0];
      if (mixedSimilarity(texts[i], rep) > CLUSTER_THRESHOLD) {
        clusters[ci].members.push(n.id);
        clusters[ci].texts.push(texts[i]);
        placed = true;
        break;
      }
    }
    if (!placed) clusters.push({ members: [n.id], texts: [texts[i]] });
  });

  var total = branches.length;
  var out = clusters.map(function (c) {
    return {
      members: c.members,
      count: c.members.length,
      total: total,
      ratio: total ? Math.round(c.members.length / total * 100) : 0,
      weak: total ? (c.members.length / total) <= WEAK_RATIO : false,
      text: c.texts[0],
    };
  });
  out.sort(function (a, b) { return b.count - a.count; });
  return { depth: targetDepth, total: total, clusters: out };
}

function maxBranchDepth(state) {
  var d = 0;
  Object.keys(state.nodes).forEach(function (id) {
    var n = state.nodes[id];
    if (n.type === 'branch' && n.depth > d && n.status !== 'pruned') d = n.depth;
  });
  return d;
}

/* ---------------- 收敛检测 ---------------- */
function converge(state, threshold) {
  var t = threshold || CONVERGE_THRESHOLD;
  // 取最深的两个分支层的结论集合，算 Jaccard 相似度
  var depths = {};
  Object.keys(state.nodes).forEach(function (id) {
    var n = state.nodes[id];
    if (n.type === 'branch' && n.status !== 'pruned') {
      (depths[n.depth] = depths[n.depth] || []).push(n);
    }
  });
  var ds = Object.keys(depths).map(Number).sort(function (a, b) { return a - b; });
  if (ds.length < 2) {
    return { converged: false, similarity: null, note: '不足两层分支，无法判断收敛' };
  }
  var older = depths[ds[ds.length - 2]].map(function (n) { return (n.content || {}).conclusion || ''; });
  var newer = depths[ds[ds.length - 1]].map(function (n) { return (n.content || {}).conclusion || ''; });
  // 两轮结论集合的重叠率（新结论中有多少能在旧结论里找到相似簇）
  var matched = 0;
  newer.forEach(function (txt) {
    var hit = older.some(function (o) { return mixedSimilarity(txt, o) > CLUSTER_THRESHOLD; });
    if (hit) matched++;
  });
  var sim = newer.length ? matched / newer.length : 1;
  return {
    converged: sim >= t,
    similarity: Math.round(sim * 100) / 100,
    note: sim >= t ? '相邻两轮结论高度重合，已收敛' : '仍有新结论，继续搜索',
  };
}

/* ---------------- 渲染 ---------------- */
function render(state, compact) {
  var lines = [];
  var total = Object.keys(state.nodes).length;
  var pruned = Object.keys(state.nodes).filter(function (id) {
    return state.nodes[id].pruned;
  }).length;
  var dead = Object.keys(state.nodes).filter(function (id) {
    return state.nodes[id].status === 'dead_end';
  }).length;
  lines.push('🌳 思维树 · ' + state.strategy.toUpperCase() + ' 搜索 · 深度 '
    + maxBranchDepth(state) + '/' + state.max_depth + ' · ' + total + ' 节点'
    + (pruned ? ' · ' + pruned + ' 剪枝' : '') + (dead ? ' · ' + dead + ' 死路' : ''));
  lines.push('│');
  var rootId = state.order[0];
  if (rootId) renderNode(lines, state, rootId, '', true, compact);
  return lines.join('\n');
}

function badge(n) {
  if (n.status === 'dead_end') return '💀 死路';
  if (n.status === 'pruned') return '✂️ 剪枝';
  if (n.status === 'expanded') return '🔍 已展开';
  if (n.status === 'scored') return '✅ 评分 ' + totalScore(n.score);
  return '⏳ 待展开';
}

function nodeText(n) {
  var c = n.content;
  if (typeof c === 'string') return c;
  return (c && (c.conclusion || c.dimension || c.hypothesis)) || '';
}

function renderNode(lines, state, id, prefix, isLast, compact) {
  var n = state.nodes[id];
  var label = '';
  if (n.type === 'trunk') {
    label = '🌳 树干：' + truncate(nodeText(n), 40);
  } else if (n.type === 'consensus') {
    label = '🧡 共识：' + truncate(nodeText(n), 50);
  } else {
    var c = n.content || {};
    label = '🌿 ' + (c.dimension || '分支') + '  [' + badge(n) + ']';
    if (n.dead_reason) label += ' 原因: ' + n.dead_reason;
    if (n.score && n.score.total !== undefined && !compact) {
      label += '\n' + prefix + (isLast ? '    ' : '│   ') + '    📊 '
        + '证据' + (n.score.evidence || 0) + ' 相关' + (n.score.relevance || 0)
        + ' 新颖' + (n.score.novelty || 0) + ' 可验证' + (n.score.verifiable || 0);
    }
    if (!compact && c.conclusion) {
      label += '\n' + prefix + (isLast ? '    ' : '│   ') + '    → ' + truncate(c.conclusion, 60);
    }
  }
  lines.push(prefix + (isLast ? '└── ' : '├── ') + label);
  var childPrefix = prefix + (isLast ? '    ' : '│   ');
  var kids = n.children;
  for (var i = 0; i < kids.length; i++) {
    renderNode(lines, state, kids[i], childPrefix, i === kids.length - 1, compact);
  }
}

function truncate(s, max) {
  s = String(s || '').replace(/\s+/g, ' ').trim();
  return s.length > max ? s.slice(0, max) + '…' : s;
}

/* ---------------- 命令解析 ---------------- */
function parseArgs(argv) {
  var opts = { state: DEFAULT_STATE };
  var rest = [];
  for (var i = 0; i < argv.length; i++) {
    if (argv[i] === '--state' && i + 1 < argv.length) {
      opts.state = argv[i + 1];
      i++;
    } else if (argv[i] === '--compact') {
      opts.compact = true;
    } else if (argv[i] === '--strategy' && i + 1 < argv.length) {
      opts.strategy = argv[i + 1];
      i++;
    } else if (argv[i] === '--beam' && i + 1 < argv.length) {
      opts.beam = parseInt(argv[i + 1], 10);
      i++;
    } else if (argv[i] === '--depth' && i + 1 < argv.length) {
      opts.depth = parseInt(argv[i + 1], 10);
      i++;
    } else {
      rest.push(argv[i]);
    }
  }
  return { opts: opts, rest: rest };
}

function usage() {
  console.log('用法：\n'
    + '  node tree-search.js init [--strategy bfs|dfs|beam] [--beam N] [--depth N] "问题"\n'
    + '  node tree-search.js expand <节点id> \'[{"dimension":"...","hypothesis":"...","reasoning":"...","conclusion":"..."}]\'\n'
    + '  node tree-search.js score <节点id> \'{"evidence":0.9,"relevance":0.8,"novelty":0.6,"verifiable":0.7}\'\n'
    + '  node tree-search.js select\n'
    + '  node tree-search.js prune\n'
    + '  node tree-search.js backtrack <节点id> "死路原因"\n'
    + '  node tree-search.js signal [深度]\n'
    + '  node tree-search.js converge [阈值]\n'
    + '  node tree-search.js render [--compact]\n'
    + '  node tree-search.js save <文件> | load <文件> | status\n'
    + '通用参数：--state <文件>（默认 .tree_state.json）\n'
    + '\n评分维度（权重）：evidence 证据 35% / relevance 相关 30% / novelty 新颖 20% / verifiable 可验证 15%');
}

/* ---------------- 主流程 ---------------- */
function main() {
  var argv = process.argv.slice(2);
  var parsed = parseArgs(argv);
  var opts = parsed.opts, rest = parsed.rest;
  if (!rest.length) { usage(); return; }

  var cmd = rest[0];
  var state = loadState(opts.state);

  if (cmd === 'init') {
    var trunk = rest.slice(1).join(' ');
    state = newState();
    state.trunk = trunk || '(未命名问题)';
    state.strategy = (['bfs', 'dfs', 'beam'].indexOf(opts.strategy) >= 0) ? opts.strategy : DEFAULT_STRATEGY;
    state.beam_width = opts.beam || DEFAULT_BEAM;
    state.max_depth = opts.depth || DEFAULT_DEPTH;
    var root = newNode(state, null, 'trunk', state.trunk);
    root.status = 'expanded';
    state.last_action = 'init';
    saveState(state, opts.state);
    console.log('🌱 思维树已初始化：strategy=' + state.strategy
      + ' beam=' + state.beam_width + ' max_depth=' + state.max_depth
      + ' | 根节点 ' + root.id + '\n下一步：expand ' + root.id + ' "分支JSON"');
  } else if (cmd === 'expand' && rest.length >= 3) {
    var pid = rest[1];
    if (!state.nodes[pid]) { console.log('✖ 节点不存在: ' + pid); process.exit(1); }
    var branches;
    try {
      branches = JSON.parse(rest.slice(2).join(' '));
    } catch (e) {
      console.log('✖ 分支 JSON 解析失败: ' + e.message); process.exit(1);
    }
    if (!Array.isArray(branches)) branches = [branches];
    var parent = state.nodes[pid];
    if (parent.status === 'pruned' || parent.status === 'dead_end') {
      console.log('✖ 节点 ' + pid + ' 已' + (parent.status === 'pruned' ? '剪枝' : '死路')
        + '，禁止展开——剪枝后的分支不再投入 token');
      process.exit(1);
    }
    if (parent.status !== 'expanded' && parent.status !== 'scored') {
      console.log('⚠️ 节点 ' + pid + ' 状态为 ' + parent.status + '，建议先 select 选可展开节点');
    }
    parent.status = 'expanded';
    var ids = branches.map(function (b) {
      return newNode(state, pid, b.type === 'consensus' ? 'consensus' : 'branch', b).id;
    });
    state.last_action = 'expand ' + pid;
    saveState(state, opts.state);
    console.log('🌿 已展开节点 ' + pid + ' → ' + ids.length + ' 个子节点: ' + ids.join(', ')
      + '\n下一步：逐个 score 评分 → select 选下一个');
  } else if (cmd === 'score' && rest.length >= 3) {
    var sid = rest[1];
    var sc;
    try {
      sc = JSON.parse(rest.slice(2).join(' '));
    } catch (e) {
      console.log('✖ 评分 JSON 解析失败: ' + e.message); process.exit(1);
    }
    if (!state.nodes[sid]) { console.log('✖ 节点不存在: ' + sid); process.exit(1); }
    if (state.nodes[sid].status === 'pruned' || state.nodes[sid].status === 'dead_end') {
      console.log('✖ 节点 ' + sid + ' 已' + (state.nodes[sid].status === 'pruned' ? '剪枝' : '死路')
        + '，禁止评分——它已退出搜索');
      process.exit(1);
    }
    sc.total = totalScore(sc);
    state.nodes[sid].score = sc;
    state.nodes[sid].status = 'scored';
    state.last_action = 'score ' + sid;
    saveState(state, opts.state);
    console.log('📊 ' + sid + ' 综合评分 = ' + sc.total + '（证据' + (sc.evidence || 0)
      + ' 相关' + (sc.relevance || 0) + ' 新颖' + (sc.novelty || 0) + ' 可验证' + (sc.verifiable || 0) + '）');
  } else if (cmd === 'select') {
    var sel = select(state);
    if (!sel) {
      console.log('⏹ 没有待展开的节点了——检查是否已收敛，或从树干重新分支');
    } else {
      console.log('🎯 按 ' + state.strategy.toUpperCase() + ' 策略选中: ' + sel.id
        + '（深度 ' + sel.depth + '，' + (sel.score ? '评分 ' + totalScore(sel.score) : '未评分') + '）\n'
        + '下一步：expand ' + sel.id + ' "分支JSON"');
    }
  } else if (cmd === 'prune') {
    var n = prune(state);
    state.last_action = 'prune';
    saveState(state, opts.state);
    console.log('✂️ 束宽 ' + state.beam_width + '，剪掉 ' + n + ' 个低分分支');
  } else if (cmd === 'backtrack' && rest.length >= 2) {
    var bid = rest[1];
    var reason = rest.slice(2).join(' ');
    var r = backtrack(state, bid, reason);
    state.last_action = 'backtrack ' + bid;
    saveState(state, opts.state);
    console.log('💀 节点 ' + bid + ' 已标记死路' + (reason ? '（' + reason + '）' : ''));
    if (r.back_to) {
      console.log('⤴ 回溯到 ' + r.back_to + ' → 下一步：expand ' + r.back_to + ' "分支JSON"');
    } else {
      console.log('⤴ 整条路径均为死路 → 回到树干重新分支');
    }
  } else if (cmd === 'signal') {
    var d = rest.length >= 2 ? parseInt(rest[1], 10) : undefined;
    var sig = signal(state, d);
    if (!sig.total) {
      console.log('⚡ 该深度没有可检测的分支');
    } else {
      console.log('⚡ 弱信号检测 · 深度 ' + sig.depth + ' · ' + sig.total + ' 个分支');
      sig.clusters.forEach(function (c) {
        var tag = c.weak ? '⚡ 弱信号' : '🧡 共识方向';
        console.log('  ' + tag + '（' + c.count + '/' + sig.total + ' 分支）: ' + truncate(c.text, 80));
      });
    }
  } else if (cmd === 'converge') {
    var th = rest.length >= 2 ? parseFloat(rest[1]) : undefined;
    var cv = converge(state, th);
    console.log((cv.converged ? '✅ 已收敛' : '⏳ 未收敛') + ' · 相似度 '
      + (cv.similarity === null ? 'N/A' : cv.similarity) + ' · ' + cv.note);
  } else if (cmd === 'render') {
    console.log(render(state, opts.compact));
  } else if (cmd === 'save' && rest.length >= 2) {
    fs.writeFileSync(rest[1], JSON.stringify(state, null, 2));
    console.log('💾 已保存到 ' + rest[1]);
  } else if (cmd === 'load' && rest.length >= 2) {
    state = loadState(rest[1]);
    saveState(state, opts.state);
    console.log('📂 已加载 ' + rest[1] + ' → 树干: ' + truncate(state.trunk, 40));
  } else if (cmd === 'status') {
    var st = state;
    console.log('📋 状态 · 策略 ' + st.strategy.toUpperCase() + ' · 深度 '
      + maxBranchDepth(st) + '/' + st.max_depth + ' · 节点 ' + Object.keys(st.nodes).length
      + ' · 树干: ' + truncate(st.trunk, 40));
  } else {
    usage();
  }
}

main();
