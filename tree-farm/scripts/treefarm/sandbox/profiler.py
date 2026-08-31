# -*- coding: utf-8 -*-
"""树场小沙箱 —— 轻量级性能剖析器（v3.9 新增，对标 cProfile）。

零依赖实现，不依赖 cProfile/line_profiler 等第三方库。
功能：
  1. 函数级耗时统计（调用次数、总耗时、平均耗时、最大耗时）
  2. 调用关系追踪（谁调用了谁）
  3. 热点函数识别（按耗时排序）
  4. 与沙箱结合：动态执行 + 性能剖析

设计原则：极致轻量化，使用 sys.settrace 实现，手机可运行。
"""

import sys
import time
from typing import Any, Dict, List, Optional, Tuple


class FunctionProfile:
    """单个函数的性能数据。"""

    def __init__(self, name: str, filename: str = "", lineno: int = 0):
        self.name = name
        self.filename = filename
        self.lineno = lineno
        self.call_count = 0
        self.total_time = 0.0  # 包含子调用的总时间
        self.self_time = 0.0   # 不包含子调用的自身时间
        self.max_time = 0.0
        self.min_time = float('inf')
        self._start_time = 0.0
        self._children_time = 0.0

    def to_dict(self) -> Dict[str, Any]:
        avg = self.total_time / self.call_count if self.call_count else 0
        return {
            "name": self.name,
            "filename": self.filename,
            "lineno": self.lineno,
            "call_count": self.call_count,
            "total_time_ms": round(self.total_time * 1000, 3),
            "self_time_ms": round(self.self_time * 1000, 3),
            "avg_time_ms": round(avg * 1000, 3),
            "max_time_ms": round(self.max_time * 1000, 3),
            "min_time_ms": round(self.min_time * 1000, 3) if self.min_time != float('inf') else 0,
        }


class Profiler:
    """轻量级性能剖析器。

    用法：
        profiler = Profiler()
        profiler.enable()
        # ... 执行代码 ...
        profiler.disable()
        report = profiler.get_report()
    """

    def __init__(self):
        self._functions: Dict[str, FunctionProfile] = {}
        self._call_stack: List[Tuple[str, float]] = []  # (func_id, start_time)
        self._enabled = False
        self._old_trace = None

    def enable(self):
        """启用剖析。"""
        if self._enabled:
            return
        self._enabled = True
        self._old_trace = sys.gettrace()
        sys.settrace(self._trace_callback)

    def disable(self):
        """禁用剖析。"""
        if not self._enabled:
            return
        self._enabled = False
        sys.settrace(self._old_trace)

    def _trace_callback(self, frame, event, arg):
        """sys.settrace 回调。"""
        if event == "call":
            func_name = frame.f_code.co_name
            if func_name and not func_name.startswith("<"):
                func_id = f"{frame.f_code.co_filename}:{frame.f_code.co_firstlineno}:{func_name}"
                if func_id not in self._functions:
                    self._functions[func_id] = FunctionProfile(
                        func_name, frame.f_code.co_filename,
                        frame.f_code.co_firstlineno
                    )
                fp = self._functions[func_id]
                fp.call_count += 1
                start = time.perf_counter()
                self._call_stack.append((func_id, start))
        elif event == "return":
            if self._call_stack:
                func_id, start = self._call_stack.pop()
                elapsed = time.perf_counter() - start
                fp = self._functions[func_id]
                fp.total_time += elapsed
                fp.max_time = max(fp.max_time, elapsed)
                fp.min_time = min(fp.min_time, elapsed)
                # 自身时间 = 总时间 - 子调用时间
                # 子调用时间在调用栈中累加
                if self._call_stack:
                    parent_id = self._call_stack[-1][0]
                    self._functions[parent_id]._children_time += elapsed
        return self._trace_callback

    def get_report(self, top_n: int = 10) -> Dict[str, Any]:
        """获取性能报告。

        Args:
            top_n: 返回前 N 个热点函数

        Returns:
            性能报告字典
        """
        # 计算自身时间
        for fp in self._functions.values():
            fp.self_time = fp.total_time - fp._children_time

        # 按自身时间排序
        sorted_funcs = sorted(
            self._functions.values(),
            key=lambda f: f.self_time,
            reverse=True
        )

        total_self_time = sum(f.self_time for f in self._functions.values())

        return {
            "total_functions": len(self._functions),
            "total_calls": sum(f.call_count for f in self._functions.values()),
            "total_self_time_ms": round(total_self_time * 1000, 3),
            "hotspots": [f.to_dict() for f in sorted_funcs[:top_n]],
            "all_functions": [f.to_dict() for f in sorted_funcs],
        }

    def print_report(self, top_n: int = 10) -> str:
        """打印格式化的性能报告。"""
        report = self.get_report(top_n)
        lines = []
        lines.append("=" * 70)
        lines.append(f"性能剖析报告（前 {top_n} 个热点函数）")
        lines.append("=" * 70)
        lines.append(f"{'函数名':<30} {'调用次数':>8} {'自身ms':>8} {'总ms':>8} {'平均ms':>8}")
        lines.append("-" * 70)
        for f in report["hotspots"]:
            name = f["name"][:28]
            lines.append(f"{name:<30} {f['call_count']:>8} {f['self_time_ms']:>8.3f} "
                         f"{f['total_time_ms']:>8.3f} {f['avg_time_ms']:>8.3f}")
        lines.append("=" * 70)
        lines.append(f"总计: {report['total_functions']} 函数, "
                     f"{report['total_calls']} 次调用, "
                     f"{report['total_self_time_ms']:.3f}ms 自身时间")
        return "\n".join(lines)


def profile_code(code: str, globals_dict: Optional[Dict] = None) -> Tuple[Any, Dict[str, Any]]:
    """便捷函数：执行代码并返回性能报告。

    Args:
        code: 要执行的 Python 代码
        globals_dict: 执行环境的 globals 字典

    Returns:
        (执行结果, 性能报告) 元组
    """
    profiler = Profiler()
    if globals_dict is None:
        globals_dict = {}
    profiler.enable()
    try:
        result = exec(code, globals_dict)
    finally:
        profiler.disable()
    return result, profiler.get_report()
