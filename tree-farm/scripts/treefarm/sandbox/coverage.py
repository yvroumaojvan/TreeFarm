# -*- coding: utf-8 -*-
"""树场小沙箱 —— 轻量覆盖率分析器（v3.8 新增）。
基于 sys.settrace 的轻量代码覆盖率采集，不依赖 coverage.py。
输出 JSON 格式的覆盖率报告，供 AI 分析哪些代码行被执行、哪些未覆盖。
零依赖，纯标准库实现。
"""
import sys
import ast
import json
import os
from typing import Any, Dict, List, Optional, Set, Tuple


class CoverageCollector:
    """轻量覆盖率采集器（v3.8 沙箱新增）。
    用法：
        collector = CoverageCollector()
        collector.start()
        exec(code)
        collector.stop()
        report = collector.report()
    """

    def __init__(self, source_code: str = "", source_file: str = "<sandbox>"):
        self.source_code = source_code
        self.source_file = source_file
        self._executed_lines: Set[int] = set()
        self._all_lines: Set[int] = set()
        self._active = False
        self._old_trace = None
        self._file_coverage: Dict[str, Dict[str, Any]] = {}

    def _parse_executable_lines(self, code: str) -> Set[int]:
        """解析代码中所有可执行行（排除空行、注释、docstring、装饰器等）。"""
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return set()
        lines = set()
        for node in ast.walk(tree):
            if hasattr(node, "lineno"):
                # 排除函数/类定义行本身（它们的行号是 def/class，不算可执行）
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    continue
                # 排除 import 行（虽然可执行，但覆盖率意义不大）
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    continue
                lines.add(node.lineno)
        return lines

    def _trace(self, frame, event, arg):
        """sys.settrace 回调：记录执行过的行。"""
        if not self._active:
            return None
        if event == "line":
            filename = frame.f_code.co_filename
            lineno = frame.f_lineno
            if filename == self.source_file or filename.startswith("<"):
                self._executed_lines.add(lineno)
            # 记录所有文件的覆盖率
            if filename not in self._file_coverage:
                self._file_coverage[filename] = {"executed": set(), "total": set()}
            self._file_coverage[filename]["executed"].add(lineno)
        return self._trace

    def start(self) -> None:
        """启动覆盖率采集。"""
        self._executed_lines = set()
        self._all_lines = self._parse_executable_lines(self.source_code)
        self._file_coverage = {}
        self._active = True
        self._old_trace = sys.gettrace()
        sys.settrace(self._trace)

    def stop(self) -> None:
        """停止覆盖率采集。"""
        self._active = False
        sys.settrace(self._old_trace)

    def report(self) -> Dict[str, Any]:
        """生成覆盖率报告。"""
        covered = self._executed_lines & self._all_lines
        total = len(self._all_lines)
        covered_count = len(covered)
        ratio = covered_count / total if total else 0.0
        return {
            "file": self.source_file,
            "total_lines": total,
            "covered_lines": covered_count,
            "uncovered_lines": sorted(self._all_lines - self._executed_lines),
            "covered_line_numbers": sorted(covered),
            "coverage": round(ratio, 4),
            "coverage_percent": f"{ratio * 100:.1f}%",
        }

    def to_json(self) -> str:
        """转为 JSON 字符串。"""
        return json.dumps(self.report(), ensure_ascii=False, indent=2)

    def format_report(self) -> str:
        """格式化人类可读的覆盖率报告。"""
        r = self.report()
        lines = [
            f"📊 代码覆盖率：{r['coverage_percent']}",
            f"   可执行行: {r['total_lines']} | 已覆盖: {r['covered_lines']}",
        ]
        if r["uncovered_lines"]:
            uncovered = r["uncovered_lines"][:20]
            lines.append(f"   未覆盖行: {', '.join(map(str, uncovered))}"
                         + (f" ... 还有 {len(r['uncovered_lines']) - 20} 行"
                            if len(r["uncovered_lines"]) > 20 else ""))
        return "\n".join(lines)


def inject_coverage(code: str) -> str:
    """将覆盖率采集代码注入到用户代码开头，返回包装后的代码。
    用于 subprocess 沙箱中，让子进程自己输出覆盖率 JSON。
    v4.5 修复：原先用 range(1, max+1) 估算总行数，会把空行/注释/包装代码
    全部算进「总行」，导致覆盖率严重失真（实测 75% → 6.25%）。
    现在用 AST 解析原始代码的可执行行号，注入后换算行号精确比对。
    """
    # 预先解析可执行行号（只统计语句节点 ast.stmt，排除定义/import 行；空行/注释天然无节点）
    import ast
    _exec_lines: Set[int] = set()
    try:
        _tree = ast.parse(code)
        for _n in ast.walk(_tree):
            if isinstance(_n, ast.stmt) and hasattr(_n, "lineno"):
                if isinstance(_n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    continue
                if isinstance(_n, (ast.Import, ast.ImportFrom)):
                    continue
                _exec_lines.add(_n.lineno)
    except SyntaxError:
        pass
    exec_lines_literal = "{" + ", ".join(str(x) for x in sorted(_exec_lines)) + "}"

    # wrapper 共 17 行前缀（第 1 行为空行，含 f_trace 行），用户代码第 1 行落在包装后第 18 行
    # ★ 关键：sys.settrace 不追踪当前帧，必须显式设置 sys._getframe().f_trace，
    #   否则模块级顶层调用（如 add(1,2)）的行不会被记录（实测仅 1 行）
    wrapper = f'''
import sys, json
_cov_executed = set()
_cov_all = {exec_lines_literal}
_COV_PREFIX_LINES = 17
def _cov_trace(frame, event, arg):
    try:
        if event == "line":
            _lineno = frame.f_lineno - _COV_PREFIX_LINES
            if _lineno in _cov_all:
                _cov_executed.add(_lineno)
    except Exception:
        pass
    return _cov_trace
sys.settrace(_cov_trace)
sys._getframe().f_trace = _cov_trace
try:
'''
    indented_code = "\n".join("    " + line for line in code.splitlines())
    footer = '''
finally:
    sys.settrace(None)
    _covered = _cov_executed & _cov_all
    print("\\n===SANDBOX_COVERAGE===")
    print(json.dumps({
        "total": len(_cov_all),
        "covered": len(_covered),
        "coverage": round(len(_covered) / len(_cov_all), 4) if _cov_all else 0.0,
        "uncovered_lines": sorted(_cov_all - _cov_executed)[:50],
        "covered_line_numbers": sorted(_covered),
        "total_lines": len(_cov_all),
        "covered_lines": len(_covered),
    }, ensure_ascii=False))
    print("===END_COVERAGE===")
'''
    return wrapper + indented_code + footer


def parse_coverage_output(stdout: str) -> Optional[Dict[str, Any]]:
    """从 subprocess 沙箱的 stdout 中解析覆盖率 JSON。"""
    marker_start = "===SANDBOX_COVERAGE==="
    marker_end = "===END_COVERAGE==="
    if marker_start not in stdout or marker_end not in stdout:
        return None
    try:
        start = stdout.index(marker_start) + len(marker_start)
        end = stdout.index(marker_end, start)
        json_str = stdout[start:end].strip()
        return json.loads(json_str)
    except (ValueError, json.JSONDecodeError):
        return None
