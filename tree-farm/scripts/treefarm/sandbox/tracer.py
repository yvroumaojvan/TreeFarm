# -*- coding: utf-8 -*-
"""树场小沙箱 —— 调用追踪器（v3.8 新增）。
提供可注入到 Python 代码中的轻量调用追踪器，基于 sys.settrace。
输出 JSON 格式的调用轨迹，供 AI 分析函数行为、参数、返回值。
零依赖，纯标准库实现。
"""
import sys
import json
import time
import traceback
from typing import Any, Dict, List, Optional


class CallTracer:
    """轻量调用追踪器（v3.8 沙箱新增）。
    用法：
        tracer = CallTracer()
        tracer.start()
        exec(code)
        tracer.stop()
        trace_data = tracer.to_dict()
    """

    def __init__(self, max_calls: int = 10000, max_depth: int = 50):
        self.max_calls = max_calls
        self.max_depth = max_depth
        self._calls: List[Dict[str, Any]] = []
        self._call_stack: List[Dict[str, Any]] = []
        self._depth = 0
        self._active = False
        self._old_trace = None
        self._truncated = False
        self._start_time = 0.0

    def _trace(self, frame, event, arg):
        """sys.settrace 回调。"""
        if not self._active:
            return None

        if len(self._calls) >= self.max_calls:
            self._truncated = True
            return None

        func_name = frame.f_code.co_name
        filename = frame.f_code.co_filename
        lineno = frame.f_lineno

        # 只追踪用户代码（<sandbox> 或临时文件），跳过内置和库代码
        if filename.startswith("<") or "python3" in filename or "/lib/" in filename:
            if event == "call":
                return None  # 不深入库函数
            return self._trace

        if event == "call":
            if self._depth >= self.max_depth:
                return None  # 超过最大深度，不追踪
            self._depth += 1
            call_info = {
                "func": func_name,
                "file": filename,
                "line": lineno,
                "depth": self._depth,
                "start_time": time.time(),
                "args": {},
                "return": None,
                "exception": None,
            }
            # 尝试获取局部变量（参数）
            try:
                arg_info = {}
                for key, val in frame.f_locals.items():
                    if not key.startswith("__"):
                        try:
                            arg_info[key] = repr(val)[:200]
                        except Exception:
                            arg_info[key] = "<unrepr>"
                call_info["args"] = arg_info
            except Exception:
                pass
            self._call_stack.append(call_info)

        elif event == "return":
            if self._call_stack:
                call_info = self._call_stack.pop()
                call_info["return"] = repr(arg)[:200] if arg is not None else None
                call_info["duration_ms"] = (time.time() - call_info["start_time"]) * 1000
                del call_info["start_time"]
                self._calls.append(call_info)
                self._depth = max(0, self._depth - 1)

        elif event == "exception":
            if self._call_stack:
                call_info = self._call_stack[-1]
                exc_type, exc_val, _ = arg
                call_info["exception"] = f"{exc_type.__name__}: {exc_val}"

        return self._trace

    def start(self) -> None:
        """启动追踪。"""
        self._calls = []
        self._call_stack = []
        self._depth = 0
        self._truncated = False
        self._active = True
        self._start_time = time.time()
        self._old_trace = sys.gettrace()
        sys.settrace(self._trace)

    def stop(self) -> None:
        """停止追踪。"""
        self._active = False
        sys.settrace(self._old_trace)
        # 处理未返回的调用（如异常中断）
        while self._call_stack:
            call_info = self._call_stack.pop()
            call_info["exception"] = call_info.get("exception") or "未正常返回"
            call_info["duration_ms"] = (time.time() - call_info["start_time"]) * 1000
            del call_info["start_time"]
            self._calls.append(call_info)

    def to_dict(self) -> Dict[str, Any]:
        """转为字典。"""
        return {
            "total_calls": len(self._calls),
            "truncated": self._truncated,
            "max_calls_limit": self.max_calls,
            "calls": self._calls,
        }

    def to_json(self) -> str:
        """转为 JSON 字符串。"""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    def format_summary(self) -> str:
        """格式化人类可读的调用摘要。"""
        if not self._calls:
            return "（无调用记录）"
        lines = [f"📞 调用追踪：共 {len(self._calls)} 次函数调用"
                 + ("（已截断）" if self._truncated else "")]
        for i, c in enumerate(self._calls[:50]):  # 最多显示50条
            indent = "  " * (c["depth"] - 1)
            exc = f" ⚠️ {c['exception']}" if c.get("exception") else ""
            ret = f" → {c['return']}" if c.get("return") else ""
            dur = f" ({c.get('duration_ms', 0):.1f}ms)"
            lines.append(f"  {indent}{c['func']}{dur}{ret}{exc}")
        if len(self._calls) > 50:
            lines.append(f"  ... 还有 {len(self._calls) - 50} 次调用")
        return "\n".join(lines)


def inject_tracer(code: str) -> str:
    """将调用追踪代码注入到用户代码开头，返回包装后的代码。
    用于 subprocess 沙箱中，让子进程自己输出调用轨迹 JSON。
    """
    wrapper = '''
import sys, json, time
_tracer_calls = []
_tracer_stack = []
def _tracer(frame, event, arg):
    try:
        fn = frame.f_code.co_filename
        if fn.startswith("<") or "/lib/" in fn or "python3" in fn:
            return None if event == "call" else _tracer
        if event == "call":
            _tracer_stack.append({"func": frame.f_code.co_name, "line": frame.f_lineno, "t": time.time()})
        elif event == "return" and _tracer_stack:
            c = _tracer_stack.pop()
            c["ret"] = repr(arg)[:100] if arg is not None else None
            c["ms"] = round((time.time() - c["t"]) * 1000, 1)
            del c["t"]
            _tracer_calls.append(c)
    except Exception:
        pass
    return _tracer
sys.settrace(_tracer)
try:
'''
    # 缩进用户代码
    indented_code = "\n".join("    " + line for line in code.splitlines())
    footer = '''
finally:
    sys.settrace(None)
    print("\\n===SANDBOX_TRACE===")
    print(json.dumps({"calls": _tracer_calls}, ensure_ascii=False))
    print("===END_TRACE===")
'''
    return wrapper + indented_code + footer


def parse_trace_output(stdout: str) -> Optional[Dict[str, Any]]:
    """从 subprocess 沙箱的 stdout 中解析调用轨迹 JSON。"""
    marker_start = "===SANDBOX_TRACE==="
    marker_end = "===END_TRACE==="
    if marker_start not in stdout or marker_end not in stdout:
        return None
    try:
        start = stdout.index(marker_start) + len(marker_start)
        end = stdout.index(marker_end, start)
        json_str = stdout[start:end].strip()
        return json.loads(json_str)
    except (ValueError, json.JSONDecodeError):
        return None
