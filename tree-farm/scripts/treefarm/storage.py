# -*- coding: utf-8 -*-
"""树场机制 —— 存储层（v4.7；v3.6 拆分自单文件 tree_farm.py）。

包含：SQLite 数据库 schema / 批量事务上下文 / GeneBank（基因库 CRUD + v2 JSON 迁移）/
TrashBin（垃圾箱熔断器）/ Session（会话状态）/ WeedIndex（杂草索引）/ SmallTree（小树过滤）。
"""

import copy
import json
import logging
import os
import sqlite3
from typing import Any, Dict, List, Optional, Set, Tuple

from .common import (CONVERGE_LIMIT, DB_FILE, SMALL_TREE_RATIO, TRASH_FAIL_LIMIT,
                     TRASH_WINDOW, WEED_SHOW, WEED_SUMMARY_LEN, WEED_SUMMARY_LINES,
                     normalize_gene)

log = logging.getLogger("treefarm")


DB_SCHEMA = """
CREATE TABLE IF NOT EXISTS genes (
  id TEXT PRIMARY KEY,
  source TEXT NOT NULL,
  target TEXT NOT NULL,
  symbol TEXT DEFAULT '',
  relation TEXT DEFAULT 'import',
  direction TEXT DEFAULT '',
  kind TEXT DEFAULT 'strong',
  confidence REAL DEFAULT 1.0,
  verified INTEGER DEFAULT 1,
  first_seen REAL,
  last_verified REAL
);
CREATE INDEX IF NOT EXISTS idx_genes_source ON genes(source);
CREATE INDEX IF NOT EXISTS idx_genes_target ON genes(target);
CREATE INDEX IF NOT EXISTS idx_genes_src_tgt ON genes(source, target);

CREATE TABLE IF NOT EXISTS mtime (
  file TEXT PRIMARY KEY,
  mtime REAL,
  size INTEGER,
  fingerprint TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT
);

CREATE TABLE IF NOT EXISTS trash (
  file TEXT PRIMARY KEY,
  history TEXT DEFAULT '[]',
  reasons TEXT DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS search_index (
  file TEXT PRIMARY KEY,
  symbols TEXT DEFAULT '[]',
  grams TEXT DEFAULT '[]'
);
"""


class _BatchContext:
    """GeneBank.batch() 的上下文管理器实现（v3.2 性能优化）：
    with bank.batch(): ... 内所有写操作攒在同一个事务里，退出时一次性 commit。
    首次扫描把 N 条基因 N 次 fsync 变成 1 次（Termux 闪存上每次 fsync 约 50-100ms）。"""

    def __init__(self, bank: "GeneBank") -> None:
        self.bank = bank

    def __enter__(self) -> "GeneBank":
        bank = self.bank
        bank._batch_depth += 1
        bank._auto_commit = False
        return bank

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        bank = self.bank
        bank._batch_depth -= 1
        if bank._batch_depth == 0:
            bank._auto_commit = True
            if exc_type is None:
                bank.conn.commit()
            else:
                bank.conn.rollback()
        return False


class GeneBank:
    """基因库：SQLite 持久化（原子事务 / 索引查询 / WAL 并发安全）。
    v2 的 gene_bank.json / mtime_index.json 首次运行自动迁移。"""

    def __init__(self, root: str):
        self.root = root
        # v4.4修复：单文件支持 - 如果root是文件，使用所在目录或临时目录
        if os.path.isfile(root):
            import tempfile
            file_dir = os.path.dirname(os.path.abspath(root))
            # 优先使用文件所在目录，如果不可写则使用临时目录
            try:
                test_file = os.path.join(file_dir, ".tree_farm_test")
                with open(test_file, "w") as f:
                    f.write("test")
                os.remove(test_file)
                self.store_dir = os.path.join(file_dir, ".tree_farm")
            except (PermissionError, OSError):
                self.store_dir = os.path.join(tempfile.gettempdir(), ".tree_farm_" + os.path.basename(root))
        else:
            self.store_dir = os.path.join(root, ".tree_farm")
        os.makedirs(self.store_dir, exist_ok=True)
        self.db_path = os.path.join(self.store_dir, DB_FILE)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(DB_SCHEMA)
        self._auto_commit = True     # False = 批量事务模式（batch() 内）
        self._batch_depth = 0        # 支持嵌套 batch
        self._migrate_json()
        # _seen：内存去重缓存。v3.3 改为四元组 (source, target, relation, symbol)——
        # 同一个文件对可有多条不同符号的 call/inherit 基因（跨文件多符号调用是分析深度关键）
        self._seen: Set[Tuple[str, str, str, str]] = set()
        for row in self.conn.execute("SELECT source, target, relation, symbol FROM genes"):
            self._seen.add(self._seen_key(row))

    @staticmethod
    def _seen_key(row: Any) -> Tuple[str, str, str, str]:
        """从数据库行/基因字典构造 _seen 键（v3.3）"""
        if isinstance(row, dict):
            return (row["source"], row["target"], row.get("relation", ""), row.get("symbol", ""))
        return (row["source"], row["target"], row["relation"], row["symbol"])

    def batch(self) -> _BatchContext:
        """批量事务：with bank.batch(): ... 退出时一次性 commit。"""
        return _BatchContext(self)

    def _commit(self) -> None:
        """提交（批量模式下攒着，由 batch() 退出时统一提交）"""
        if self._auto_commit:
            self.conn.commit()

    def close(self) -> None:
        """关闭数据库连接（防 ResourceWarning / 句柄泄漏）"""
        try:
            self.conn.close()
        except sqlite3.Error:
            pass

    def __del__(self) -> None:
        # 兜底：测试/异常路径未显式 close 时，GC 阶段也要释放连接
        try:
            self.close()
        except Exception:
            pass

    def _migrate_json(self) -> None:
        """v2 JSON 文件 → SQLite（迁移后改名为 .bak，不删用户数据）。
        若 db 丢失但 .bak 仍在且基因表为空 → 从 .bak 恢复（数据不丢）。"""
        store = self.store_dir
        gene_file = os.path.join(store, "gene_bank.json")
        mtime_file = os.path.join(store, "mtime_index.json")
        session_file = os.path.join(store, "session.json")
        trash_file = os.path.join(store, "trash.json")

        # db 丢失恢复：.bak 是唯一副本时从它恢复
        if (not os.path.isfile(gene_file) and os.path.isfile(gene_file + ".bak")
                and self.conn.execute("SELECT COUNT(*) c FROM genes").fetchone()["c"] == 0):
            os.rename(gene_file + ".bak", gene_file)
            log.info("检测到 db 丢失，从 gene_bank.json.bak 恢复")
        if (not os.path.isfile(mtime_file) and os.path.isfile(mtime_file + ".bak")
                and self.conn.execute("SELECT COUNT(*) c FROM mtime").fetchone()["c"] == 0):
            os.rename(mtime_file + ".bak", mtime_file)
        if (not os.path.isfile(session_file) and os.path.isfile(session_file + ".bak")
                and self.conn.execute("SELECT COUNT(*) c FROM meta").fetchone()["c"] == 0):
            os.rename(session_file + ".bak", session_file)
        if (not os.path.isfile(trash_file) and os.path.isfile(trash_file + ".bak")
                and self.conn.execute("SELECT COUNT(*) c FROM trash").fetchone()["c"] == 0):
            os.rename(trash_file + ".bak", trash_file)

        if os.path.isfile(gene_file):
            with open(gene_file, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                raw = raw.get("genes", [])
            for g in raw:
                g = normalize_gene(g)
                self.conn.execute(
                    "INSERT OR IGNORE INTO genes VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (g["id"], g["source"], g["target"], g["symbol"], g["relation"],
                     g["direction"], g["kind"], g["confidence"], int(g["verified"]),
                     g["first_seen"], g["last_verified"]))
            # 记录迁移源的 schema 版本：旧库升级后需全量重扫一次补齐新类型基因
            self._meta_set("gene_schema", 2)
            os.rename(gene_file, gene_file + ".bak")
            log.info("已迁移 gene_bank.json → SQLite（%d 条基因）", len(raw))
        if os.path.isfile(mtime_file):
            with open(mtime_file, "r", encoding="utf-8") as f:
                idx = json.load(f)
            for path, meta in idx.items():
                m, s, fp = (meta + [""])[:3] if isinstance(meta, list) else (meta, 0, "")
                self.conn.execute("INSERT OR REPLACE INTO mtime VALUES (?,?,?,?)",
                                  (path, m, s, fp))
            os.rename(mtime_file, mtime_file + ".bak")
        if os.path.isfile(session_file):
            with open(session_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._meta_set_many(data)
            os.rename(session_file, session_file + ".bak")
        if os.path.isfile(trash_file):
            with open(trash_file, "r", encoding="utf-8") as f:
                records = json.load(f)
            for path, r in records.items():
                hist = [True] * r.get("fails", 0)
                reasons = r.get("reasons", [])
                self.conn.execute("INSERT OR REPLACE INTO trash VALUES (?,?,?)",
                                  (path, json.dumps(hist[-TRASH_WINDOW:]), json.dumps(reasons)))
            os.rename(trash_file, trash_file + ".bak")
        self._commit()

    # ---- meta（session 状态，KV 表） ----
    def _meta_get(self, key: str, default: Any = None) -> Any:
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except (TypeError, ValueError):
            return row["value"]

    def _meta_set(self, key: str, value: Any) -> None:
        self.conn.execute("INSERT OR REPLACE INTO meta VALUES (?,?)",
                          (key, json.dumps(value, ensure_ascii=False)))
        self._commit()

    def _meta_set_many(self, data: Dict[str, Any]) -> None:
        for k, v in data.items():
            self.conn.execute("INSERT OR REPLACE INTO meta VALUES (?,?)",
                              (k, json.dumps(v, ensure_ascii=False)))
        self._commit()

    # ---- 基因 CRUD ----
    def add(self, g: Dict[str, Any]) -> bool:
        g = normalize_gene(g)
        key = self._seen_key(g)
        if key in self._seen:
            return False
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO genes VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (g["id"], g["source"], g["target"], g["symbol"], g["relation"],
             g["direction"], g["kind"], g["confidence"], int(g["verified"]),
             g["first_seen"], g["last_verified"]))
        self._commit()
        self._seen.add(key)
        return cur.rowcount > 0

    def eat(self, source: str, target: str) -> bool:
        """吃掉假耦合：删除 source→target 的全部基因（含不同符号的 call 基因）"""
        rows = self.conn.execute("SELECT relation, symbol FROM genes WHERE source=? AND target=?",
                                 (source, target)).fetchall()
        for r in rows:
            self._seen.discard((source, target, r["relation"], r["symbol"]))
        cur = self.conn.execute("DELETE FROM genes WHERE source=? AND target=?",
                                (source, target))
        self._commit()
        return cur.rowcount > 0

    def replace_source(self, source: str, genes: List[Dict[str, Any]]) -> Tuple[int, int]:
        """增量更新：整体替换某文件的所有基因（先删旧再插新）。返回 (删除数, 新增数)"""
        rows = self.conn.execute("SELECT target, relation, symbol FROM genes WHERE source=?",
                                 (source,)).fetchall()
        for r in rows:
            self._seen.discard((source, r["target"], r["relation"], r["symbol"]))
        cur = self.conn.execute("DELETE FROM genes WHERE source=?", (source,))
        removed = cur.rowcount
        for g in genes:
            g["source"] = source
            g = normalize_gene(g)
            self.conn.execute(
                "INSERT OR IGNORE INTO genes VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (g["id"], g["source"], g["target"], g["symbol"], g["relation"],
                 g["direction"], g["kind"], g["confidence"], int(g["verified"]),
                 g["first_seen"], g["last_verified"]))
        self._commit()
        added = 0
        for g in genes:
            key = self._seen_key(g)
            if key not in self._seen:
                added += 1
            self._seen.add(key)
        return removed, added

    def drop_source(self, source: str) -> int:
        """删除某文件作为【依赖方】的所有基因。返回回收数"""
        rows = self.conn.execute("SELECT target, relation, symbol FROM genes WHERE source=?",
                                 (source,)).fetchall()
        for r in rows:
            self._seen.discard((source, r["target"], r["relation"], r["symbol"]))
        cur = self.conn.execute("DELETE FROM genes WHERE source=?", (source,))
        self._commit()
        return cur.rowcount

    def drop_file(self, path: str, module_names: Optional[List[str]] = None) -> int:
        """文件删除级联回收（v3）：
        删除 source=该文件 或 target=该文件（或它的模块名）的基因，
        防止「别人引用一个已不存在的文件」的悬空基因留在库里。
        module_names 传 rel_module 得到的模块名。"""
        targets = [path] + (module_names or [])
        marks = ",".join("?" for _ in targets)
        rows = self.conn.execute(
            f"SELECT source, target, relation, symbol FROM genes WHERE source=? OR target IN ({marks})",
            (path, *targets)).fetchall()
        for r in rows:
            self._seen.discard((r["source"], r["target"], r["relation"], r["symbol"]))
        cur = self.conn.execute(
            f"DELETE FROM genes WHERE source=? OR target IN ({marks})",
            (path, *targets))
        self._commit()
        return cur.rowcount

    def rename_source(self, old: str, new: str) -> int:
        """重命名：source 路径迁移 + target 路径迁移（v3 增强）。返回迁移数"""
        cur = self.conn.execute("UPDATE genes SET source=? WHERE source=?", (new, old))
        cur2 = self.conn.execute("UPDATE genes SET target=? WHERE target=?", (new, old))
        # 刷新 _seen：旧 source/target 的 key 移除，新 key 加入
        rows = self.conn.execute(
            "SELECT source, target, relation, symbol FROM genes WHERE source=? OR target=?",
            (new, new)).fetchall()
        self._seen = {k for k in self._seen if k[0] != old and k[1] != old}
        self._seen.update((r["source"], r["target"], r["relation"], r["symbol"]) for r in rows)
        self._commit()
        return cur.rowcount + cur2.rowcount

    def sources_referencing(self, target: str) -> List[str]:
        """谁引用了这个 target（路径或模块名）——重命名/删除后需要重扫的源文件"""
        rows = self.conn.execute(
            "SELECT DISTINCT source FROM genes WHERE target=?", (target,)).fetchall()
        return [r["source"] for r in rows]

    def genes_of_source(self, source: str) -> List[Dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM genes WHERE source=?", (source,)).fetchall()
        return [dict(r) for r in rows]

    def genes_of_target(self, target: str) -> List[Dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM genes WHERE target=?", (target,)).fetchall()
        return [dict(r) for r in rows]

    def all_genes(self) -> List[Dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM genes").fetchall()
        return [dict(r) for r in rows]

    def gene_count(self) -> int:
        """基因总数（v3.2：COUNT 查询，不拉全表）"""
        return self.conn.execute("SELECT COUNT(*) c FROM genes").fetchone()["c"]

    def stats(self) -> Dict[str, int]:
        total = self.conn.execute("SELECT COUNT(*) c FROM genes").fetchone()["c"]
        strong = self.conn.execute("SELECT COUNT(*) c FROM genes WHERE kind='strong'").fetchone()["c"]
        weak = self.conn.execute("SELECT COUNT(*) c FROM genes WHERE kind='weak'").fetchone()["c"]
        calls = self.conn.execute("SELECT COUNT(*) c FROM genes WHERE relation IN ('call','inherit')").fetchone()["c"]
        return {"genes": total, "strong": strong, "weak": weak, "deep": calls}

    def db_info(self) -> Dict[str, Any]:
        rows = self.conn.execute(
            "SELECT relation, COUNT(*) c FROM genes GROUP BY relation").fetchall()
        return {
            "db": os.path.basename(self.db_path),
            "size_kb": round(os.path.getsize(self.db_path) / 1024, 1) if os.path.exists(self.db_path) else 0,
            "relations": {r["relation"]: r["c"] for r in rows},
        }

    # ---- mtime 索引（增量扫描 + 腐肉检测 + 重命名指纹） ----
    def mtime_snapshot(self) -> Dict[str, List[Any]]:
        rows = self.conn.execute("SELECT file, mtime, size, fingerprint FROM mtime").fetchall()
        return {r["file"]: [r["mtime"], r["size"], r["fingerprint"]] for r in rows}

    def mtime_update(self, path: str, mtime: float, size: int, fingerprint: str) -> None:
        self.conn.execute("INSERT OR REPLACE INTO mtime VALUES (?,?,?,?)",
                          (path, mtime, size, fingerprint))
        self._commit()

    def mtime_remove(self, path: str) -> None:
        self.conn.execute("DELETE FROM mtime WHERE file=?", (path,))
        self._commit()

    # ---- 搜索索引（符号 + 内容 n-gram，预构建持久化） ----
    def save_search_index(self, file: str, symbols: List[str], grams: List[str]) -> None:
        self.conn.execute("INSERT OR REPLACE INTO search_index VALUES (?,?,?)",
                          (file, json.dumps(symbols, ensure_ascii=False),
                           json.dumps(grams, ensure_ascii=False)))
        self._commit()

    def drop_search_index(self, file: str) -> None:
        self.conn.execute("DELETE FROM search_index WHERE file=?", (file,))
        self._commit()

    def load_search_index(self) -> Tuple[Dict[str, List[str]], Dict[str, List[str]]]:
        symbol_map: Dict[str, List[str]] = {}
        content_index: Dict[str, List[str]] = {}
        for row in self.conn.execute("SELECT * FROM search_index").fetchall():
            syms = json.loads(row["symbols"])
            for s in syms:
                symbol_map.setdefault(s, []).append(row["file"])
            content_index[row["file"]] = json.loads(row["grams"])
        return symbol_map, content_index


class TrashBin:
    """垃圾箱熔断器（v3：滑动窗口，对齐设计文档「3 次里 2 次」）"""

    def __init__(self, bank: GeneBank, window: int = TRASH_WINDOW, fail_limit: int = TRASH_FAIL_LIMIT):
        self.bank = bank
        self.window = window
        self.fail_limit = fail_limit

    def _history(self, path: str) -> List[bool]:
        row = self.bank.conn.execute("SELECT history FROM trash WHERE file=?", (path,)).fetchone()
        return json.loads(row["history"]) if row else []

    def _reasons(self, path: str) -> List[str]:
        row = self.bank.conn.execute("SELECT reasons FROM trash WHERE file=?", (path,)).fetchone()
        return json.loads(row["reasons"]) if row else []

    def record(self, path: str, failed: bool, reason: str = "") -> bool:
        """记录一次小鸟验证结果。返回是否触发熔断。
        failed=True 表示候选基因被小虫子吃掉（假耦合）。"""
        hist = self._history(path) + [failed]
        hist = hist[-self.window:]
        reasons = self._reasons(path)
        if failed and reason:
            reasons.append(reason)
        reasons = reasons[-10:]
        self.bank.conn.execute("INSERT OR REPLACE INTO trash VALUES (?,?,?)",
                               (path, json.dumps(hist), json.dumps(reasons)))
        self.bank._commit()
        recent = hist[-self.window:]
        return len(recent) >= self.fail_limit and sum(recent) >= self.fail_limit

    def reset(self, path: str) -> None:
        self.bank.conn.execute("DELETE FROM trash WHERE file=?", (path,))
        self.bank._commit()

    def trash(self) -> List[str]:
        rows = self.bank.conn.execute("SELECT * FROM trash").fetchall()
        out = []
        for r in rows:
            hist = json.loads(r["history"])
            if len(hist) >= self.fail_limit and sum(hist[-self.window:]) >= self.fail_limit:
                out.append(r["file"])
        return out

    def is_empty(self) -> bool:
        return len(self.trash()) == 0

    def report(self) -> str:
        if self.is_empty():
            return "垃圾箱: 空 ✅"
        lines = [f"垃圾箱: {len(self.trash())} 个文件未处理 ⚠️（不清空 = 任务未完成）"]
        for f in self.trash():
            reasons = self._reasons(f)
            lines.append(f"  {f}  最近 {self.window} 次验证失败 "
                         f"{sum(self._history(f)[-self.window:])} 次: {', '.join(reasons) or '(无记录)'}")
        return "\n".join(lines)


class Session:
    """会话状态（v3 存 SQLite meta 表）"""

    DEFAULTS: Dict[str, Any] = {"round": 1, "birds_this_round": 0, "weak_pending": {},
                                "llm_enabled": True}

    def __init__(self, bank: GeneBank):
        self.bank = bank
        self.data = copy.deepcopy(self.DEFAULTS)
        for k in self.DEFAULTS:
            v = bank._meta_get(k, None)
            if v is not None:
                self.data[k] = v
        self.data.setdefault("llm_enabled", True)

    def _save(self) -> None:
        self.bank._meta_set_many(self.data)

    def set_llm(self, on: bool) -> None:
        self.data["llm_enabled"] = on
        self._save()

    def next_round(self) -> None:
        self.data["round"] += 1
        self.data["birds_this_round"] = 0
        self._save()

    def add_bird(self) -> None:
        self.data["birds_this_round"] += 1
        self._save()

    def converged(self) -> bool:
        return self.data["birds_this_round"] <= CONVERGE_LIMIT


class WeedIndex:
    """杂草索引：只存 路径 + 大小 + 首行摘要，不读全文"""

    def __init__(self):
        self.entries: List[Dict[str, Any]] = []

    def build(self, weed_files: List[str]) -> None:
        self.entries = []
        for f in weed_files:
            summary = "(无法读取)"
            try:
                with open(f, "r", encoding="utf-8", errors="ignore") as fh:
                    first_lines = [fh.readline().strip() for _ in range(WEED_SUMMARY_LINES)]
                summary = " | ".join(x for x in first_lines if x)
                if len(summary) > WEED_SUMMARY_LEN:
                    summary = summary[:WEED_SUMMARY_LEN] + "..."
            except Exception:
                pass
            try:
                size = os.path.getsize(f)
            except OSError:
                size = 0
            self.entries.append({"path": f, "size": size, "summary": summary})

    def report(self) -> str:
        total = sum(e["size"] for e in self.entries)
        lines = [f"杂草 {len(self.entries)} 个文件，合计 {total} 字节（只存索引，未读全文）"]
        for e in self.entries[:WEED_SHOW]:
            lines.append(f"  {e['path']}  [{e['size']}B]  {e['summary']}")
        if len(self.entries) > WEED_SHOW:
            lines.append(f"  ... 还有 {len(self.entries) - WEED_SHOW} 个")
        return "\n".join(lines)


class SmallTree:
    """小树：双向过滤（v3 不变：O(1) 预构建索引）"""

    def __init__(self, out_index: Dict[str, List[Dict[str, Any]]],
                 inn_index: Dict[str, List[Dict[str, Any]]], ratio: float = SMALL_TREE_RATIO):
        self.out_index = out_index
        self.inn_index = inn_index
        self.ratio = ratio

    def active(self, tree_count: int) -> bool:
        return len(self.inn_index) + len(self.out_index) > tree_count * self.ratio

    def related(self, py_file: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """out = 它引用别人（正向，重构看这个）；inn = 别人引用它（反向，修 bug 看这个）
        O(1) 查询——大项目必备（9187 文件 × 5.7 万基因 = 5 亿次循环会卡死）"""
        return self.out_index.get(py_file, []), self.inn_index.get(py_file, [])
