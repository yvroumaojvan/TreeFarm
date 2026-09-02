# -*- coding: utf-8 -*-
"""树场机制 —— v3.6（多模块版）
对应设计文档：《树场机制的核心.txt》

v3.6 变更（深度分析报告 P0 第 1 项）：
  · 单文件 3227 行 → 拆分为多模块（treefarm/ 包）：
      common.py   常量 / 工具 / 文件缓存 / 语义搜索 / 指纹增量
      parser.py   多语言基因提取（Python AST + JS/Java/Go 轻量解析器）
      storage.py  GeneBank / TrashBin / Session / WeedIndex / SmallTree（SQLite）
      config.py   配置解析（.treefarm.toml/.json）+ LLMClient
      analysis.py 死代码 / 循环依赖 / 影响分析 / 复杂度 / 架构分层
      core.py     TreeFarm 主类
      cli.py      命令分发 / main
  · tree_farm.py 保留为入口（聚合导出全部公共符号），旧用法与 import 完全兼容
  · 影响分析 / 架构分层确认跨语言可用（补充 4 语言测试）

用法：
  python3 tree_farm.py <项目>                      # 树场体检
  python3 tree_farm.py <项目> --brief [--compact]  # AI 工作简报（--compact 精简版）
  python3 tree_farm.py <项目> --genes <文件>       # 小树视角：看某文件的相关基因
  python3 tree_farm.py <项目> --bird <源> <目标>   # 报小鸟（强耦合）
  python3 tree_farm.py <项目> --bird-weak <源> <目标>   # 报小鸟（弱耦合）
  python3 tree_farm.py <项目> --analyze <文件>     # LLM 自动找小鸟（需配 key，无 key dry-run）
  python3 tree_farm.py <项目> --search <关键词>    # 语义搜索（预构建索引）
  python3 tree_farm.py <项目> --symbols <文件>     # 看文件里的符号
  python3 tree_farm.py <项目> --trash              # 垃圾箱
  python3 tree_farm.py <项目> --next-round         # 新一轮
  python3 tree_farm.py <项目> --converge           # 收敛检查
  python3 tree_farm.py <项目> --fix <文件>         # 修复协议
  python3 tree_farm.py <项目> --llm on|off|status  # LLM 开关
  python3 tree_farm.py <项目> --db                 # 存储状态

  # 代码分析（v3.1 新增，移植自融合版 v2.2）
  python3 tree_farm.py <项目> --dead-code          # 死代码检测
  python3 tree_farm.py <项目> --circular-deps      # 循环依赖检测
  python3 tree_farm.py <项目> --impact <文件> [符号] [深度]  # 影响分析
  python3 tree_farm.py <项目> --complexity [文件]  # 代码复杂度
  python3 tree_farm.py <项目> --architecture       # 架构分层识别
  # 分析深度（v3.3 新增）
  python3 tree_farm.py <项目> --graph [文件]       # 调用图（Mermaid 输出）
  python3 tree_farm.py <项目> --duplicates [阈值]  # 重复代码检测（n-gram 相似度）
  # v3.5：技术债务 / 函数级查重；v3.7：代码异味 / 交互式 REPL
  python3 tree_farm.py <项目> --debt               # 技术债务评估（四维打分 A~D）
  python3 tree_farm.py <项目> --duplicates-func [阈值]  # 函数级查重
  python3 tree_farm.py <项目> --smells             # 代码异味检测（长函数/长参数/嵌套/魔法数字/大类）
  python3 tree_farm.py <项目> --repl               # 交互式 REPL（Tab 补全/历史/彩色输出）
  # 中文命令（与上面等价）
  python3 tree_farm.py <项目> 分析 死代码
  python3 tree_farm.py <项目> 分析 循环依赖
  python3 tree_farm.py <项目> 分析 复杂度
  python3 tree_farm.py <项目> 分析 架构
  python3 tree_farm.py <项目> 分析 调用图 [文件]
  python3 tree_farm.py <项目> 分析 重复代码
  python3 tree_farm.py <项目> 分析 影响 <文件>
  任意命令加 --debug 看日志 | --quiet 关进度条 | --json 机器可读输出

LLM 配置（环境变量或配置文件）：
  TREEFARM_API_KEY=... TREEFARM_API_URL=... TREEFARM_MODEL=...
  或项目目录 .treefarm.json / .treefarm.toml（自动识别：项目 → 用户主目录 → 环境变量）
"""

import json
import os
import sys
import time
from typing import List

from .common import SCHEMA_VERSION, VERSION, semantic_search
from .config import LLMClient
from .core import TreeFarm

# 中文菜单速查（小白友好：python3 tree_farm.py 菜单）
MENU_TEXT = """🌳 树场 TreeFarm v""" + VERSION + """ —— 中文速查菜单

【最简单用法】把下面的"项目路径"换成你的代码文件夹，直接跑：
  python3 tree_farm.py 菜单                     ← 本菜单
  python3 tree_farm.py 项目路径                  ← 体检：扫一遍你的代码
  python3 tree_farm.py 项目路径 简报             ← AI 工作简报（先看这个！）
  python3 tree_farm.py 项目路径 搜索 关键词       ← 找代码：按关键词搜
  python3 tree_farm.py 项目路径 全量检测          ← 一键全面体检（最推荐！）

【想偷懒？一键全面体检】
  python3 tree_farm.py 项目路径 全量检测
  自动查：安全漏洞 + 性能问题 + 逻辑错误 + 死代码 + 循环依赖 + 复杂度 + 架构 + 技术债务

【单项体检（按需选）】
  安全：   python3 tree_farm.py 项目路径 安全
  性能：   python3 tree_farm.py 项目路径 性能
  逻辑：   python3 tree_farm.py 项目路径 逻辑
  死代码： python3 tree_farm.py 项目路径 死代码
  复杂度： python3 tree_farm.py 项目路径 复杂度
  架构：   python3 tree_farm.py 项目路径 架构
  技术债务：python3 tree_farm.py 项目路径 技术债务
  重复代码：python3 tree_farm.py 项目路径 查重
  循环依赖：python3 tree_farm.py 项目路径 循环依赖

【改代码前看影响】
  python3 tree_farm.py 项目路径 影响 文件名.py   ← 改了它会影响谁？
  python3 tree_farm.py 项目路径 基因 文件名.py    ← 它和谁有关系？

【沙箱：安全试跑代码】不用装环境，在安全屋里跑
  python3 tree_farm.py 沙箱 状态                 ← 看沙箱环境
  python3 tree_farm.py 沙箱 运行 "print('你好')"  ← 试跑一段代码
  python3 tree_farm.py 沙箱 测试 测试文件.py      ← 跑测试文件

【还有】（进阶，先记住上面这些就够了）
  调用图 / 函数级查重 / 代码异味 / 语法树分析 / 静态AST / 收敛 / 垃圾箱 / 小鸟 / 修复
  交互模式：python3 tree_farm.py 项目路径 交互    ← 进入问答式操作

💡 记不住？随时跑：python3 tree_farm.py 菜单
"""
from .parser import extract_symbols
from .sandbox import SandboxRunner, SandboxConfig


def setup_logging(debug: bool) -> None:
    import logging
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.WARNING,
        stream=sys.stderr,
        format="[treefarm] %(levelname)s %(message)s")




def _dispatch(farm: TreeFarm, root: str, args: List[str]) -> None:
    """命令分发（从 main 抽出，便于 main 用 try/finally 统一释放数据库连接）"""
    rest_all = args[1:]
    compact = "--compact" in rest_all
    quiet = "--quiet" in rest_all or "--no-progress" in rest_all
    as_json = "--json" in rest_all
    rest = [a for a in rest_all if a not in ("--compact", "--quiet", "--no-progress", "--json")]

    def _progress(i: int, total: int, f: str) -> None:
        # 进度刷到 stderr（不污染 stdout 的结果输出 / 管道 / JSON）
        sys.stderr.write(f"\r🌳 扫描 {i}/{total}: {os.path.relpath(f, root)}")
        sys.stderr.flush()

    t0 = time.time()
    is_new, stat = farm.plant(_progress if not quiet else None)
    if not quiet:
        sys.stderr.write(f"\r🌳 扫描完成: {len(farm.scanned['tree'])} 个代码文件, "
                         f"{time.time() - t0:.1f}s\n")


        sys.stderr.flush()

    if not rest:
        b = farm.bank.stats()
        if as_json:
            print(json.dumps({
                "tool": "tree_farm",
                "version": VERSION,
                "is_new": is_new,
                "added": stat["added"],
                "eaten": stat["eaten"],
                "renamed": stat["renamed"],
                "trees": len(farm.scanned["tree"]),
                "weeds": len(farm.scanned["weed"]),
                "genes": b["genes"],
                "schema": SCHEMA_VERSION,
            }, ensure_ascii=False, indent=2))
        else:
            print(f"建库={'是' if is_new else '否'} | 新增基因 {stat['added']['import'] + stat['added']['call']}"
                  f"（函数级+继承 {stat['added']['call']}）| 腐肉啃掉 {stat['eaten']} | 重命名迁移 {stat['renamed']}")
            print(f"大树 {len(farm.scanned['tree'])} | 杂草 {len(farm.scanned['weed'])} | "
                  f"基因库 {b['genes']} 条（schema v{SCHEMA_VERSION}）")
            print("想看细节: --brief  |  报小鸟: --bird  |  自动: --analyze  |  搜索: --search")
        return

    cmd = rest[0]
    if cmd in ("--brief", "简报", "工作简报", "体检报告", "体检"):
        if as_json:
            print(json.dumps(farm.brief_data(), ensure_ascii=False, indent=2))
        else:
            print(farm.brief(compact=compact))
    elif cmd in ("--genes", "基因", "小树") and len(rest) >= 2:
        f = os.path.abspath(rest[1])
        if not os.path.isfile(f):
            print(f"✖ 文件不存在: {f}")
            return
        out, inn = farm.small_tree.related(f)

        def _disp(target: str) -> str:
            if os.path.isabs(target) and os.path.isfile(target):
                return os.path.relpath(target, root)
            return target

        print(f"🌳 小树视角 · {os.path.relpath(f, root)}")
        print("  → 引用（它依赖谁，重构看这个）:")
        for g in out:
            print(f"      {g['relation']:<7} {_disp(g['target'])}  [{g['kind']}, 置信 {g['confidence']}]")
        print("  ← 被引用（谁依赖它，修 bug 看这个）:")
        for g in inn:
            print(f"      {g['relation']:<7} {os.path.relpath(g['source'], root)}  [{g['kind']}, 置信 {g['confidence']}]")
        if not out and not inn:
            print("  (无相关基因 —— 它是孤岛？)")
    elif cmd in ("--bird", "小鸟", "报小鸟") and len(rest) >= 3:
        print(farm.bird(rest[1], rest[2]))
    elif cmd in ("--bird-weak", "小鸟弱", "弱小鸟", "弱耦合") and len(rest) >= 3:
        print(farm.bird(rest[1], rest[2], is_weak=True))
    elif cmd in ("--analyze", "自动分析", "智能分析") and len(rest) >= 2:
        print(farm.analyze(os.path.abspath(rest[1]), LLMClient(root)))
    elif cmd in ("--llm", "LLM", "大模型") and len(rest) >= 2:
        llm = LLMClient(root)
        sub = rest[1]
        if sub == "off":
            farm.session.set_llm(False)
            print("📵 LLM 已关闭。说「开启 LLM」或 --llm on 恢复。")
        elif sub == "on":
            farm.session.set_llm(True)
            print("📞 LLM 已开启（默认开启）。")
        elif sub == "status":
            state = "✅ 开启" if farm.session.data.get("llm_enabled", True) else "📵 已关闭"
            print("📞 LLM 状态: " + state + "\n"
                  f"   卡: {llm.source}\n"
                  f"   模型: {llm.model or '未指定'}\n"
                  f"   接口: {llm.base_url or '未指定'}")
    elif cmd in ("--search", "搜索", "查找", "找代码") and len(rest) >= 2:
        hits = semantic_search(rest[1], farm.scanned["tree"], farm.symbol_index,
                               farm.content_index)
        if not hits:
            print(f"🔍 没找到与「{rest[1]}」相关的文件")
        else:
            print(f"🔍 「{rest[1]}」的相关文件（按相似度排序）：")
            for f, score, why in hits:
                print(f"  {score:.2f}  {os.path.relpath(f, root)}  ({why})")
    elif cmd in ("--symbols", "符号") and len(rest) >= 2:
        syms = extract_symbols(os.path.abspath(rest[1]))
        print(f"符号（函数/类）: {syms or '无'}")
    elif cmd in ("--trash", "垃圾箱", "垃圾桶"):
        print(farm.trash.report())
    elif cmd in ("--next-round", "下一轮", "新一轮"):
        farm.session.next_round()
        print(f"第 {farm.session.data['round']} 轮开始")
    elif cmd in ("--converge", "收敛", "收工检查"):
        print(f"本轮新增小鸟 {farm.session.data['birds_this_round']} 只 | "
              f"{'✅ 已收敛，排查结束' if farm.session.converged() else '⏳ 未收敛，继续排查'}")
        if not farm.trash.is_empty():
            print("⚠️ 但垃圾箱未清空 —— 任务仍未完成！")
    elif cmd in ("--fix", "修复", "修复协议") and len(rest) >= 2:
        print(farm.fix_protocol(os.path.abspath(rest[1])))
    elif cmd in ("--db", "存储", "数据库"):
        info = farm.bank.db_info()
        print(f"🗄 存储: {info['db']} ({info['size_kb']} KB, WAL 模式, 原子事务)")
        print(f"   基因按关系分布: {info['relations'] or '(空库)'}")
    # ===== 代码分析（v3.1，英文命令） =====
    elif cmd in ("--dead-code", "死代码", "死代码检测", "未使用"):
        print(farm.dead_code())
    elif cmd in ("--circular-deps", "循环依赖", "循环", "依赖环"):
        print(farm.circular_deps())
    elif cmd in ("--impact", "影响", "影响分析") and len(rest) >= 2:
        target = os.path.abspath(rest[1])
        symbol = rest[2] if len(rest) >= 3 else None
        try:
            depth = int(rest[3]) if len(rest) >= 4 else 3
        except ValueError:
            print(f"✖ 深度参数必须是数字: {rest[3]}")
            return
        print(farm.impact(target, symbol, depth))
    elif cmd in ("--complexity", "复杂度", "复杂"):
        target = os.path.abspath(rest[1]) if len(rest) >= 2 else None
        print(farm.complexity(target))
    elif cmd in ("--architecture", "架构", "架构分层", "分层"):
        print(farm.architecture())
    elif cmd == "--debt" or cmd in ("技术债务", "债务", "健康度"):
        print(farm.debt())
    elif cmd == "--smells" or cmd in ("代码异味", "异味", "检查异味", "坏味道"):
        print(farm.smells())
    # ===== v4.0 新增：安全/性能/逻辑检测 =====
    elif cmd == "--security" or cmd in ("安全", "安全检测", "漏洞检测", "安全扫描"):
        print(farm.security())
    elif cmd == "--performance" or cmd in ("性能", "性能检测", "性能分析", "性能优化"):
        print(farm.performance())
    elif cmd == "--logic" or cmd in ("逻辑", "逻辑检测", "逻辑错误", "bug检测"):
        print(farm.logic())
    elif cmd == "--all-checks" or cmd in ("全量检测", "全部检测", "全面检测", "一键检测"):
        print(farm.all_checks())
    elif cmd == "--static-ast" or cmd in ("静态AST", "AST分析", "ast分析", "语法树分析"):
        print(farm.static_ast())
    # ===== 分析深度（v3.3，Mermaid 调用图 / 重复代码检测） =====
    elif cmd == "--graph" or (cmd in ("调用图", "图谱") and len(rest) >= 1):
        target = os.path.abspath(rest[1]) if len(rest) >= 2 else None
        if as_json:
            print(json.dumps(farm.call_graph_data(target), ensure_ascii=False, indent=2))
        else:
            print(farm.call_graph(target))
    elif cmd == "--duplicates" or cmd in ("重复代码", "相似代码", "查重"):
        try:
            threshold = float(rest[1]) if len(rest) >= 2 else 0.6
        except ValueError:
            print(f"✖ 相似度阈值必须是数字（0~1）: {rest[1]}")
            return
        print(farm.duplicates(threshold=threshold))
    elif cmd == "--duplicates-func" or cmd in ("函数级查重", "函数查重", "重复函数"):
        try:
            threshold = float(rest[1]) if len(rest) >= 2 else 0.6
        except ValueError:
            print(f"✖ 相似度阈值必须是数字（0~1）: {rest[1]}")
            return
        print(farm.duplicates_func(threshold=threshold))
    # ===== 代码分析（v3.1，中文命令，移植自 v2.2） =====
    elif cmd in ("分析", "解析", "检查") and len(rest) >= 2:
        sub = rest[1]
        if sub in ("死代码", "死代码检测", "未使用", "没用的代码"):
            print(farm.dead_code())
        elif sub in ("循环依赖", "循环", "依赖环"):
            print(farm.circular_deps())
        elif sub in ("复杂度", "复杂", "代码质量"):
            target = os.path.abspath(rest[2]) if len(rest) >= 3 else None
            print(farm.complexity(target))
        elif sub in ("架构", "架构分层", "分层", "结构"):
            print(farm.architecture())
        elif sub in ("技术债务", "债务", "健康度"):
            print(farm.debt())
        elif sub in ("异味", "代码异味", "检查异味", "坏味道"):
            print(farm.smells())
        elif sub in ("调用图", "依赖图", "图谱"):
            target = os.path.abspath(rest[2]) if len(rest) >= 3 else None
            print(farm.call_graph(target))
        elif sub in ("重复代码", "相似代码", "查重", "重复"):
            print(farm.duplicates())
        elif sub in ("函数级查重", "函数查重", "重复函数"):
            print(farm.duplicates_func())
        elif sub in ("影响", "影响分析", "影响范围") and len(rest) >= 3:
            target = os.path.abspath(rest[2])
            symbol = rest[3] if len(rest) >= 4 else None
            try:
                depth = int(rest[4]) if len(rest) >= 5 else 3
            except ValueError:
                print(f"✖ 深度参数必须是数字: {rest[4]}")
                return
            print(farm.impact(target, symbol, depth))
        else:
            print(f"❌ 未知分析类型: {sub}\n💡 支持: 死代码 / 循环依赖 / 复杂度 / 架构 / 调用图 / 重复代码 / 影响 <文件>")
    elif cmd in ("死代码", "死代码检测", "未使用"):
        print(farm.dead_code())
    elif cmd in ("循环依赖", "循环", "依赖环"):
        print(farm.circular_deps())
    elif cmd in ("架构", "架构分层", "分层"):
        print(farm.architecture())
    # ===== 小沙箱（v3.8 新增，动态代码分析） =====
    elif cmd in ("--sandbox", "沙箱", "小沙箱", "动态分析"):
        _dispatch_sandbox(rest[1:])
    else:
        print(__doc__)


# ===== 小沙箱命令分发（v3.8 新增） =====
def _dispatch_sandbox(rest: List[str]) -> None:
    """沙箱命令分发：--sandbox on|off|status|run <code>|test <file>|lang"""
    if not rest:
        print("🔬 小沙箱命令：")
        print("  --sandbox on          开启沙箱")
        print("  --sandbox off         关闭沙箱")
        print("  --sandbox status      查看沙箱状态")
        print("  --sandbox lang        查看可用语言")
        print("  --sandbox run <code>  在沙箱中执行代码（默认 Python）")
        print("  --sandbox run <lang> <code>  指定语言执行")
        print("  --sandbox test <file> 在沙箱中运行测试文件")
        print("  --sandbox trace <code>  执行代码并采集调用轨迹")
        print("  --sandbox cov <code>    执行代码并采集覆盖率")
        return

    sub = rest[0]
    runner = SandboxRunner()

    if sub in ("on", "开启", "打开"):
        runner.enable()
        print("🔬 小沙箱已开启。AI 进行代码分析时可请求动态执行验证。")
    elif sub in ("off", "关闭", "关掉"):
        runner.disable()
        print("📵 小沙箱已关闭。")
    elif sub in ("status", "状态", "info"):
        print(runner.format_status())
    elif sub in ("lang", "语言", "languages"):
        langs = runner._available_langs
        if langs:
            print("🔤 可用编程语言：")
            for lang, path in langs.items():
                print(f"  {lang:<12} {path}")
        else:
            print("⚠️ 未检测到可用的编程语言解释器")
    elif sub in ("run", "运行", "执行", "exec") and len(rest) >= 2:
        runner.enable()
        if len(rest) >= 3 and rest[1] in ("python", "javascript", "js", "shell", "bash", "ruby", "perl", "lua"):
            lang = "javascript" if rest[1] == "js" else ("shell" if rest[1] == "bash" else rest[1])
            code = " ".join(rest[2:])
        else:
            lang = "python"
            code = " ".join(rest[1:])
        result = runner.run_code(code, language=lang)
        print(result.format_summary())
        if result.stdout:
            print(f"\n📤 stdout:\n{result.stdout}")
        if result.stderr:
            print(f"\n📥 stderr:\n{result.stderr}")
    elif sub in ("trace", "追踪", "调用") and len(rest) >= 2:
        runner.enable()
        code = " ".join(rest[1:])
        result = runner.run_code(code, language="python", capture_trace=True)
        print(result.format_summary())
        if result.call_trace:
            print(f"\n📞 调用轨迹（{len(result.call_trace)} 次）：")
            for c in result.call_trace[:30]:
                print(f"  {c.get('func', '?')}({c.get('args', {})}) → {c.get('ret', c.get('return', ''))} ({c.get('ms', c.get('duration_ms', 0))}ms)")
    elif sub in ("cov", "coverage", "覆盖率") and len(rest) >= 2:
        runner.enable()
        code = " ".join(rest[1:])
        result = runner.run_code(code, language="python", capture_coverage=True)
        print(result.format_summary())
        if result.coverage:
            cov = result.coverage
            # v4.5 统一格式：files + overall_coverage（restricted/subprocess 一致）
            overall = cov.get("overall_coverage", 0)
            print(f"\n📊 覆盖率: {overall:.1%}")
            for fname, finfo in cov.get("files", {}).items():
                covered = len(finfo.get("lines_covered", []))
                total = len(finfo.get("lines_total", []))
                uncovered = sorted(set(finfo.get("lines_total", [])) - set(finfo.get("lines_covered", [])))
                print(f"   {fname}: {covered}/{total} 行")
                if uncovered:
                    print(f"   未覆盖行: {uncovered[:20]}")
    elif sub in ("test", "测试") and len(rest) >= 2:
        runner.enable()
        filepath = rest[1]
        if not os.path.isfile(filepath):
            print(f"✖ 文件不存在: {filepath}")
            return
        with open(filepath, "r", encoding="utf-8") as f:
            code = f.read()
        lang = "python" if filepath.endswith(".py") else "javascript" if filepath.endswith(".js") else "shell"
        result = runner.run_tests(code, language=lang)
        print(result.format_summary())
        if result.stdout:
            print(f"\n📤 测试输出:\n{result.stdout}")
    elif sub in ("scan", "安全扫描") and len(rest) >= 2:
        from treefarm.sandbox.pattern_matcher import scan_security
        filepath = rest[1]
        if os.path.isfile(filepath):
            with open(filepath, "r", encoding="utf-8") as f:
                code = f.read()
            issues = scan_security(code)
            if issues:
                print(f"🔍 安全扫描发现 {len(issues)} 个问题：")
                for issue in issues:
                    print(f"  [{issue['severity'].upper()}] {issue['rule']}: {issue['description']}")
            else:
                print("✅ 未发现安全问题")
    elif sub in ("quality", "质量") and len(rest) >= 2:
        from treefarm.sandbox.code_quality import analyze_code_quality
        filepath = rest[1]
        if os.path.isfile(filepath):
            with open(filepath, "r", encoding="utf-8") as f:
                code = f.read()
            report = analyze_code_quality(code, filepath)
            print(f"📊 代码质量: {report['metrics']['functions']}函数, "
                  f"平均复杂度{report['complexity']['average']}, "
                  f"{len(report['issues'])}个问题, "
                  f"技术债务{report['technical_debt']['formatted']}")
    elif sub in ("profile", "性能") and len(rest) >= 2:
        from treefarm.sandbox.profiler import Profiler
        filepath = rest[1]
        if os.path.isfile(filepath):
            with open(filepath, "r", encoding="utf-8") as f:
                code = f.read()
            profiler = Profiler()
            profiler.enable()
            try:
                exec(code, {})
            finally:
                profiler.disable()
            print(profiler.print_report(top_n=10))
    else:
        print(f"❌ 未知沙箱命令: {sub}")
        print("💡 支持: on/off/status/lang/run/trace/cov/test/scan/quality/profile")


# ===== 交互式 REPL（v3.7 新增 --repl） =====
REPL_HELP = {
    "brief": "AI 工作简报",
    "search": "语义搜索: search <关键词>",
    "dead-code": "死代码检测",
    "circular-deps": "循环依赖检测",
    "impact": "影响分析: impact <文件> [符号] [深度]",
    "complexity": "代码复杂度: complexity [文件]",
    "architecture": "架构分层识别",
    "graph": "调用图: graph [文件]",
    "duplicates": "重复代码: duplicates [阈值]",
    "duplicates-func": "函数级查重: duplicates-func [阈值]",
    "debt": "技术债务评估",
    "smells": "代码异味检测（长函数/长参数/嵌套/魔法数字等）",
    "bird": "报小鸟: bird <源> <目标>",
    "trash": "垃圾箱状态",
    "help": "帮助: help [命令]",
    "exit": "退出 REPL（Ctrl+D 也行）",
}


def _repl_dispatch(farm: TreeFarm, root: str, cmd: str, args: List[str]) -> bool:
    """REPL 单条命令分发。返回 False 表示退出。"""
    def _path(a: str) -> str:
        return a if os.path.isabs(a) else os.path.join(root, a)

    if cmd in ("exit", "quit", "q"):
        return False
    if cmd == "help":
        if args:
            print(REPL_HELP.get(args[0], f"未知命令: {args[0]}"))
        else:
            for c, h in REPL_HELP.items():
                print(f"  {c:<16} {h}")
        return True
    if cmd == "brief":
        print(farm.brief(compact="--compact" in args))
    elif cmd == "search" and args:
        hits = semantic_search(args[0], farm.scanned["tree"],
                               farm.symbol_index, farm.content_index)
        if not hits:
            print(f"🔍 没找到与「{args[0]}」相关的文件")
        else:
            print(f"🔍 「{args[0]}」的相关文件（按相似度排序）：")
            for f, score, why in hits:
                print(f"  {score:.2f}  {os.path.relpath(f, root)}  ({why})")
    elif cmd == "dead-code":
        print(farm.dead_code())
    elif cmd == "circular-deps":
        print(farm.circular_deps())
    elif cmd == "impact" and args:
        symbol = args[1] if len(args) >= 2 else None
        try:
            depth = int(args[2]) if len(args) >= 3 else 3
        except ValueError:
            print(f"✖ 深度参数必须是数字: {args[2]}")
            return True
        print(farm.impact(_path(args[0]), symbol, depth))
    elif cmd == "complexity":
        target = _path(args[0]) if args else None
        print(farm.complexity(target))
    elif cmd == "architecture":
        print(farm.architecture())
    elif cmd == "graph":
        target = _path(args[0]) if args else None
        print(farm.call_graph(target))
    elif cmd == "duplicates":
        try:
            threshold = float(args[0]) if args else 0.6
        except ValueError:
            print(f"✖ 相似度阈值必须是数字（0~1）: {args[0]}")
            return True
        print(farm.duplicates(threshold=threshold))
    elif cmd == "duplicates-func":
        try:
            threshold = float(args[0]) if args else 0.6
        except ValueError:
            print(f"✖ 相似度阈值必须是数字（0~1）: {args[0]}")
            return True
        print(farm.duplicates_func(threshold=threshold))
    elif cmd == "debt":
        print(farm.debt())
    elif cmd == "smells":
        print(farm.smells())
    elif cmd == "bird" and len(args) >= 2:
        print(farm.bird(_path(args[0]), args[1]))
    elif cmd == "trash":
        print(farm.trash.report())
    else:
        print(f"❌ 未知命令: {cmd}（help 看支持的命令）")
    return True


def _repl(farm: TreeFarm, root: str) -> None:
    """交互式 REPL 模式（v3.7 新增，--repl）：
    启动时扫描一次建库，之后每条命令复用结果，不用重复扫描。
    支持 Tab 补全（命令名 + 项目文件路径）、命令历史（~/.treefarm_history）、彩色输出。"""
    is_tty = sys.stdout.isatty()

    def _paint(text: str, color: str = "cyan") -> str:
        codes = {"cyan": "96", "green": "92", "yellow": "93", "red": "91",
                 "bold": "1", "dim": "2"}
        return f"\033[{codes[color]}m{text}\033[0m" if is_tty else text

    try:
        import readline
        _has_readline = True
    except ImportError:
        _has_readline = False

    if _has_readline:
        hist = os.path.join(os.path.expanduser("~"), ".treefarm_history")
        try:
            readline.read_history_file(hist)
        except (OSError, IOError):
            pass
        readline.parse_and_bind("tab: complete")
        cmds = sorted(REPL_HELP)

        def _completer(text: str, state: int):
            options = [c for c in cmds if c.startswith(text)]
            if not options:
                # 文件路径补全（相对项目根；支持子目录）
                if "/" in text:
                    base, name = os.path.split(text)
                else:
                    base, name = "", text
                full = os.path.join(root, base) if base else root
                try:
                    names = os.listdir(full)
                except OSError:
                    names = []
                options = [os.path.join(base, n) if base else n
                           for n in names if n.startswith(name)]
            return options[state] if state < len(options) else None

        readline.set_completer(_completer)

    # 启动时扫描一次，REPL 会话内复用（项目缓存）
    t0 = time.time()
    is_new, _stat = farm.plant()
    print(f"🌳 树场已就绪（建库={'是' if is_new else '否'}，扫描 {time.time() - t0:.1f}s），"
          f"输入 help 查看命令，exit 退出")
    print(_paint("🌳> ", "green"), end="", flush=True)
    while True:
        try:
            line = input().strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        parts = line.split()
        try:
            cont = _repl_dispatch(farm, root, parts[0].lower(), parts[1:])
        except Exception as e:
            print(f"✖ 命令出错: {e}")
            cont = True
        if not cont:
            break

    if _has_readline:
        try:
            readline.write_history_file(hist)
        except (OSError, IOError):
            pass
    print("👋 树场 REPL 已退出，基因库已保存")


def main() -> None:
    args = sys.argv[1:]
    debug = "--debug" in args
    setup_logging(debug)
    args = [a for a in args if a != "--debug"]

    # 处理全局选项（不需要项目路径）
    if not args or args[0] in ("--help", "-h", "help", "帮助"):
        print(__doc__)
        return
    if args[0] in ("--version", "-v", "version", "版本"):
        print(f"🌳 TreeFarm v{VERSION} (schema v{SCHEMA_VERSION})")
        print(f"   零依赖代码分析引擎 · 5语言函数级分析 · 小沙箱动态验证")
        return
    # 中文菜单速查（小白友好：不用记命令，跑一下全知道）
    if args[0] in ("菜单", "速查", "功能", "我会什么"):
        print(MENU_TEXT)
        return
    # 沙箱快捷入口（无需项目路径，与 SKILL.md 文档一致）
    if args[0] in ("--sandbox", "沙箱", "小沙箱", "动态分析"):
        _dispatch_sandbox(args[1:])
        return

    # 第一个参数必须是项目路径，不能是选项
    if args[0].startswith("-"):
        print(f"❌ 未知选项: {args[0]}")
        print("💡 用法: python3 tree_farm.py <项目路径> [选项]")
        print("   查看帮助: python3 tree_farm.py --help")
        print("   查看版本: python3 tree_farm.py --version")
        return

    root = os.path.abspath(args[0])
    # v4.6：路径不存在时给出友好提示，而不是静默退出/报 Traceback
    if not os.path.exists(root):
        print(f"❌ 路径不存在: {root}")
        print("💡 请检查项目路径是否写错（可指向文件夹或单个代码文件）")
        print("   查看帮助: python3 tree_farm.py --help")
        return

    farm = TreeFarm(root)
    try:
        if "--repl" in args or any(a in ("交互", "交互模式", "问答模式") for a in args):
            _repl(farm, root)
        else:
            _dispatch(farm, root, args)
    finally:
        if farm.bank is not None:
            farm.bank.close()
