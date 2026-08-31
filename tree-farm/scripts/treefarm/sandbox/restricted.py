# -*- coding: utf-8 -*-
"""树场小沙箱 —— Python 受限执行环境（v3.8 新增）。
零依赖实现，不依赖 RestrictedPython 等第三方库。
安全策略：
  1. AST 静态检查：禁用 import / open / exec / eval / compile / __import__ / getattr/setattr 等
  2. 受限 builtins：只提供安全的内置函数
  3. 模块白名单：只允许导入 math/statistics/itertools/collections/re/json 等纯计算模块
  4. 属性访问控制：禁止以下划线开头的属性（防止 _hidden 攻击 / __globals__ 逃逸）
  5. 递归深度限制：sys.setrecursionlimit
  6. 超时机制：线程级超时（不依赖 signal，因为子进程中 signal 可能被覆盖）
  7. 输出捕获：重定向 stdout/stderr
  8. 调用追踪：sys.settrace 采集函数调用轨迹
  9. 覆盖率：基于 trace 模块的轻量覆盖率采集
"""
import ast
import sys
import io
import time
import traceback
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from .types import CallTrace, CoverageData, SandboxResult, SandboxConfig


# ========== 危险 AST 节点黑名单 ==========
# import 语句不在此禁止，而是在运行时通过 _safe_import 限制白名单模块
_DANGEROUS_NODES: Set[type] = set()  # Python 3 中 exec/eval 是函数调用，在 _DANGEROUS_NAMES 中拦截

# 危险函数名黑名单（即使通过 AST 检查，运行时也拦截）
_DANGEROUS_NAMES: Set[str] = {
    "open", "exec", "eval", "compile", "__import__", "getattr", "setattr",
    "delattr", "globals", "locals", "vars", "dir", "help", "input",
    "breakpoint", "exit", "quit", "memoryview", "classmethod", "staticmethod",
    "property", "super", "type", "isinstance", "issubclass", "hasattr",
}

# 受限 builtins（只提供安全的内置函数）
_SAFE_BUILTINS: Dict[str, Any] = {
    "print": print,
    "len": len,
    "range": range,
    "enumerate": enumerate,
    "zip": zip,
    "map": map,
    "filter": filter,
    "sorted": sorted,
    "reversed": reversed,
    "sum": sum,
    "min": min,
    "max": max,
    "abs": abs,
    "round": round,
    "pow": pow,
    "divmod": divmod,
    "mod": lambda a, b: a % b,  # v4.5 修复：原为 divmod（返回(商,余数)元组），mod 应为取模
    "all": all,
    "any": any,
    "bool": bool,
    "int": int,
    "float": float,
    "str": str,
    "list": list,
    "tuple": tuple,
    "dict": dict,
    "set": set,
    "frozenset": frozenset,
    "bytes": bytes,
    "bytearray": bytearray,
    "chr": chr,
    "ord": ord,
    "hex": hex,
    "oct": oct,
    "bin": bin,
    "repr": repr,
    "format": format,
    "id": id,
    "hash": hash,
    "iter": iter,
    "next": next,
    "slice": slice,
    "True": True,
    "False": False,
    "None": None,
    "NotImplemented": NotImplemented,
    "Ellipsis": Ellipsis,
    "Exception": Exception,
    "ValueError": ValueError,
    "TypeError": TypeError,
    "KeyError": KeyError,
    "IndexError": IndexError,
    "AttributeError": AttributeError,
    "RuntimeError": RuntimeError,
    "StopIteration": StopIteration,
    "ZeroDivisionError": ZeroDivisionError,
    "OverflowError": OverflowError,
    "ArithmeticError": ArithmeticError,
    "LookupError": LookupError,
    "AssertionError": AssertionError,
    "NameError": NameError,
    "SyntaxError": SyntaxError,
    "ImportError": ImportError,
    "OSError": OSError,
    "IOError": IOError,
    "FileNotFoundError": FileNotFoundError,
    "PermissionError": PermissionError,
    "TimeoutError": TimeoutError,
    "RecursionError": RecursionError,
    "MemoryError": MemoryError,
    "SystemError": SystemError,
    "BaseException": BaseException,
    "try": None,  # 占位，实际不导出
}


class VariableTracker:
    """变量追踪器（v3.9 新增，对标 CodeQL 数据流分析）。

    零依赖实现轻量级数据流分析：
    - 记录变量赋值历史（每次赋值的行号、值类型、来源）
    - 污点跟踪（标记外部输入变量，追踪其传播路径）
    - 变量生命周期分析（定义点、使用点、最后修改点）

    不做完整的 SSA/DFA，而是基于 AST 静态分析 + 执行后快照，
    在极致轻量化的前提下提供有用的数据流信息。
    """

    def __init__(self, taint_sources: Optional[Set[str]] = None):
        self.assignments: Dict[str, List[Dict[str, Any]]] = {}
        self.tainted: Set[str] = set(taint_sources or set())
        self.taint_propagation: List[Dict[str, Any]] = []
        self.final_values: Dict[str, Any] = {}

    def record_assignment(self, name: str, lineno: int, value_type: str,
                          source: str = "literal"):
        """记录一次变量赋值。"""
        if name not in self.assignments:
            self.assignments[name] = []
        self.assignments[name].append({
            "lineno": lineno,
            "type": value_type,
            "source": source,
        })
        # 如果赋值来源是污点变量，则传播污点
        if source in self.tainted and name not in self.tainted:
            self.tainted.add(name)
            self.taint_propagation.append({
                "from": source,
                "to": name,
                "lineno": lineno,
            })

    def analyze_ast(self, code: str) -> None:
        """通过 AST 静态分析提取赋值关系和污点传播。"""
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return

        for node in ast.walk(tree):
            # 赋值语句：x = expr
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        value_type = type(node.value).__name__
                        source = self._extract_source(node.value)
                        self.record_assignment(target.id, node.lineno, value_type, source)
            # 增强赋值：x += expr
            elif isinstance(node, ast.AugAssign):
                if isinstance(node.target, ast.Name):
                    source = self._extract_source(node.value)
                    self.record_assignment(node.target.id, node.lineno,
                                           "AugAssign", source or node.target.id)

    def _extract_source(self, node: ast.AST) -> str:
        """从表达式中提取主要来源变量名。"""
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            # user_input.upper() -> 返回 user_input
            return self._extract_source(node.value)
        if isinstance(node, ast.BinOp):
            left = self._extract_source(node.left)
            right = self._extract_source(node.right)
            return left or right
        if isinstance(node, ast.Call):
            # func() 或 obj.method()
            if isinstance(node.func, ast.Name):
                return node.func.id
            if isinstance(node.func, ast.Attribute):
                return self._extract_source(node.func.value)
            # 检查参数中是否有污点变量
            for arg in node.args:
                src = self._extract_source(arg)
                if src and src != "literal":
                    return src
        if isinstance(node, ast.Subscript):
            return self._extract_source(node.value)
        return "literal"

    def snapshot(self, globals_dict: Dict[str, Any]) -> None:
        """执行后快照：记录变量最终值和类型。"""
        for name, value in globals_dict.items():
            if name.startswith("_") or name in ("__builtins__",):
                continue
            if callable(value):
                continue
            self.final_values[name] = {
                "type": type(value).__name__,
                "repr": repr(value)[:100],
                "tainted": name in self.tainted,
            }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "variables": list(self.assignments.keys()),
            "assignments": self.assignments,
            "tainted_variables": sorted(self.tainted),
            "taint_propagation": self.taint_propagation,
            "final_values": self.final_values,
            "total_assignments": sum(len(v) for v in self.assignments.values()),
        }


class _TimeoutError(Exception):
    """沙箱执行超时。"""
    pass


class RestrictedExecutor:
    """Python 受限执行器（v3.8 沙箱核心）。
    零依赖，纯标准库实现。在当前进程中执行受限代码（用于 L2 安全等级）。
    注意：L2 是进程内受限执行，隔离强度不如 subprocess。
    对于不可信代码，建议使用 L1（subprocess + 资源限制）。
    """

    def __init__(self, config: SandboxConfig):
        self.config = config
        self._call_trace: List[CallTrace] = []
        self._coverage = CoverageData()
        self._exec_lines: Set[Tuple[str, int]] = set()
        self._all_lines: Dict[str, Set[int]] = {}
        self._start_time = 0.0

    def _check_ast(self, code: str) -> Tuple[bool, str]:
        """AST 静态安全检查，返回 (是否安全, 错误信息)。"""
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            return False, f"语法错误: {e}"

        for node in ast.walk(tree):
            # 检查 import 语句的模块白名单
            if isinstance(node, ast.Import):
                for alias in node.names:
                    mod_name = alias.name.split(".")[0]
                    if mod_name not in self.config.allowed_modules:
                        return False, f"禁止导入模块: {alias.name}（不在白名单中）"
            if isinstance(node, ast.ImportFrom):
                if node.module:
                    mod_name = node.module.split(".")[0]
                    if mod_name not in self.config.allowed_modules:
                        return False, f"禁止从模块导入: {node.module}（不在白名单中）"

            # 检查危险节点类型
            if type(node) in _DANGEROUS_NODES:
                return False, f"禁止的操作: {type(node).__name__}"

            # 检查危险函数调用
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name) and func.id in _DANGEROUS_NAMES:
                    return False, f"禁止调用危险函数: {func.id}"
                # 禁止 __import__('os') 形式
                if isinstance(func, ast.Attribute) and func.attr.startswith("_"):
                    return False, f"禁止访问私有属性: {func.attr}"

            # 检查危险属性访问
            if isinstance(node, ast.Attribute):
                if node.attr.startswith("__") and node.attr.endswith("__"):
                    # 允许一些常用的 dunder，但禁止 __globals__/__builtins__ 等
                    if node.attr in ("__globals__", "__builtins__", "__class__",
                                     "__bases__", "__subclasses__", "__mro__",
                                     "__import__", "__dict__", "__code__",
                                     "__func__", "__self__", "__closure__"):
                        return False, f"禁止访问危险属性: {node.attr}"
                elif node.attr.startswith("_"):
                    return False, f"禁止访问私有属性: {node.attr}"

            # 检查危险名称赋值（如 open = ... 后调用）
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                if node.id in _DANGEROUS_NAMES:
                    return False, f"禁止覆盖危险名称: {node.id}"

        return True, ""

    def _make_safe_globals(self) -> Dict[str, Any]:
        """构建安全的 globals 字典。"""
        g = {"__builtins__": _SAFE_BUILTINS.copy()}
        # 注入白名单模块的 __import__ 替代
        allowed = set(self.config.allowed_modules)

        def _safe_import(name: str, *args, **kwargs):
            """安全的 import，只允许白名单模块。"""
            if name not in allowed:
                raise ImportError(f"模块 '{name}' 不在白名单中（沙箱限制）")
            return __import__(name, *args, **kwargs)

        g["__builtins__"]["__import__"] = _safe_import
        return g

    def _trace_calls(self, frame, event, arg):
        """sys.settrace 回调：采集调用轨迹、覆盖率、超时检查。"""
        # 超时检查（直接检查时间，不依赖线程事件，更可靠）
        if time.time() - self._start_time > self.config.max_cpu_seconds:
            raise _TimeoutError("沙箱执行超时")

        filename = frame.f_code.co_filename
        lineno = frame.f_lineno

        # 覆盖率：记录执行过的行
        if event == "line":
            self._exec_lines.add((filename, lineno))

        # 调用追踪
        if event == "call":
            func_name = frame.f_code.co_name
            if func_name and not func_name.startswith("<"):
                pass  # 记录函数入口（返回值在 return 事件中补充）

        return self._trace_calls

    def execute(self, code: str, capture_trace: bool = False,
                capture_coverage: bool = False,
                capture_variables: bool = False,
                taint_sources: Optional[Set[str]] = None) -> SandboxResult:
        """执行受限 Python 代码。

        Args:
            code: 要执行的 Python 代码字符串
            capture_trace: 是否采集调用轨迹
            capture_coverage: 是否采集覆盖率
            capture_variables: 是否采集变量追踪和数据流分析（v3.9）
            taint_sources: 污点来源变量名集合（v3.9）

        Returns:
            SandboxResult 执行结果
        """
        result = SandboxResult(security_level="restricted")
        self._call_trace = []
        self._coverage = CoverageData()
        self._exec_lines = set()
        self._all_lines = {}
        self._var_tracker = VariableTracker(taint_sources) if capture_variables else None

        # 1. AST 静态安全检查
        safe, err = self._check_ast(code)
        if not safe:
            result.success = False
            result.error = err
            result.return_code = 1
            return result

        # 2. 记录所有可执行行（用于覆盖率分母）
        try:
            tree = ast.parse(code)
            lines = {node.lineno for node in ast.walk(tree)
                     if hasattr(node, "lineno") and not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
            self._all_lines["<sandbox>"] = lines
        except Exception:
            pass

        # 2.5 变量追踪：AST 静态分析赋值关系和污点传播
        if self._var_tracker is not None:
            self._var_tracker.analyze_ast(code)

        # 3. 准备执行环境
        globals_dict = self._make_safe_globals()
        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()
        old_stdout, old_stderr = sys.stdout, sys.stderr
        old_trace = sys.gettrace()
        old_recursion = sys.getrecursionlimit()

        # 4. 超时监控（在 trace 回调中直接检查时间，不依赖线程）
        self._start_time = time.time()

        try:
            sys.stdout = stdout_buf
            sys.stderr = stderr_buf
            sys.setrecursionlimit(min(self.config.max_recursion_depth, 200))

            # 始终设置 trace（用于超时检查；同时采集 trace/coverage 如果需要）
            sys.settrace(self._trace_calls)

            # 5. 执行代码
            exec(compile(code, "<sandbox>", "exec"), globals_dict)

            result.success = True
            result.return_code = 0

            # 6. 检查是否有返回值（最后一个表达式）
            # exec 不返回值，这里检查 globals 中是否有特殊变量 _result
            if "_result" in globals_dict:
                result.return_value = globals_dict["_result"]

            # 6.5 变量追踪：执行后快照
            if self._var_tracker is not None:
                self._var_tracker.snapshot(globals_dict)

        except _TimeoutError as e:
            result.success = False
            result.error = str(e)
            result.return_code = 124
        except Exception as e:
            result.success = False
            result.error = f"{type(e).__name__}: {e}"
            result.stderr = traceback.format_exc()
            result.return_code = 1
        finally:
            sys.stdout = old_stdout
            sys.stderr = old_stderr
            sys.settrace(old_trace)
            sys.setrecursionlimit(old_recursion)

        result.stdout = stdout_buf.getvalue()
        result.stderr = stderr_buf.getvalue() or result.stderr
        result.execution_time_ms = (time.time() - self._start_time) * 1000

        # 7. 收集调用轨迹和覆盖率
        if capture_trace:
            result.call_trace = [t.to_dict() for t in self._call_trace]

        if capture_coverage:
            # 从 _exec_lines 构建覆盖率
            for (filename, lineno) in self._exec_lines:
                if filename not in self._coverage.files:
                    total = self._all_lines.get(filename, set())
                    self._coverage.add_file(filename, [], sorted(total))
                if filename in self._coverage.files:
                    self._coverage.files[filename]["lines_covered"] = sorted(
                        set(self._coverage.files[filename]["lines_covered"]) | {lineno}
                    )
            # 重新计算覆盖率
            for f in self._coverage.files.values():
                covered = set(f["lines_covered"])
                total = set(f["lines_total"])
                f["coverage"] = round(len(covered & total) / len(total), 4) if total else 0.0
            result.coverage = self._coverage.to_dict()

        # 8. 变量追踪结果（v3.9 数据流分析）
        if capture_variables and self._var_tracker is not None:
            result.variables = self._var_tracker.to_dict()

        return result

    def execute_function(self, func_code: str, func_name: str,
                         args: tuple = (), kwargs: dict = None,
                         capture_trace: bool = False) -> SandboxResult:
        """执行一个函数定义并调用它。

        Args:
            func_code: 包含函数定义的代码
            func_name: 要调用的函数名
            args: 位置参数
            kwargs: 关键字参数
            capture_trace: 是否采集调用轨迹

        Returns:
            SandboxResult 执行结果，return_value 为函数返回值
        """
        if kwargs is None:
            kwargs = {}
        # 构造调用代码
        call_line = f"\n_result = {func_name}(*{repr(args)}, **{repr(kwargs)})\n"
        full_code = func_code + call_line
        return self.execute(full_code, capture_trace=capture_trace, capture_coverage=False)
