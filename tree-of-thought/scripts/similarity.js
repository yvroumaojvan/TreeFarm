/* ============================================================
 * similarity.js —— 公共相似度模块（抓虫树 / 灵感树 共用）
 * 算法：trigram Jaccard + bigram 余弦取 max（中文短句鲁棒，
 *       与弱信号聚类同族——乖宝原创弱信号检测的工程底座）
 * ============================================================ */
'use strict';

function trigrams(s) {
  var set = {};
  for (var i = 0; i < s.length - 2; i++) set[s.slice(i, i + 3)] = true;
  return Object.keys(set);
}
function bigrams(s) {
  var set = {};
  for (var i = 0; i < s.length - 1; i++) set[s.slice(i, i + 2)] = true;
  return Object.keys(set);
}
function jaccardOf(a, b) {
  if (!a.length || !b.length) return 0;
  var inter = 0, seen = {};
  for (var i = 0; i < a.length; i++) seen[a[i]] = true;
  for (var j = 0; j < b.length; j++) if (seen[b[j]]) inter++;
  return inter / new Set(a.concat(b)).size;
}
function cosineOf(a, b) {
  if (!a.length || !b.length) return 0;
  var fa = {}, fb = {}, all = new Set();
  a.forEach(function (t) { fa[t] = (fa[t] || 0) + 1; all.add(t); });
  b.forEach(function (t) { fb[t] = (fb[t] || 0) + 1; all.add(t); });
  var dot = 0, na = 0, nb = 0;
  all.forEach(function (t) {
    dot += (fa[t] || 0) * (fb[t] || 0);
    na += (fa[t] || 0) * (fa[t] || 0);
    nb += (fb[t] || 0) * (fb[t] || 0);
  });
  if (!na || !nb) return 0;
  return dot / (Math.sqrt(na) * Math.sqrt(nb));
}

/** 文本相似度：max(trigram Jaccard, bigram 余弦)，0~1 */
function similarity(a, b) {
  return Math.max(jaccardOf(trigrams(a), trigrams(b)),
                  cosineOf(bigrams(a), bigrams(b)));
}

module.exports = { similarity: similarity, jaccard: jaccardOf, cosine: cosineOf };
