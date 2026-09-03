# -*- coding: utf-8 -*-
"""树场机制 —— 项目功能描述上下文（Spec）与 Grader 综合评分（v4.7 新增）。

树场从「扫出问题」升格为「grader：评估程序好坏并支撑后续决策」的关键一层。

包含三块能力：
  1. build_spec() / autodetect_spec() —— 项目功能画像：
       被检者输入功能描述（--spec "..."），或让 AI 自己读项目
       （README / docs / 入口 docstring / 基因库）推断功能要点。
       画像含 features（功能点）、stack（技术栈）、focused（该重点看的维度）。
  2. 上下文感知：
      带着功能画像出报告时，把问题标注「与哪个功能相关」，
      并给出「哪些检查项与该项目高度相关 / 哪些可能不适用」的判断，
      让抓 bug / 修 bug 更精准（结合功能而非泛泛而谈）。
  3. grade_project() / diff_grade() —— Grader 综合评分：
      六维健康度（安全/性能/逻辑/结构/质量/冗余）统一为 0~100，
      加权出综合分 + 等级，支持与上次评分对比看趋势（进步/退步）。
"""

import json
import os
import re
import time
from typing import Any, Dict, List, Optional

# ========== 功能画像：描述解析 ==========

# 技术栈/领域关键词 → 判定特征（描述里出现即认为项目含该能力）
_STACK_KEYWORDS = {
    "web": ["web", "http", "flask", "django", "fastapi", "网站", "网页", "接口", "api",
            "rest", "server", "服务端", "后端", "url", "路由", "bottle", "tornado",
            "aiohttp", "前端", "浏览器", "html", "json 接口", "graphql"],
    "database": ["数据库", "sql", "mysql", "postgres", "sqlite", "redis", "mongo",
                 "orm", "查询", "存储", "数据表", "sqlalchemy"],
    "cli": ["命令行", "cli", "终端", "命令", "shell", "参数解析", "脚本工具",
            "控制台", "argv", "输入命令"],
    "gui_app": ["android", "app", "界面", "ui", "按钮", "窗口", "桌面应用",
                "apk", "悬浮窗", "点击", "触摸"],
    "network": ["网络", "socket", "请求", "下载", "上传", "爬虫", "抓取",
                "http 请求", "urlopen", "代理", "连接服务器"],
    "auth": ["登录", "注册", "密码", "认证", "token", "jwt", "session", "会话",
             "权限", "鉴权", "cookie", "账号", "用户体系"],
    "file": ["文件", "读写", "路径", "上传", "导出", "导入", "解析文件", "csv",
             "图片处理", "压缩"],
    "async": ["异步", "协程", "async", "await", "事件循环", "并发请求", "asyncio"],
    "pay": ["支付", "订单", "金额", "充值", "收款", "交易", "账单"],
    "ai_ml": ["ai", "大模型", "llm", "机器学习", "推理", "智能", "识别", "ocr",
              "语音", "翻译", "ocr", "生成"],
    "multithread": ["线程", "多线程", "thread", "并发", "线程池", "并行", "任务队列"],
}

# 每种「已判定具备」的栈能力 → 应该重点关注的检测类型（v4.7 上下文感知）
_STACK_FOCUS: Dict[str, List[str]] = {
    "web": ["SQL注入", "XSS", "开放重定向", "认证绕过", "CSRF", "SSRF", "CRLF注入",
            "会话固定", "路径遍历", "认证缺失"],
    "database": ["SQL注入", "N+1查询", "数据库连接未关闭", "缺少数据库索引"],
    "auth": ["硬编码密码", "弱哈希", "时序攻击", "认证绕过", "会话固定", "硬编码Token",
             "权限提升", "明文存储密码"],
    "network": ["SSRF", "命令注入", "CRLF注入", "网络连接未关闭", "路径遍历"],
    "cli": ["命令注入", "路径遍历", "参数校验", "可变默认参数", "除零风险"],
    "gui_app": ["任意文件上传", "路径遍历", "调试模式", "硬编码API密钥"],
    "file": ["路径遍历", "任意文件上传", "Zip Slip", "文件未关闭", "TOCTOU 竞争"],
    "async": ["同步IO阻塞事件循环", "协程未await", "竞态条件"],
    "pay": ["支付金额校验", "硬编码密钥", "日志泄露", "越权"],
    "multithread": ["竞态条件", "死锁", "线程池未关闭"],
}

# ========== 项目功能画像（人工输入版） ==========

def build_spec(description: str) -> Dict[str, Any]:
    """解析用户输入的「被检项目功能描述」，构建功能画像。

    输入示例：
      "这是一个 Flask 博客系统：用户可以注册登录、发布文章、评论，
       支持搜索和后台管理，数据存 MySQL。"
    输出:
      {"desc": 原文, "stack": [已判定能力], "focused": [建议重点检测类型],
       "snippet": 一句话画像}
    """
    description = (description or "").strip()
    if not description:
        return {"desc": "", "stack": [], "focused": [], "snippet": ""}
    text_lower = description.lower()
    stack = []
    for key, kws in _STACK_KEYWORDS.items():
        for kw in kws:
            if kw in text_lower:
                stack.append(key)
                break
    # 去重保序
    stack = list(dict.fromkeys(stack))
    focused: List[str] = []
    for s in stack:
        for t in _STACK_FOCUS.get(s, []):
            if t not in focused:
                focused.append(t)
    snippet = description if len(description) <= 80 else description[:77] + "..."
    return {"desc": description, "stack": stack, "focused": focused, "snippet": snippet}


# ========== 项目功能画像（AI 自读版：零依赖启发式） ==========

def autodetect_spec(tree_files: List[str], root: Optional[str] = None) -> Dict[str, Any]:
    """让树场「自己读」被检项目，推断功能画像。

    零依赖启发式（无需 LLM key 也能用）：
      1. 读 README*/readme* 文件，抽取标题 + 前几行简介
      2. 读 docs/*.md 的标题行
      3. 读常见入口文件（main/app/run/__main__/cli）的模块 docstring
      4. 把这些文本拼起来 → build_spec 判定栈能力
    无 README 时基于文件命名/目录结构兜底（web→含 route/app.py 等）。

    返回与 build_spec 相同结构，另带 "source": 依据文本。
    """
    probe_texts: List[str] = []
    readme_hits: List[str] = []

    for f in tree_files:
        base = os.path.basename(f)
        low = base.lower()
        rel = os.path.relpath(f, root) if root else f
        rel_low = rel.lower()
        # README 优先
        if low in ("readme.md", "readme.txt", "readme", "readme.rst") or \
           (low.startswith("readme") and low.endswith((".md", ".txt"))):
            text = _head_text(f, 2500)
            if text:
                readme_hits.append(f"{rel}:\n{text[:1200]}")
                probe_texts.append(text)
        # docs 目录的 md 标题
        elif "/docs/" in rel_low and low.endswith((".md", ".txt")):
            text = _head_text(f, 1200)
            if text:
                probe_texts.append(text[:800])
        # 入口文件 docstring（Python）
        elif low in ("app.py", "main.py", "run.py", "cli.py", "__main__.py",
                     "server.py", "manage.py") and f.endswith(".py"):
            text = _head_text(f, 3000)
            if text:
                probe_texts.append(text[:1600])

    source_text = "\n".join(probe_texts)
    if readme_hits:
        source_text = "\n".join(readme_hits) + "\n" + source_text

    result = build_spec(source_text)

    # 无 README 时的文件名兜底判定
    if not result["stack"]:
        names = " ".join(os.path.basename(f).lower() for f in tree_files)
        dirs = " ".join(os.path.basename(os.path.dirname(f)).lower() for f in tree_files)
        if any(k in names for k in ("route", "view", "template", "static")):
            result["stack"].append("web")
        if any(k in dirs for k in ("controller", "routes", "views", "templates")):
            if "web" not in result["stack"]:
                result["stack"].append("web")
        if any(k in names for k in ("model", "repository", "dao", "schema")):
            result["stack"].append("database")
        if any(k in names for k in ("main", "cli", "cmd")):
            result["stack"].append("cli")
        result["stack"] = list(dict.fromkeys(result["stack"]))
        result["focused"] = []
        for s in result["stack"]:
            for t in _STACK_FOCUS.get(s, []):
                if t not in result["focused"]:
                    result["focused"].append(t)

    result["source"] = source_text[:4000]
    return result


def _head_text(path: str, chars: int) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            return fh.read(chars)
    except Exception:
        return ""


# ========== 画像输出 ==========

def format_spec(ctx: Dict[str, Any]) -> str:
    """把功能画像格式化成报告开头的一段（中文，树场风格）。"""
    if not ctx or (not ctx.get("stack") and not ctx.get("desc")):
        return ("🎯 项目功能画像: 未提供/未能自读（可用 --spec \"描述项目功能\" 提供，"
                "或 --spec-read 让我自己读，抓 bug 更精准）")
    stack_cn = {
        "web": "Web服务", "database": "数据库", "cli": "命令行工具",
        "gui_app": "应用/界面", "network": "网络通信", "auth": "用户认证",
        "file": "文件处理", "async": "异步", "pay": "支付相关",
        "ai_ml": "AI/智能", "multithread": "多线程",
    }
    if ctx.get("stack"):
        names = "、".join(stack_cn.get(s, s) for s in ctx["stack"])
        lines = [f"🎯 项目功能画像: {names}"]
    else:
        lines = ["🎯 项目功能画像: 已提供描述（未识别出明显技术栈特征）"]
    if ctx.get("desc"):
        lines.append(f"   描述: {ctx['desc'][:200]}")
    if ctx.get("focused"):
        fcs = ctx["focused"][:12]
        lines.append(f"   重点检测: {', '.join(fcs)}（结合功能上下文）")
    return "\n".join(lines)


# ========== Grader 综合评分 ==========

# 六维健康度权重（和为 1）：安全最重，逻辑次之
_GRADE_WEIGHTS = {
    "security": 0.30,
    "logic": 0.25,
    "performance": 0.20,
    "structure": 0.10,
    "quality": 0.10,
    "debt": 0.05,
}


def grade_project(dim_scores: Dict[str, float]) -> Dict[str, Any]:
    """把各维度健康度（0~100，越高越好）加权成综合分。

    输入 dim_scores: {"security": 88.0, "logic": 75.0, "performance": 90.0,
                      "structure": 82.0, "quality": 70.0, "debt": 60.0}
    输出:
      {"composite": 综合分, "grade": 等级, "emoji": ..., "dimensions": {...},
       "weights": {...}, "suggestions": [...]}
    """
    total_w = sum(_GRADE_WEIGHTS.values())
    composite = 0.0
    present = {}
    for dim, w in _GRADE_WEIGHTS.items():
        s = dim_scores.get(dim)
        if s is None:
            continue
        s = max(0.0, min(100.0, float(s)))
        present[dim] = s
        composite += s * (w / total_w)
    composite = round(composite, 1)
    if composite >= 95:
        grade, emoji = "A+", "🏆"
    elif composite >= 90:
        grade, emoji = "A", "🟢"
    elif composite >= 80:
        grade, emoji = "B", "🟢"
    elif composite >= 65:
        grade, emoji = "C", "🟡"
    elif composite >= 50:
        grade, emoji = "D", "🟠"
    else:
        grade, emoji = "F", "🔴"

    suggestions = _grade_suggestions(present)
    return {"composite": composite, "grade": grade, "emoji": emoji,
            "dimensions": present, "weights": dict(_GRADE_WEIGHTS),
            "suggestions": suggestions}


def _grade_suggestions(dims: Dict[str, float]) -> List[str]:
    """按「短板优先」给改进方向（grader 决策支撑）。"""
    sugg = []
    if not dims:
        return sugg
    # 最短板维度
    weakest = min(dims.items(), key=lambda kv: kv[1])
    if weakest[1] < 60:
        sugg.append(f"最短板: {_dim_cn(weakest[0])} 健康度 {weakest[1]:.0f}/100 —— 先集中修这里，"
                    f"提分最快（对应报告里的修复建议）")
    for dim, s in sorted(dims.items(), key=lambda kv: kv[1]):
        if s < 80 and dim != weakest[0]:
            sugg.append(f"{_dim_cn(dim)} {s:.0f}/100，有提升空间（见对应检测报告）")
    good = [d for d, s in dims.items() if s >= 90]
    if good:
        sugg.append("做得好的维度: " + "、".join(_dim_cn(d) for d in good)
                    + "，保持当前水准")
    if not sugg:
        sugg.append("各维度都很健康，可以考虑进阶项（查重/债务清理）进一步打磨")
    return sugg


def _dim_cn(dim: str) -> str:
    return {"security": "安全", "logic": "逻辑正确性", "performance": "性能",
            "structure": "结构", "quality": "质量", "debt": "技术债务"}.get(dim, dim)


def format_grade(g: Dict[str, Any], prev: Optional[Dict[str, Any]] = None) -> str:
    """把综合评分格式化成报告文本。prev 为上次评分时输出趋势对比。"""
    lines = ["=" * 56, "🏆 Grader 综合评分报告（v4.7）", "=" * 56]
    lines.append(f"综合评分: {g['composite']}/100  {g['emoji']} 等级 {g['grade']}")
    lines.append("")
    lines.append("【六维健康度】（越高越好）")
    for dim, s in g["dimensions"].items():
        bar = "█" * int(s / 5) + "░" * (20 - int(s / 5))
        delta = ""
        if prev and dim in prev.get("dimensions", {}):
            d = g["dimensions"][dim] - prev["dimensions"][dim]
            delta = f"  {'📈 +' if d > 0 else '📉 ' if d < 0 else '＝ '}{d:.1f}" if d != 0 else "  ＝ 0.0"
        lines.append(f"  {_dim_cn(dim):<6} {s:5.1f} |{bar}|{delta}")
    if prev and prev.get("composite") is not None:
        d = g["composite"] - prev["composite"]
        trend = "📈 进步" if d > 0.5 else ("📉 退步" if d < -0.5 else "＝ 持平")
        lines.append(f"  综合分较上次: {prev['composite']} → {g['composite']} "
                     f"({'+' if d > 0 else ''}{d:.1f}) {trend}")
    lines.append("")
    lines.append("【改进方向】")
    for s in g["suggestions"]:
        lines.append(f"  • {s}")
    return "\n".join(lines)


# ========== 评分历史（SQLite meta，走 GeneBank 持久化） ==========

def save_grade(bank: Any, grade: Dict[str, Any]) -> None:
    """把最近一次综合评分存进基因库 meta（key: last_grade）。"""
    try:
        snap = {k: v for k, v in grade.items() if k != "suggestions"}
        snap["_ts"] = time.strftime("%Y-%m-%d %H:%M")
        bank._meta_set("last_grade", json.dumps(snap, ensure_ascii=False))
    except Exception:
        pass


def load_grade(bank: Any) -> Optional[Dict[str, Any]]:
    """读上次综合评分，没有返回 None。"""
    try:
        raw = bank._meta_get("last_grade", None)
        if not raw:
            return None
        data = json.loads(raw)
        data.pop("_ts", None)
        return data
    except Exception:
        return None
