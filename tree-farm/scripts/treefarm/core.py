# -*- coding: utf-8 -*-
"""树场机制 —— 树场主体（v4.7；v3.6 拆分自单文件 tree_farm.py）。

包含：TreeFarm 主类（建库/增量更新/小鸟机制/LLM 侦察/简报/代码分析/调用图/查重/修复协议）。
组装 common / parser / storage / config / analysis 各层。
"""

import json
import logging
import os
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from .analysis import (calculate_complexity_any, calculate_debt,
                       collect_function_grams, detect_architecture_layers,
                       detect_circular_dependencies, detect_code_smells,
                       detect_dead_code, detect_duplicate_func_pairs,
                       detect_duplicates, generate_call_graph,
                       generate_call_graph_data, impact_analysis)
from .common import (BRIEF_MAX_SYMBOLS, CONVERGE_LIMIT, LLM_CODE_CHARS,
                     READ_HEAD_BYTES, SCHEMA_VERSION, TRASH_FAIL_LIMIT,
                     TRASH_WINDOW, VERSION, WEAK_CONFIRM_LIMIT, SymbolIndex,
                     _cache, _gram_hashes, file_fingerprint, read_text,
                     rel_module, scan, scan_changes)
from .config import LLMClient, load_config
from .parser import (extract_call_graph, extract_genes, extract_go_call_graph,
                     extract_java_call_graph, extract_js_call_graph,
                     extract_rust_call_graph, extract_symbols, verify_candidate)
from .storage import GeneBank, Session, SmallTree, TrashBin, WeedIndex

log = logging.getLogger("treefarm")


class TreeFarm:
    def __init__(self, root: str):
        self.root = root
        self.scanned: Optional[Dict[str, List[str]]] = None
        self.bank: Optional[GeneBank] = None
        self.weed_index: Optional[WeedIndex] = None
        self.small_tree: Optional[SmallTree] = None
        self.trash: Optional[TrashBin] = None
        self.session: Optional[Session] = None
        self.symbol_index = SymbolIndex()
        self.content_index: Dict[str, List[str]] = {}
        self.module_map: Dict[str, str] = {}
        self.symbol_map: Dict[str, List[str]] = {}   # 符号 → [文件...]（跨文件 call 验证用）
        self._symbol_map_ready = False               # 惰性构建标志（v3.2：增量扫描免全量符号表）
        self._spec_ctx: Optional[Dict[str, Any]] = None   # v4.7：项目功能画像（--spec/--spec-read 注入）

    # ===== 建库 / 增量更新 =====
    def plant(self, progress: Optional[Callable[[int, int, str], None]] = None) -> Tuple[bool, Dict[str, int]]:
        """建库 / 增量更新（v3.2：FileCache + 批量事务，首次扫描不再重复读盘 / 逐条 fsync）。
        progress: 可选回调 (已完成数, 总数, 当前文件)，用于显示扫描进度。"""
        _cache.clear()  # 本次扫描用全新缓存，结束后立即释放（内存有界）
        try:
            return self._plant_inner(progress)
        finally:
            _cache.clear()

    def _plant_inner(self, progress: Optional[Callable[[int, int, str], None]]) -> Tuple[bool, Dict[str, int]]:
        # v3.2：配置文件支持忽略规则（.treefarm.toml / .treefarm.json 的 [scan] 段）
        cfg = load_config(self.root)
        scan_cfg = cfg.get("scan", {}) if isinstance(cfg, dict) else {}
        self.scanned = scan(self.root,
                            ignore_patterns=scan_cfg.get("ignore") if isinstance(scan_cfg, dict) else None,
                            ignore_dirs=scan_cfg.get("ignore_dirs") if isinstance(scan_cfg, dict) else None)
        tree_files = self.scanned["tree"]

        self.weed_index = WeedIndex()
        self.weed_index.build(self.scanned["weed"])

        self.bank = GeneBank(self.root)
        self.trash = TrashBin(self.bank)
        self.session = Session(self.bank)
        is_new = self.bank.gene_count() == 0

        # 模块名映射（v3 修复：splitext 而非 [:-3]）
        self.module_map = {rel_module(self.root, f): f for f in tree_files}
        for f in tree_files:
            mod = rel_module(self.root, f)
            self.module_map.setdefault(mod, f)

        # 项目符号表（跨文件 call/inherit 基因的目标验证）——
        # v3.2 改为惰性：只在 _build_genes 真正需要时构建（增量扫描无变化则完全跳过）

        added = {"import": 0, "call": 0, "inherit": 0}
        eaten = 0
        renamed = 0

        def _notify(i: int, f: str) -> None:
            if progress:
                progress(i, len(tree_files), f)

        # v3.2 性能优化：整个扫描过程包进一个批量事务（N 条基因 N 次 fsync → 1 次）
        with self.bank.batch():
            if is_new:
                # 冷启动：全量提取（import + 函数调用 + 类继承）
                for i, f in enumerate(tree_files, 1):
                    _notify(i, f)
                    n_imp, n_deep = self._build_genes(f)
                    added["import"] += n_imp
                    added["call"] += n_deep
                    try:
                        st = os.stat(f)
                        self.bank.mtime_update(f, st.st_mtime, st.st_size, file_fingerprint(f))
                    except OSError:
                        pass
                self.bank._meta_set("gene_schema", SCHEMA_VERSION)
            else:
                # 默认按旧 schema（2）处理：无记录的老库升级后触发一次全量重扫补齐新类型基因
                stored_schema = self.bank._meta_get("gene_schema", 2)
                if stored_schema < SCHEMA_VERSION:
                    # 旧库升级（如 v2→v3 新增函数级分析）：全量重扫一次补齐新类型基因，
                    # 之后恢复正常增量。防止旧库永远缺 call/inherit 基因。
                    log.info("schema v%d → v%d：全量重扫补齐新类型基因", stored_schema, SCHEMA_VERSION)
                    for i, f in enumerate(tree_files, 1):
                        _notify(i, f)
                        old_keys = {(g["source"], g["target"]) for g in self.bank.genes_of_source(f)}
                        self.bank.drop_source(f)
                        n_imp, n_deep = self._build_genes(f, old_keys)
                        added["import"] += n_imp
                        added["call"] += n_deep
                        try:
                            st = os.stat(f)
                            self.bank.mtime_update(f, st.st_mtime, st.st_size, file_fingerprint(f))
                        except OSError:
                            pass
                    self.bank._meta_set("gene_schema", SCHEMA_VERSION)
                else:
                    changed, deleted, renames = scan_changes(tree_files, self.bank)
                    # 重命名：迁移基因 + 引用旧文件/旧模块名的源文件强制重扫
                    for old, new in renames:
                        n = self.bank.rename_source(old, new)
                        self.bank.mtime_remove(old)
                        self.bank.drop_search_index(old)
                        if new in changed:
                            self.bank.mtime_update(new, *changed[new])
                            del changed[new]
                        renamed += n
                        log.info("检测到重命名: %s → %s（迁移 %d 条基因）", old, new, n)
                        for src in self.bank.sources_referencing(old):
                            if os.path.isfile(src) and src not in changed:
                                st = os.stat(src)
                                changed[src] = (st.st_mtime, st.st_size, file_fingerprint(src))
                                log.info("  引用旧路径的源文件重扫: %s", src)
                        old_mod = rel_module(self.root, old)
                        for src in self.bank.sources_referencing(old_mod):
                            if os.path.isfile(src) and src not in changed:
                                st = os.stat(src)
                                changed[src] = (st.st_mtime, st.st_size, file_fingerprint(src))
                                log.info("  引用旧模块名的源文件重扫: %s", src)
                    # 删除：级联回收（source + target + 模块名引用）
                    for old in deleted:
                        if any(old == o for o, _ in renames):
                            continue
                        eaten += self.bank.drop_file(old, [rel_module(self.root, old)])
                        self.bank.mtime_remove(old)
                        self.bank.drop_search_index(old)
                        log.info("文件已删除，级联回收基因: %s", old)
                    # 变更（排序展示，进度稳定）
                    for i, (f, meta) in enumerate(sorted(changed.items()), 1):
                        if any(f == nw for _, nw in renames):
                            continue
                        _notify(i, f)
                        old_keys = {(g["source"], g["target"]) for g in self.bank.genes_of_source(f)}
                        self.bank.drop_source(f)
                        n_imp, n_deep = self._build_genes(f, old_keys)
                        added["import"] += n_imp
                        added["call"] += n_deep
                        self.bank.mtime_update(f, *meta)
                        log.info("增量重扫: %s（+%d 条）", f, n_imp + n_deep)

            # 预构建双向索引（大项目防 O(n×m) 卡死）
            out_index: Dict[str, List[Dict[str, Any]]] = {}
            inn_index: Dict[str, List[Dict[str, Any]]] = {}
            inn_seen: Set[Tuple[str, str]] = set()
            for g in self.bank.all_genes():
                out_index.setdefault(g["source"], []).append(g)
                tf = self.module_map.get(g["target"])
                if not tf:
                    # call/inherit 基因的 target 是文件路径（见 _build_genes），直接可用
                    tf = g["target"] if os.path.isfile(g["target"]) else None
                if tf:
                    # 去重：同一文件对多条不同符号的基因都保留（v3.3 多符号调用）
                    key = (g["source"], tf, g.get("symbol", ""))
                    if key not in inn_seen:
                        inn_seen.add(key)
                        inn_index.setdefault(tf, []).append(g)
            self.small_tree = SmallTree(out_index, inn_index)

            # 搜索索引（符号 + 内容 n-gram）：持久化复用，仅重建变更文件
            self._build_search_index(tree_files, is_new)

        return is_new, {"added": added, "eaten": eaten, "renamed": renamed}

    def _ensure_symbol_map(self) -> None:
        """惰性构建项目符号表（v3.2）：首次 _build_genes 时才全量扫描，增量无变化则跳过"""
        if self._symbol_map_ready or self.scanned is None:
            return
        self.symbol_map = {}
        for f in self.scanned["tree"]:
            for s in extract_symbols(f):
                self.symbol_map.setdefault(s, []).append(f)
        self._symbol_map_ready = True

    def _build_genes(self, f: str, old_keys: Optional[Set[Tuple[str, str]]] = None) -> Tuple[int, int]:
        """提取某文件全部基因（import 级 + 函数级 + 继承级）。
        返回 (import 新增数, 深度基因新增数)——只统计【旧库里没有的新耦合】，
        重建已有基因不算新增（增量重扫时统计才准确）。"""
        self._ensure_symbol_map()
        old_keys = old_keys or set()
        n_imp = 0
        for target, rel in extract_genes(f):
            if self.bank.add({"source": f, "target": target, "relation": rel,
                              "kind": "strong", "confidence": 1.0,
                              "verified": True, "ts": time.time()}):
                if (f, target) not in old_keys:
                    n_imp += 1

        # 函数级调用 + 类继承（Python：AST 精确；JS/TS：v3.3 轻量解析器；仅跨文件入库）
        n_deep = 0
        ext = os.path.splitext(f)[1].lower()
        if ext == ".py":
            calls, inherits = extract_call_graph(f)
            for name in calls:
                n_deep += self._add_deep_gene(f, name, "call", old_keys)
            for name in inherits:
                n_deep += self._add_deep_gene(f, name, "inherit", old_keys)
        elif ext in (".js", ".ts"):
            _, js_calls = extract_js_call_graph(f)
            for name in js_calls:
                n_deep += self._add_deep_gene(f, name, "call", old_keys)
        elif ext == ".java":
            java_calls, java_inherits = extract_java_call_graph(f)
            for name in java_calls:
                n_deep += self._add_deep_gene(f, name, "call", old_keys)
            for name in java_inherits:
                n_deep += self._add_deep_gene(f, name, "inherit", old_keys)
        elif ext == ".go":
            go_calls, go_inherits = extract_go_call_graph(f)
            for name in go_calls:
                n_deep += self._add_deep_gene(f, name, "call", old_keys)
            for name in go_inherits:
                n_deep += self._add_deep_gene(f, name, "inherit", old_keys)
        elif ext == ".rs":
            rust_calls, rust_inherits = extract_rust_call_graph(f)
            for name in rust_calls:
                n_deep += self._add_deep_gene(f, name, "call", old_keys)
            for name in rust_inherits:
                n_deep += self._add_deep_gene(f, name, "inherit", old_keys)
        return n_imp, n_deep

    def _add_deep_gene(self, f: str, name: str, rel: str,
                       old_keys: Set[Tuple[str, str]]) -> int:
        """函数/方法级基因入库（Python + JS/TS + Java 共用）：
        目标符号存在于【其他文件】才入库（树场管跨文件耦合，文件内部调用不管）。
        返回新增数。"""
        leaf = name.split(".")[-1]
        targets = self.symbol_map.get(name) or self.symbol_map.get(leaf)
        if not targets:
            return 0
        added = 0
        abs_f = os.path.abspath(f)
        for tf in targets:
            if os.path.abspath(tf) == abs_f:
                continue  # 文件内部调用不入库
            verified = leaf in extract_symbols(tf)
            if self.bank.add({"source": f, "target": tf, "symbol": name,
                              "relation": rel, "kind": "strong",
                              "confidence": 1.0 if verified else 0.7,
                              "verified": verified, "ts": time.time()}):
                if (f, tf) not in old_keys:
                    added += 1
        return added

    def _build_search_index(self, tree_files: List[str], force: bool) -> None:
        """搜索索引：持久化到 SQLite，仅重建新增/变更文件（v3 预构建）"""
        if force:
            # 新库：全量构建
            for f in tree_files:
                syms = extract_symbols(f)
                grams = _gram_hashes(read_text(f)[:READ_HEAD_BYTES])
                self.bank.save_search_index(f, syms, grams)
        else:
            # 已有库：只补缺失条目（变更文件已在增量流程中 drop，这里重建）
            rows = {r["file"] for r in self.bank.conn.execute("SELECT file FROM search_index")}
            for f in tree_files:
                if f not in rows:
                    syms = extract_symbols(f)
                    grams = _gram_hashes(read_text(f)[:READ_HEAD_BYTES])
                    self.bank.save_search_index(f, syms, grams)
        symbol_map, content_index = self.bank.load_search_index()
        self.symbol_index = SymbolIndex(symbol_map)
        self.content_index = content_index

    # ===== 小鸟机制（v3：垃圾箱滑动窗口） =====
    def bird(self, source: str, target: str, is_weak: bool = False) -> Dict[str, Any]:
        self.session.add_bird()
        source = os.path.abspath(source)
        if is_weak:
            key = f"{source}|{target}"
            pending = self.session.data["weak_pending"]
            pending[key] = pending.get(key, 0) + 1
            if pending[key] >= WEAK_CONFIRM_LIMIT:
                added = self.bank.add({"source": source, "target": target, "kind": "weak",
                                       "confidence": 0.5, "verified": False, "ts": time.time()})
                return {"status": "weak_confirmed", "added": added,
                        "confirms": pending[key], "note": "弱耦合已入库（低优先级）"}
            self.session._save()
            return {"status": "weak_pending", "confirms": pending[key],
                    "note": f"弱耦合待确认（还需 {WEAK_CONFIRM_LIMIT - pending[key]} 个独立分支）"}
        if verify_candidate(source, target):
            added = self.bank.add({"source": source, "target": target, "kind": "strong",
                                   "confidence": 1.0, "verified": True, "ts": time.time()})
            self.trash.record(source, False)  # 验证成功也记账（滑动窗口）
            return {"status": "verified_true", "added": added, "note": "小虫子验证为真，基因入库"}
        else:
            self.bank.eat(source, target)
            failed = self.trash.record(source, True, f"假耦合: {target}")
            msg = "小虫子验证为假，基因被吃掉 🐛"
            if failed:
                msg += (f" —— ⚠️ 最近 {TRASH_WINDOW} 次验证 ≥{TRASH_FAIL_LIMIT} 次失败，"
                        "该文件已丢入垃圾箱！")
            return {"status": "eaten_false", "trash_triggered": failed, "note": msg}

    # ===== LLM 自动报小鸟 =====
    def analyze(self, target_file: str, llm: LLMClient) -> str:
        if not self.session.data.get("llm_enabled", True):
            return ("📵 LLM 功能已关闭（用户说过「关闭 LLM」）。"
                    "说「开启 LLM」或运行 --llm on 恢复。")
        if not os.path.isfile(target_file):
            return f"✖ 文件不存在: {target_file}"
        code = read_text(target_file)
        out, inn = self.small_tree.related(target_file)
        known = [(g["target"], g["kind"]) for g in out]
        rel = os.path.relpath(target_file, self.root)

        prompt = self._build_analyze_prompt(rel, known, code)

        if not llm.available():
            return self._dry_run_prompt(llm, prompt)
        try:
            raw = llm.chat([{"role": "user", "content": prompt}])
        except Exception as e:
            return f"✖ LLM 调用失败: {e}"
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            return f"✖ LLM 返回不是合法 JSON: {raw[:200]}"

        birds = data.get("birds", [])
        if not birds:
            return "🐦 小鸟侦察员没发现新耦合（或都已在基因库里）。"
        lines = [f"🐦 小鸟侦察员发现 {len(birds)} 个候选，正在交给小虫子验证..."]
        for b in birds:
            tgt = b.get("target", "")
            relation = b.get("relation", "weak")
            is_weak = relation == "weak"
            result = self.bird(target_file, tgt, is_weak=is_weak)
            lines.append(f"  {tgt} [{relation}] 证据: {b.get('evidence', '')} → {result['status']}")
        return "\n".join(lines)

    def _build_analyze_prompt(self, rel: str, known: List[Tuple[str, str]], code: str) -> str:
        return (
            "你是树场机制里的「小鸟侦察员」。你的任务是找出代码里隐藏的可疑耦合。\n"
            f"正在分析文件: {rel}\n"
            f"它已知的依赖（基因库，已验证）: {known}\n"
            "请阅读下面的代码，找出【基因库还没有的】可疑耦合，包括隐式耦合\n"
            "（比如：函数返回值格式被外部依赖、共享的常量/约定、时序依赖、数据格式耦合）。\n"
            "只报有代码证据的，不确定的别报。\n"
            "输出 JSON（严格格式）:\n"
            '{"birds": [{"target": "模块名或符号", "relation": "import|call|inherit|weak", "evidence": "一句话证据"}]}\n'
            "没有发现就返回 {\"birds\": []}\n\n"
            f"=== 代码开始 ===\n{code[:LLM_CODE_CHARS]}\n=== 代码结束 ==="
        )

    def _dry_run_prompt(self, llm: LLMClient, prompt: str) -> str:
        return ("⚠️ " + llm.source + "。这是 dry-run 预览——引擎会自动找卡，找到就发真请求：\n"
                "自动识别顺序: <项目>/.treefarm.json → ~/.treefarm.json → 环境变量\n"
                "（支持 TREEFARM_ / OPENAI / DASHSCOPE / DEEPSEEK / MOONSHOT / ARK）\n"
                "示例: echo '{\"api_key\":\"sk-xxx\",\"base_url\":\"https://api.deepseek.com/v1\","
                "\"model\":\"deepseek-chat\"}' > .treefarm.json\n\n"
                f"请求模型: {llm.model or '未指定'}\n请求内容:\n{prompt}")

    # ===== 简报 =====
    def brief(self, compact: bool = False) -> str:
        s = self.scanned
        b = self.bank.stats()
        lines = ["=" * 52, "🌳🌳 树场简报（AI 请先读这个，别全读项目）🌳🌳", "=" * 52]
        lines.append(f"会话: 第 {self.session.data['round']} 轮 | 本轮已报小鸟 "
                     f"{self.session.data['birds_this_round']} 只 | 收敛阈值 ≤{CONVERGE_LIMIT} 只")
        lines.append(f"基因库: {b['genes']} 条（强 {b['strong']} / 弱 {b['weak']}"
                     f" / 函数级+继承 {b['deep']}）| schema v{SCHEMA_VERSION} | "
                     f"小树: {'激活 ✅' if self.small_tree.active(len(s['tree'])) else '未激活'}")
        lines.append("")
        lines.append("【大树清单 · 每个文件的相关基因（小树视角）】")
        for f in s["tree"]:
            lines.append(self._tree_line(f, compact))
        lines.append("")
        lines.append("【杂草索引 · 不读全文，命中才展开】")
        lines.append(self.weed_index.report())
        lines.append("")
        lines.append(self.trash.report())
        lines.append("")
        lines.append("【下一步】")
        lines.append("  1. 挑重点文件展开阅读 → 发现可疑耦合就报小鸟")
        lines.append("  2. 强耦合: --bird | 弱耦合: --bird-weak | 自动: --analyze <文件>")
        lines.append("  3. 找相关代码: --search <关键词> | 小树视角: --genes <文件>")
        lines.append("  4. 每轮结束 --converge；垃圾箱 --fix 必须清空")
        return "\n".join(lines)

    def brief_data(self) -> Dict[str, Any]:
        """结构化简报（v3.2 新增，--json 用）：AI/其他工具可直接消费的机器可读数据"""
        s = self.scanned
        b = self.bank.stats()
        trees = []
        for f in s["tree"]:
            entry: Dict[str, Any] = {"path": os.path.relpath(f, self.root)}
            try:
                entry["size"] = os.path.getsize(f)
            except OSError:
                entry["size"] = 0
            if f.endswith(".py"):
                out, inn = self.small_tree.related(f)
                entry["out"] = [g["target"] for g in out]
                entry["inn"] = [os.path.relpath(g["source"], self.root) for g in inn]
            else:
                entry["symbols"] = extract_symbols(f)[:BRIEF_MAX_SYMBOLS]
            trees.append(entry)
        return {
            "tool": "tree_farm",
            "version": VERSION,
            "round": self.session.data["round"],
            "birds_this_round": self.session.data["birds_this_round"],
            "converged": self.session.converged(),
            "genes": b,
            "schema": SCHEMA_VERSION,
            "small_tree_active": self.small_tree.active(len(s["tree"])),
            "trees": trees,
            "weeds": [{"path": e["path"], "size": e["size"], "summary": e["summary"]}
                      for e in self.weed_index.entries],
            "trash": self.trash.trash(),
        }

    def _tree_line(self, f: str, compact: bool) -> str:
        rel = os.path.relpath(f, self.root)
        try:
            size = os.path.getsize(f)
        except OSError:
            size = 0
        if not f.endswith(".py"):
            syms = extract_symbols(f)[:BRIEF_MAX_SYMBOLS] or []
            return f"  {rel} [{size}B]  (符号: {syms or '无'})"
        out, inn = self.small_tree.related(f)
        if compact:
            out_n = [g['target'] for g in out]
            inn_n = [os.path.relpath(g['source'], self.root) for g in inn]
            out_s = ", ".join(out_n[:3]) + ("..." if len(out_n) > 3 else "")
            inn_s = ", ".join(inn_n[:3]) + ("..." if len(inn_n) > 3 else "")
            return (f"  {rel} [{size}B]"
                    f"\n      → 引用 {len(out)}: {out_s or '(无)'}"
                    f"\n      ← 被引用 {len(inn)}: {inn_s or '(无)'}")
        lines = [f"  {rel} [{size}B]"]
        lines.append(f"      → 引用: {[g['target'] for g in out] or '(无)'}")
        lines.append(f"      ← 被引用: {[os.path.relpath(g['source'], self.root) for g in inn] or '(无)'}")
        return "\n".join(lines)

    # ===== 代码分析（v3.1：死代码 / 循环依赖 / 影响分析 / 复杂度 / 架构分层） =====
    def dead_code(self) -> str:
        """死代码检测：找出从未被调用的函数和类（v4.0改进：降低误报率）"""
        result = detect_dead_code(self.scanned["tree"])
        lines = ["=" * 52, "💀 死代码检测报告（v4.0改进）", "=" * 52]
        lines.append(f"总函数数: {result['total_functions']} | 总类数: {result['total_classes']}")
        lines.append(f"高置信度死代码比例: {result['dead_ratio'] * 100:.1f}%")
        if 'exported_count' in result:
            lines.append(f"已排除导出符号: {result['exported_count']} | 已排除装饰器注册: {result['registered_count']}")
        lines.append("")
        if result["dead_functions"]:
            # 按置信度分组
            high = [d for d in result["dead_functions"] if d["confidence"] == "high"]
            med = [d for d in result["dead_functions"] if d["confidence"] == "medium"]
            low = [d for d in result["dead_functions"] if d["confidence"] == "low"]
            if high:
                lines.append(f"【高置信度死函数 · {len(high)} 个】")
                for d in high[:20]:
                    lines.append(f"  {os.path.relpath(d['file'], self.root)}:{d['line']} - {d['name']}()")
                if len(high) > 20:
                    lines.append(f"  ... 还有 {len(high) - 20} 个")
                lines.append("")
            if med:
                lines.append(f"【中置信度（公共API，可能被外部调用）· {len(med)} 个】")
                for d in med[:10]:
                    lines.append(f"  {os.path.relpath(d['file'], self.root)}:{d['line']} - {d['name']}()")
                if len(med) > 10:
                    lines.append(f"  ... 还有 {len(med) - 10} 个")
                lines.append("")
            if low:
                lines.append(f"【低置信度（测试文件/框架调用）· {len(low)} 个】")
                for d in low[:10]:
                    lines.append(f"  {os.path.relpath(d['file'], self.root)}:{d['line']} - {d['name']}()")
                if len(low) > 10:
                    lines.append(f"  ... 还有 {len(low) - 10} 个")
                lines.append("")
        if result["dead_classes"]:
            high_c = [d for d in result["dead_classes"] if d["confidence"] == "high"]
            med_c = [d for d in result["dead_classes"] if d["confidence"] == "medium"]
            low_c = [d for d in result["dead_classes"] if d["confidence"] == "low"]
            if high_c:
                lines.append(f"【高置信度死类 · {len(high_c)} 个】")
                for d in high_c[:10]:
                    lines.append(f"  {os.path.relpath(d['file'], self.root)}:{d['line']} - class {d['name']}")
                lines.append("")
            if med_c:
                lines.append(f"【中置信度（公共类，可能被外部实例化）· {len(med_c)} 个】")
                for d in med_c[:10]:
                    lines.append(f"  {os.path.relpath(d['file'], self.root)}:{d['line']} - class {d['name']}")
                lines.append("")
        if not result["dead_functions"] and not result["dead_classes"]:
            lines.append("✅ 没有发现死代码！")
        lines.append("")
        lines.append("💡 v4.0改进：已排除__all__导出、装饰器注册（@app.route等）、测试文件、公共API")
        lines.append("⚠️ 高置信度项建议人工确认后删除，中低置信度项可能被动态调用")
        return "\n".join(lines)

    def smells(self, long_func_lines: int = 50, max_params: int = 5,
               max_nesting: int = 4, big_class_lines: int = 300) -> str:
        """代码异味检测（v3.7 新增，--smells）：
        长函数 / 长参数列表 / 嵌套过深 / 重复条件分支 / 魔法数字 / 大类。
        Python 走 AST 精确分析；JS/TS/Java/Go/Rust 走轻量启发式。"""
        result = detect_code_smells(self.scanned["tree"], root=self.root,
                                    long_func_lines=long_func_lines,
                                    max_params=max_params, max_nesting=max_nesting,
                                    big_class_lines=big_class_lines)
        smells = result["smells"]
        counts = result["counts"]
        lines = ["=" * 52, "🧪 代码异味检测报告（v3.7）", "=" * 52]
        n_high = sum(1 for s in smells if s["severity"] == "high")
        n_med = sum(1 for s in smells if s["severity"] == "medium")
        n_low = sum(1 for s in smells if s["severity"] == "low")
        lines.append(f"共发现 {result['total']} 处异味（高 {n_high} / 中 {n_med} / 低 {n_low}）")
        lines.append("")
        if not smells:
            lines.append("✅ 没有发现明显的代码异味！")
        else:
            type_names = {
                "long_function": "长函数", "long_parameter_list": "长参数列表",
                "deep_nesting": "嵌套过深", "duplicate_condition": "重复条件分支",
                "magic_number": "魔法数字", "god_class": "大类（上帝类）",
            }
            for sev in ("high", "medium", "low"):
                group = [s for s in smells if s["severity"] == sev]
                if not group:
                    continue
                sev_names = {"high": "🔴 高严重度", "medium": "🟡 中严重度", "low": "🟢 低严重度"}
                lines.append(f"【{sev_names[sev]} · {len(group)}】")
                for s in group[:30]:
                    name_part = f" - {s['name']}()" if s["name"] else ""
                    lines.append(f"  [{type_names.get(s['type'], s['type'])}] "
                                 f"{s['file']}:{s['line']}{name_part} | {s['detail']}")
                    lines.append(f"      💡 {s['suggestion']}")
                if len(group) > 30:
                    lines.append(f"  ... 还有 {len(group) - 30} 个")
                lines.append("")
            lines.append("【异味统计】")
            lines.append("  " + " | ".join(f"{type_names.get(k, k)} {v}" for k, v in counts.items()))
            lines.append("")
            lines.append("⚠️ 启发式检测（非 AST 语言可能误报），需人工确认后再重构")
        return "\n".join(lines)

    def security(self) -> str:
        """安全漏洞检测（v4.0 新增，--security）"""
        from .analysis import detect_security_issues
        result = detect_security_issues(self.scanned["tree"], root=self.root)
        issues = self._mark_spec_issues(result["issues"])
        sev = result["severity"]
        lines = [self._spec_header().rstrip("\n")]
        lines.append("=" * 56)
        lines.append("🔒 安全漏洞检测报告（v4.0）")
        lines.append("=" * 56)
        lines.append(f"风险评分: {result['risk_score']}/100 | 等级: {result['grade']} {result['emoji']}")
        lines.append(f"共发现 {result['total']} 个安全问题"
                     f"（严重 {sev['critical']} / 高危 {sev['high']} / 中危 {sev['medium']} / 低危 {sev['low']}）")
        if result.get("test_issues"):
            lines.append(f"🧪 其中 {result['test_issues']} 个在测试代码里（不参与评分，测试用例本身常含故意构造的脏数据）")
        lines.append("")
        if not issues:
            lines.append("✅ 未发现明显安全漏洞！")
        else:
            sev_order = [("critical", "🔴 严重"), ("high", "🟠 高危"),
                         ("medium", "🟡 中危"), ("low", "🟢 低危")]
            for sev_key, sev_name in sev_order:
                group = [i for i in issues if i["severity"] == sev_key]
                if not group:
                    continue
                lines.append(f"【{sev_name} · {len(group)}】")
                for iss in group[:25]:
                    lines.append(f"  [{iss['type']}] {iss['file']}:{iss['line']}")
                    lines.append(f"      {iss['desc']}")
                    lines.append(f"      代码: {iss['code']}")
                if len(group) > 25:
                    lines.append(f"  ... 还有 {len(group) - 25} 个")
                lines.append("")
            lines.append("【修复建议】")
            for s in result["suggestions"]:
                lines.append(f"  • {s}")
        return "\n".join(lines)

    def performance(self) -> str:
        """性能问题检测（v4.0 新增，--performance）"""
        from .analysis import detect_performance_issues
        result = detect_performance_issues(self.scanned["tree"], root=self.root)
        issues = self._mark_spec_issues(result["issues"])
        sev = result["severity"]
        lines = [self._spec_header().rstrip("\n")]
        lines.append("=" * 56)
        lines.append("⚡ 性能问题检测报告（v4.0）")
        lines.append("=" * 56)
        lines.append(f"性能评分: {result['perf_score']}/100 | 等级: {result['grade']} {result['emoji']}")
        lines.append(f"共发现 {result['total']} 个性能问题"
                     f"（高 {sev['high']} / 中 {sev['medium']} / 低 {sev['low']}）")
        if result.get("test_issues"):
            lines.append(f"🧪 其中 {result['test_issues']} 个在测试代码里（不参与评分）")
        lines.append("")
        if not issues:
            lines.append("✅ 未发现明显性能问题！")
        else:
            sev_order = [("high", "🔴 高性能影响"), ("medium", "🟡 中性能影响"), ("low", "🟢 低性能影响")]
            for sev_key, sev_name in sev_order:
                group = [i for i in issues if i["severity"] == sev_key]
                if not group:
                    continue
                lines.append(f"【{sev_name} · {len(group)}】")
                for iss in group[:25]:
                    lines.append(f"  [{iss['type']}] {iss['file']}:{iss['line']}")
                    lines.append(f"      {iss['desc']}")
                    lines.append(f"      代码: {iss['code']}")
                if len(group) > 25:
                    lines.append(f"  ... 还有 {len(group) - 25} 个")
                lines.append("")
            lines.append("【优化建议】")
            for s in result["suggestions"]:
                lines.append(f"  • {s}")
        return "\n".join(lines)

    def logic(self) -> str:
        """逻辑错误检测（v4.0 新增，--logic）"""
        from .analysis import detect_logic_issues
        result = detect_logic_issues(self.scanned["tree"], root=self.root)
        issues = self._mark_spec_issues(result["issues"])
        sev = result["severity"]
        lines = [self._spec_header().rstrip("\n")]
        lines.append("=" * 56)
        lines.append("🐛 逻辑错误检测报告（v4.0）")
        lines.append("=" * 56)
        lines.append(f"逻辑评分: {result['logic_score']}/100 | 等级: {result['grade']} {result['emoji']}")
        lines.append(f"共发现 {result['total']} 个逻辑问题"
                     f"（高 {sev['high']} / 中 {sev['medium']} / 低 {sev['low']}）")
        if result.get("test_issues"):
            lines.append(f"🧪 其中 {result['test_issues']} 个在测试代码里（不参与评分）")
        lines.append("")
        if not issues:
            lines.append("✅ 未发现明显逻辑错误！")
        else:
            sev_order = [("high", "🔴 高风险"), ("medium", "🟡 中风险"), ("low", "🟢 低风险")]
            for sev_key, sev_name in sev_order:
                group = [i for i in issues if i["severity"] == sev_key]
                if not group:
                    continue
                lines.append(f"【{sev_name} · {len(group)}】")
                for iss in group[:25]:
                    lines.append(f"  [{iss['type']}] {iss['file']}:{iss['line']}")
                    lines.append(f"      {iss['desc']}")
                    lines.append(f"      代码: {iss['code']}")
                    if iss.get("fix"):
                        lines.append(f"      🔧 修复: {iss['fix']}")
                if len(group) > 25:
                    lines.append(f"  ... 还有 {len(group) - 25} 个")
                lines.append("")
            lines.append("【修复建议】")
            for s in result["suggestions"]:
                lines.append(f"  • {s}")
        return "\n".join(lines)

    def all_checks(self) -> str:
        """全量检测（v4.0 新增，--all-checks）"""
        lines = [self._spec_header().rstrip("\n")]
        lines.append("\n" + "=" * 60)
        lines.append(f"🌳 TreeFarm v{VERSION} 全量检测报告")
        lines.append("=" * 60)
        lines.append("\n" + self.dead_code())
        lines.append("\n" + self.circular_deps())
        lines.append("\n" + self.complexity())
        lines.append("\n" + self.architecture())
        lines.append("\n" + self.debt())
        lines.append("\n" + self.smells())
        lines.append("\n" + self.security())
        lines.append("\n" + self.performance())
        lines.append("\n" + self.logic())
        lines.append("\n" + "=" * 60 + "\n✅ 全量检测完成！\n" + "=" * 60)
        return "\n".join(lines)

    def static_ast(self) -> str:
        """轻量 AST 辅助分析（v4.4 新增，--static-ast）
        检测 AST 沙箱绕过 / 动态代码执行 / 装饰器注册函数 / 类型混淆，
        作为沙箱动态分析的静态补充。默认关闭，仅显式调用时运行。"""
        from .static_ast import LightASTAnalyzer
        analyzer = LightASTAnalyzer()
        all_issues = []
        decorated = set()
        py_files = [f for f in self.scanned["tree"] if f.endswith(".py")]
        for f in py_files:
            rel = os.path.relpath(f, self.root)
            try:
                issues = analyzer.analyze_file(f, rel)
            except Exception:
                continue
            all_issues.extend(issues)
            decorated |= analyzer.get_decorated_functions()

        sev_order = [("critical", "🔴 严重"), ("high", "🟠 高危"),
                     ("medium", "🟡 中危"), ("low", "🟢 低危")]
        lines = ["=" * 56, "🔬 轻量 AST 辅助分析报告（v4.4）", "=" * 56]
        lines.append(f"扫描 Python 文件 {len(py_files)} 个 | 共发现 {len(all_issues)} 个问题")
        lines.append("")
        if not all_issues:
            lines.append("✅ 未发现 AST 层面的可疑问题！")
        else:
            for sev_key, sev_name in sev_order:
                group = [i for i in all_issues if i["severity"] == sev_key]
                if not group:
                    continue
                lines.append(f"【{sev_name} · {len(group)}】")
                for iss in group[:25]:
                    lines.append(f"  [{iss['type']}] {iss['file']}:{iss['line']}")
                    lines.append(f"      {iss['desc']}")
                    lines.append(f"      代码: {iss['code']}")
                if len(group) > 25:
                    lines.append(f"  ... 还有 {len(group) - 25} 个")
                lines.append("")
        lines.append(f"📌 装饰器注册函数 {len(decorated)} 个（供死代码检测减少误报）")
        return "\n".join(lines)

    def circular_deps(self) -> str:
        """循环依赖检测：模块间的循环依赖"""
        result = detect_circular_dependencies(self.bank, self.module_map)
        lines = ["=" * 52, "🔄 循环依赖检测报告", "=" * 52]
        lines.append(f"总模块数: {result['total_modules']}")
        lines.append("")
        if result["has_cycle"]:
            lines.append(f"❌ 发现 {len(result['cycles'])} 个循环依赖！")
            lines.append("")
            for i, cycle in enumerate(result["cycles"], 1):
                lines.append(f"【循环 {i}】")
                lines.append("  → ".join(cycle))
                lines.append("")
        else:
            lines.append("✅ 没有发现循环依赖！")
        return "\n".join(lines)

    def impact(self, target_file: str, target_symbol: Optional[str] = None, max_depth: int = 3) -> str:
        """影响分析：修改某个文件/函数会影响哪些地方"""
        result = impact_analysis(self.bank, self.module_map, target_file, target_symbol, max_depth)
        lines = ["=" * 52, "💥 影响分析报告", "=" * 52]
        rel = os.path.relpath(result["target"], self.root)
        lines.append(f"目标: {rel}" + (f" - {target_symbol}" if target_symbol else ""))
        lines.append(f"影响范围: {result['total_impacted']} 个 | 最大深度: {result['max_depth_reached']}")
        lines.append("")
        if result["impacted_files"]:
            lines.append(f"【受影响的文件 · {len(result['impacted_files'])} 个】")
            for f, depth in result["impacted_files"][:30]:
                lines.append(f"  [深度 {depth}] {os.path.relpath(f, self.root)}")
            if len(result["impacted_files"]) > 30:
                lines.append(f"  ... 还有 {len(result['impacted_files']) - 30} 个")
            lines.append("")
        if result["impacted_functions"]:
            lines.append(f"【受影响的函数 · {len(result['impacted_functions'])} 个】")
            for f, func, depth in result["impacted_functions"][:30]:
                lines.append(f"  [深度 {depth}] {os.path.relpath(f, self.root)} - {func}()")
            if len(result["impacted_functions"]) > 30:
                lines.append(f"  ... 还有 {len(result['impacted_functions']) - 30} 个")
            lines.append("")
        if result["total_impacted"] == 0:
            lines.append("✅ 没有发现受影响的代码（可能是叶子节点）")
        return "\n".join(lines)

    def complexity(self, target_file: Optional[str] = None) -> str:
        """代码复杂度：单个文件或整个项目（v3.5：支持 Python/JS/Java/Go）"""
        lines = ["=" * 52, "📊 代码复杂度分析报告", "=" * 52]
        lines.append("")
        if target_file:
            result = calculate_complexity_any(os.path.abspath(target_file))
            if not result:
                return f"✖ 无法分析文件: {target_file}"
            lines.append(f"文件: {os.path.relpath(result['file'], self.root)}")
            lines.append(f"总行数: {result['total_lines']} | 函数数: {result['total_functions']}")
            lines.append(f"平均复杂度: {result['avg_complexity']} | 最高复杂度: {result['max_complexity']}")
            lines.append("")
            if result["functions"]:
                lines.append("【函数复杂度排行】")
                for func, comp, lines_count, lineno in result["functions"][:20]:
                    lines.append(f"  {func}() - 复杂度 {comp} | {lines_count} 行 | 第 {lineno} 行")
                if len(result["functions"]) > 20:
                    lines.append(f"  ... 还有 {len(result['functions']) - 20} 个")
        else:
            all_results = [r for f in self.scanned["tree"] if (r := calculate_complexity_any(f))]
            if not all_results:
                return "✖ 没有可分析的文件"
            total_functions = sum(r["total_functions"] for r in all_results)
            total_lines = sum(r["total_lines"] for r in all_results)
            all_funcs = [(r["file"], name, comp, cnt, lineno)
                         for r in all_results
                         for name, comp, cnt, lineno in r["functions"]]
            avg_comp = sum(f[2] for f in all_funcs) / len(all_funcs) if all_funcs else 0
            max_comp = max((f[2] for f in all_funcs), default=0)
            lines.append("项目总览:")
            lines.append(f"  文件数: {len(all_results)} | 总函数数: {total_functions}")
            lines.append(f"  总行数: {total_lines}")
            lines.append(f"  平均复杂度: {avg_comp:.2f} | 最高复杂度: {max_comp}")
            lines.append("")
            all_funcs.sort(key=lambda x: -x[2])
            lines.append("【复杂度最高的 Top 10 函数】")
            for f, func, comp, cnt, lineno in all_funcs[:10]:
                lines.append(f"  {os.path.relpath(f, self.root)}:{lineno} - {func}() | 复杂度 {comp} | {cnt} 行")
        lines.append("")
        lines.append("复杂度参考: 1-10 简单 | 11-20 中等 | 21-50 复杂 | 50+ 非常复杂")
        return "\n".join(lines)

    def architecture(self) -> str:
        """架构分层识别：表现层/业务层/数据层/工具层"""
        result = detect_architecture_layers(self.scanned["tree"], self.bank, self.module_map,
                                            root=self.root)
        lines = ["=" * 52, "🏗️  架构分层识别报告", "=" * 52]
        lines.append(f"分层数量: {result['layer_count']} | "
                     f"分层清晰: {'是 ✅' if result['has_clear_layers'] else '否 ❌'}")
        lines.append("")
        layer_names = {
            "presentation": "🎨 表现层（UI/Controller/API）",
            "business": "💼 业务层（Service/Logic）",
            "data": "💾 数据层（Model/Repository/DAO）",
            "util": "🔧 工具层（Utils/Helpers）",
            "unknown": "❓ 未分类",
        }
        for key, name in layer_names.items():
            files = result["layers"][key]
            if files:
                lines.append(f"【{name} · {len(files)} 个文件】")
                for f in files[:15]:
                    lines.append(f"  {os.path.relpath(f, self.root)}")
                if len(files) > 15:
                    lines.append(f"  ... 还有 {len(files) - 15} 个")
                lines.append("")
        return "\n".join(lines)

    # ===== 技术债务评估（v3.5 四维打分，v3.7 计算迁入 analysis.calculate_debt） =====
    def debt(self) -> str:
        """技术债务评估（v3.5 新增）：综合死代码比例、圈复杂度超标、重复代码、
        循环依赖四个维度打分（0~100，越低越好），并给优化建议。"""
        d = calculate_debt(self.scanned["tree"], self.bank, self.module_map,
                           root=self.root)
        lines = ["=" * 52, "📉 技术债务评估报告（v3.5）", "=" * 52]
        lines.append("")
        lines.append(f"【综合技术债务分】 {d['total']}/100  {d['emoji']} 等级 {d['grade']}")
        for dim in d["dimensions"]:
            lines.append(f"   {dim['label']}")
        lines.append("")
        lines.append("【优化建议】")
        for s in d["suggestions"]:
            lines.append(f"  • {s}")
        lines.append("")
        lines.append("评分说明: 死代码≤20% / 高复杂度函数 0 个 / 无重复 / 无环 = 0 分（满分健康）")
        return "\n".join(lines)

    # ===== 调用图 / 重复代码（v3.3 分析深度提升；v3.7 计算迁入 analysis） =====
    def call_graph_data(self, target_file: Optional[str] = None) -> Dict[str, Any]:
        """调用图结构化数据（v3.5：供 --json 输出，方便其他工具集成）：
        返回 {"nodes": [{"id","label"}], "edges": [{"from","to","relation","symbol"}]}"""
        return generate_call_graph_data(self.bank, self.module_map, self.root,
                                        self.small_tree, target_file)

    def call_graph(self, target_file: Optional[str] = None) -> str:
        """调用图（v3.3，Mermaid flowchart 输出）：
        无参数 = 全项目文件级调用图；带文件 = 该文件的符号级调用图。
        复制输出到 mermaid.live / 支持 Mermaid 的编辑器即可渲染。"""
        return generate_call_graph(self.bank, self.module_map, self.root,
                                   self.small_tree, target_file)

    def duplicates_func(self, threshold: float = 0.6, top: int = 10) -> str:
        """函数级重复代码检测（v3.5，比文件级更精确）：
        提取每个函数体做 n-gram Jaccard，大小差 >30% 跳过，输出相似度 Top N。
        分桶优化：按函数体大小分桶（size//64），只比较同桶 + 相邻桶。"""
        pairs = detect_duplicate_func_pairs(self.scanned["tree"], threshold)
        funcs = collect_function_grams(self.scanned["tree"])
        lines = ["=" * 52, "🔁 函数级重复代码检测报告（v3.5）", "=" * 52]
        lines.append(f"提取函数 {len(funcs)} 个 | 相似度阈值 ≥{threshold} | 大小差 >30% 自动跳过")
        lines.append("")
        if not pairs:
            lines.append("✅ 没有发现明显重复的函数！")
        else:
            lines.append(f"【疑似重复函数 · Top {min(top, len(pairs))}】")
            for fa, fname, fb, bname, score in pairs[:top]:
                ra = os.path.relpath(fa, self.root)
                rb = os.path.relpath(fb, self.root)
                lines.append(f"  {score:.0%}  {ra}:{fname}()  ↔  {rb}:{bname}()")
            lines.append("")
            lines.append("提示：确认后可用小树视角（--genes）看两边的引用关系再决定是否合并")
        return "\n".join(lines)

    def duplicates(self, threshold: float = 0.6, top: int = 10) -> str:
        """重复代码检测（v3.3，内容 n-gram Jaccard 相似度，零依赖）：
        大小差 >30% 的文件直接跳过（不可能重复）；输出相似度 Top N。"""
        pairs, files = detect_duplicates(self.scanned["tree"], threshold)
        lines = ["=" * 52, "🔁 重复代码检测报告", "=" * 52]
        lines.append(f"代码文件 {files} 个 | 相似度阈值 ≥{threshold} | 大小差 >30% 自动跳过")
        lines.append("")
        if not pairs:
            lines.append("✅ 没有发现明显重复的代码！")
        else:
            lines.append(f"【疑似重复 · Top {min(top, len(pairs))}】")
            for a, b, score in pairs[:top]:
                ra = os.path.relpath(a, self.root)
                rb = os.path.relpath(b, self.root)
                lines.append(f"  {score:.0%}  {ra}  ↔  {rb}")
            lines.append("")
            lines.append("提示：确认后可用小树视角（--genes）看两边的引用关系再决定是否合并")
        return "\n".join(lines)

    # ===== 修复协议 =====
    def fix_protocol(self, target_file: str) -> str:
        if target_file not in self.trash.trash():
            return f"{target_file} 不在垃圾箱里，无需修复。"
        reasons = self.trash._reasons(target_file)
        lines = [f"🔧 修复协议（{target_file}）",
                 f"  失败记录: {', '.join(reasons) or '(无)'}",
                 "  修复步骤: 1. 新开一场思维树深度阅读 2. 定位为什么 AI 总在这出错",
                 "            3. 修正代码 4. 重新整体排查",
                 "  ⚠️ 需用户同意后才能执行修复（默认逐处确认）"]
        return "\n".join(lines)

    # ===== v4.7 grader 化：项目功能画像 / 综合评分 / 趋势 =====

    def set_spec(self, description: str) -> Dict[str, Any]:
        """注入「被检项目功能描述」（--spec "..."），构建功能画像。"""
        from .spec import build_spec
        self._spec_ctx = build_spec(description)
        return self._spec_ctx

    def spec_read(self) -> Dict[str, Any]:
        """AI 自读项目功能画像（--spec-read）：读 README/docs/入口 docstring 推断。"""
        from .spec import autodetect_spec
        self._spec_ctx = autodetect_spec(self.scanned["tree"], root=self.root)
        return self._spec_ctx

    def spec(self) -> str:
        """显示当前功能画像（--spec / --spec-read / 未提供时给引导）。"""
        from .spec import format_spec
        ctx = self._spec_ctx
        if ctx is None:
            ctx = {"stack": [], "desc": "", "focused": []}
        lines = [format_spec(ctx)]
        if ctx.get("focused"):
            lines.append("")
            lines.append("💡 结合功能上下文，抓 bug / 修 bug 会更精准：")
            lines.append("   报告会把问题按「与哪个功能相关」标注，并优先展示与核心功能相关的问题")
        return "\n".join(lines)

    def _spec_header(self) -> str:
        """检测报告开头的功能画像段（有 spec 时显示，无则不打扰）。"""
        if not self._spec_ctx:
            return ""
        from .spec import format_spec
        return "\n" + format_spec(self._spec_ctx) + "\n"

    def _mark_spec_issues(self, issues: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """结合功能画像标记「与核心功能相关」的问题（v4.7 上下文感知）：
        命中 spec.focused 检测类型的问题加 🎯 前缀，抓 bug 时优先看这些。"""
        if not self._spec_ctx:
            return issues
        focused = set(self._spec_ctx.get("focused", []))
        if not focused:
            return issues
        out = []
        for iss in issues:
            if iss.get("type") in focused:
                iss = dict(iss)
                iss["desc"] = "🎯与核心功能相关 " + iss["desc"]
            out.append(iss)
        return out

    def grade(self, spec_text: Optional[str] = None) -> str:
        """Grader 综合评分（v4.7，--grade）：六维健康度 → 综合分 + 等级 + 改进方向。
        支持 --spec 上下文（功能画像参与判断）与历史趋势（对比上次评分）。"""
        from .analysis import (calculate_debt, detect_code_smells,
                               detect_architecture_layers, detect_logic_issues,
                               detect_performance_issues, detect_security_issues)
        from .spec import format_grade, grade_project, load_grade, save_grade

        if spec_text:
            self.set_spec(spec_text)

        tree = self.scanned["tree"]
        root = self.root

        # 六维健康度（各检测器 score 越高越差 → 健康度 = 100 - score）
        sec = detect_security_issues(tree, root=root)
        logic = detect_logic_issues(tree, root=root)
        perf = detect_performance_issues(tree, root=root)
        debt = calculate_debt(tree, self.bank, self.module_map, root=root)
        smells = detect_code_smells(tree, root=root)
        arch = detect_architecture_layers(tree, self.bank, self.module_map, root=root)

        # 异味/结构 → 健康度（异味越少越好；分层越清晰越好）
        smell_health = max(0.0, 100 - smells["total"] * 4.0)
        structure_health = 85.0
        if arch.get("has_clear_layers"):
            structure_health = 90.0 + arch.get("layer_count", 3) * 2
        structure_health = min(100.0, structure_health)

        dims = {
            "security": 100.0 - sec["risk_score"],
            "logic": 100.0 - logic["logic_score"],
            "performance": 100.0 - perf["perf_score"],
            "structure": structure_health,
            "quality": smell_health,
            "debt": 100.0 - debt["total"],
        }
        g = grade_project(dims)
        prev = load_grade(self.bank)
        save_grade(self.bank, g)

        lines = [self._spec_header().rstrip("\n")]
        lines.append(format_grade(g, prev))
        # 结合 spec 给出「功能相关」重点提醒
        if self._spec_ctx and self._spec_ctx.get("focused"):
            fcs = self._spec_ctx["focused"][:10]
            lines.append("")
            lines.append(f"🎯 与项目功能最相关的检查项: {', '.join(fcs)}")
            lines.append("   （跑单项检测时优先看这些类型，命中即优先修）")
        return "\n".join(lines)

    def grade_diff(self) -> str:
        """查看综合评分趋势（--grade-diff）：上次 vs 当前（需先跑过一次 --grade）。"""
        from .spec import format_grade, grade_project, load_grade
        from .analysis import (calculate_debt, detect_code_smells,
                               detect_architecture_layers, detect_logic_issues,
                               detect_performance_issues, detect_security_issues)
        prev = load_grade(self.bank)
        if prev is None:
            return "📊 还没有历史评分。先跑一次 --grade 建立基准，之后 --grade-diff 就能看趋势。"
        tree = self.scanned["tree"]
        sec = detect_security_issues(tree, root=self.root)
        logic = detect_logic_issues(tree, root=self.root)
        perf = detect_performance_issues(tree, root=self.root)
        debt = calculate_debt(tree, self.bank, self.module_map, root=self.root)
        smells = detect_code_smells(tree, root=self.root)
        arch = detect_architecture_layers(tree, self.bank, self.module_map, root=self.root)
        total_lines = sum(1 for f in tree if f.endswith(".py")
                          for _ in open(f, encoding="utf-8", errors="ignore"))
        klines = max(total_lines / 1000.0, 0.1)
        smell_w = 0.0
        for s in smells["smells"]:
            smell_w += {"high": 8.0, "medium": 3.0, "low": 1.0}.get(s["severity"], 1.0)
        smell_health = max(0.0, min(100.0, 100 - smell_w / klines * 4.0))
        structure_health = 90.0 + arch.get("layer_count", 3) * 2 if arch.get("has_clear_layers") else 85.0
        structure_health = min(100.0, structure_health)
        dims = {
            "security": 100.0 - sec["risk_score"],
            "logic": 100.0 - logic["logic_score"],
            "performance": 100.0 - perf["perf_score"],
            "structure": structure_health,
            "quality": smell_health,
            "debt": 100.0 - debt["total"],
        }
        g = grade_project(dims)
        return format_grade(g, prev)
