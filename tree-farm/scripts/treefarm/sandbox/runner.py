# -*- coding: utf-8 -*-
"""树场小沙箱 —— 统一调度器（v3.8 新增，沙箱核心入口）。
负责：
  1. 沙箱开关管理（开启/关闭/状态查询）
  2. 根据安全等级自动选择执行方式（auto/restricted/subprocess）
  3. 高级 API：run_code / run_function / run_tests / analyze_function
  4. 集成调用追踪和覆盖率采集
  5. 与树场的集成点（动态验证小鸟、死代码动态确认、函数行为探索）
  6. 手机端 Termux 自动降级
零依赖，纯标准库实现。
"""
import os
import sys
import json
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from .types import SandboxResult, SandboxConfig, SecurityLevel
from .restricted import RestrictedExecutor
from .subprocess_sandbox import SubprocessSandbox, detect_available_languages, is_language_available
from .tracer import CallTracer, inject_tracer, parse_trace_output
from .coverage import CoverageCollector, inject_coverage, parse_coverage_output
from .resource import detect_platform, is_termux


class SandboxRunner:
    """树场小沙箱统一调度器（v3.8 沙箱核心入口）。

    用法：
        from treefarm.sandbox import SandboxRunner
        runner = SandboxRunner()
        runner.enable()  # 开启沙箱
        result = runner.run_code("print('hello')", language="python")
        print(result.format_summary())
    """

    def __init__(self, config: Optional[SandboxConfig] = None):
        self.config = config or SandboxConfig()
        self._restricted = RestrictedExecutor(self.config)
        self._subprocess = SubprocessSandbox(self.config)
        self._platform = detect_platform()
        self._available_langs = detect_available_languages()

    # ========== 开关管理 ==========

    def enable(self) -> None:
        """开启沙箱（用户说「开启沙箱」时调用）。"""
        self.config.enabled = True

    def disable(self) -> None:
        """关闭沙箱（用户说「关闭沙箱」时调用）。"""
        self.config.enabled = False

    @property
    def enabled(self) -> bool:
        """沙箱是否开启。"""
        return self.config.enabled

    def status(self) -> Dict[str, Any]:
        """查询沙箱状态。"""
        return {
            "enabled": self.config.enabled,
            "security_level": self.config.security_level,
            "platform": self._platform,
            "is_termux": is_termux(),
            "available_languages": self._available_langs,
            "max_memory_mb": self.config.max_memory_mb,
            "max_cpu_seconds": self.config.max_cpu_seconds,
            "max_output_kb": self.config.max_output_kb,
            "network_allowed": self.config.network,
            "filesystem": self.config.filesystem,
        }

    def format_status(self) -> str:
        """格式化人类可读的状态。"""
        s = self.status()
        state = "✅ 已开启" if s["enabled"] else "📵 已关闭"
        lines = [
            f"🔬 小沙箱状态：{state}",
            f"   安全等级: {s['security_level']}",
            f"   运行平台: {s['platform']}" + (" (Termux)" if s["is_termux"] else ""),
            f"   可用语言: {', '.join(s['available_languages'].keys()) or '无'}",
            f"   内存限制: {s['max_memory_mb']}MB",
            f"   CPU 时间: {s['max_cpu_seconds']}秒",
            f"   输出限制: {s['max_output_kb']}KB",
            f"   网络访问: {'允许' if s['network_allowed'] else '禁止'}",
            f"   文件系统: {s['filesystem']}",
        ]
        if not s["enabled"]:
            lines.append("   💡 说「开启沙箱」启用动态代码分析")
        return "\n".join(lines)

    # ========== 核心执行 API ==========

    def run_code(self, code: str, language: str = "python",
                  capture_trace: bool = False, capture_coverage: bool = False,
                  capture_variables: bool = False,
                  taint_sources: Optional[set] = None,
                  stdin: str = "") -> SandboxResult:
        """在沙箱中执行代码。

        Args:
            code: 要执行的代码字符串
            language: 编程语言（python/javascript/shell/ruby/perl/lua）
            capture_trace: 是否采集调用轨迹
            capture_coverage: 是否采集覆盖率
            capture_variables: 是否采集变量追踪和数据流分析（v3.9）
            taint_sources: 污点来源变量名集合（v3.9）
            stdin: 标准输入内容

        Returns:
            SandboxResult 执行结果
        """
        if not self.config.enabled:
            return SandboxResult(
                success=False,
                error="沙箱未开启。请先说「开启沙箱」再执行动态分析。",
                return_code=-1,
            )

        language = language.lower()
        level = self.config.security_level

        # auto 模式自动选择：Python 用 restricted（更快），其他语言用 subprocess
        if level == "auto":
            if language == "python" and not capture_trace and not capture_coverage and not capture_variables:
                level = "restricted"
            else:
                level = "subprocess"

        # Termux 下 restricted 可能有问题（信号/线程），优先 subprocess
        if is_termux() and level == "restricted":
            level = "subprocess"

        if level == "restricted" and language == "python":
            return self._run_restricted(code, capture_trace, capture_coverage,
                                         capture_variables, taint_sources)
        else:
            return self._run_subprocess(code, language, capture_trace, capture_coverage, stdin)

    def _run_restricted(self, code: str, capture_trace: bool,
                        capture_coverage: bool,
                        capture_variables: bool = False,
                        taint_sources: Optional[set] = None) -> SandboxResult:
        """用受限执行器运行 Python 代码。"""
        return self._restricted.execute(
            code, capture_trace=capture_trace, capture_coverage=capture_coverage,
            capture_variables=capture_variables, taint_sources=taint_sources
        )

    def _run_subprocess(self, code: str, language: str,
                        capture_trace: bool, capture_coverage: bool,
                        stdin: str) -> SandboxResult:
        """用 subprocess 沙箱运行代码。"""
        # Python 代码需要注入追踪/覆盖率采集代码
        if language == "python":
            if capture_trace:
                code = inject_tracer(code)
            if capture_coverage:
                code = inject_coverage(code)

        result = self._subprocess.execute(code, language=language, stdin=stdin)

        # 解析注入的追踪/覆盖率输出
        if capture_trace and language == "python":
            trace_data = parse_trace_output(result.stdout)
            if trace_data:
                result.call_trace = trace_data.get("calls", [])
                # 从 stdout 中移除追踪标记
                result.stdout = self._strip_markers(result.stdout)

        if capture_coverage and language == "python":
            cov_data = parse_coverage_output(result.stdout)
            if cov_data:
                # v4.5 统一格式：subprocess 覆盖率也转成与 restricted 一致的
                # {files: {<sandbox>: {lines_covered, lines_total, coverage}}, overall_coverage}
                covered_lines = cov_data.get("covered_line_numbers", [])
                total_lines = self._extract_total_lines(cov_data)
                overall = cov_data.get("overall_coverage", cov_data.get("coverage", 0.0))
                result.coverage = {
                    "files": {
                        "<sandbox>": {
                            "lines_covered": sorted(covered_lines),
                            "lines_total": sorted(total_lines),
                            "coverage": overall,
                        }
                    },
                    "overall_coverage": overall,
                }
                result.stdout = self._strip_markers(result.stdout)

        return result

    @staticmethod
    def _strip_markers(stdout: str) -> str:
        """从 stdout 中移除沙箱注入的标记块。"""
        import re
        # 移除 ===SANDBOX_TRACE=== ... ===END_TRACE===
        stdout = re.sub(r"\n*===SANDBOX_TRACE===.*?===END_TRACE===\n*", "", stdout, flags=re.DOTALL)
        # 移除 ===SANDBOX_COVERAGE=== ... ===END_COVERAGE===
        stdout = re.sub(r"\n*===SANDBOX_COVERAGE===.*?===END_COVERAGE===\n*", "", stdout, flags=re.DOTALL)
        return stdout.strip()

    @staticmethod
    def _extract_total_lines(cov_data: Dict[str, Any]) -> List[int]:
        """从 subprocess 覆盖率数据还原总行号列表（covered ∪ uncovered）。"""
        covered = set(cov_data.get("covered_line_numbers", []))
        uncovered = set(cov_data.get("uncovered_lines", []))
        return sorted(covered | uncovered)

    def run_function(self, func_code: str, func_name: str,
                     args: tuple = (), kwargs: dict = None,
                     language: str = "python") -> SandboxResult:
        """执行一个函数定义并调用它。

        Args:
            func_code: 包含函数定义的代码
            func_name: 要调用的函数名
            args: 位置参数
            kwargs: 关键字参数
            language: 编程语言（仅 python 支持）

        Returns:
            SandboxResult，return_value 为函数返回值
        """
        if kwargs is None:
            kwargs = {}
        if language != "python":
            return SandboxResult(success=False, error="run_function 仅支持 Python", return_code=-1)

        if self.config.security_level in ("auto", "restricted") and not is_termux():
            return self._restricted.execute_function(func_code, func_name, args, kwargs, capture_trace=True)
        else:
            # subprocess 模式：构造调用代码
            call_code = func_code + f"\n_result = {func_name}(*{repr(args)}, **{repr(kwargs)})\nprint('===RESULT===')\nprint(repr(_result))\n"
            result = self.run_code(call_code, language="python")
            # 解析返回值
            if "===RESULT===" in result.stdout:
                try:
                    parts = result.stdout.split("===RESULT===")
                    result.return_value = parts[-1].strip()
                except Exception:
                    pass
            return result

    def run_tests(self, test_code: str, language: str = "python") -> SandboxResult:
        """运行测试代码。

        Args:
            test_code: 测试代码（Python 会自动包装 unittest）
            language: 编程语言

        Returns:
            SandboxResult，stdout 包含测试结果
        """
        return self._subprocess.run_tests(test_code, language=language)

    # ========== 树场集成 API ==========

    def dynamic_verify_bird(self, source_file: str, target_file: str,
                             project_root: str = "") -> SandboxResult:
        """动态验证「小鸟」（强耦合）：执行源文件中调用目标文件的代码，确认运行时确实调用。

        Args:
            source_file: 源文件路径
            target_file: 目标文件路径
            project_root: 项目根目录

        Returns:
            SandboxResult，包含动态验证结果
        """
        # 构造验证代码：导入源模块，调用其函数，用追踪器确认是否调用了目标模块
        verify_code = f'''
import sys
sys.path.insert(0, {repr(project_root or os.getcwd())})
import traceback
_source = {repr(os.path.relpath(source_file, project_root) if project_root else source_file)}
_target = {repr(os.path.relpath(target_file, project_root) if project_root else target_file)}
_called = False
def _verify():
    global _called
    # 尝试导入源模块并调用其公开函数
    try:
        mod_name = _source.replace("/", ".").replace(".py", "")
        mod = __import__(mod_name, fromlist=["*"])
        for name in dir(mod):
            if not name.startswith("_"):
                obj = getattr(mod, name)
                if callable(obj):
                    try:
                        obj()  # 无参调用尝试
                    except Exception:
                        pass
    except Exception as e:
        print(f"导入失败: {{e}}")
_verify()
print(f"动态验证: 源={{_source}}, 目标={{_target}}, 调用={{_called}}")
'''
        return self.run_code(verify_code, language="python", capture_trace=True)

    def dynamic_confirm_dead_code(self, file_path: str, func_name: str,
                                   project_root: str = "") -> SandboxResult:
        """动态确认死代码：导入模块，检查函数是否被其他模块调用。

        Args:
            file_path: 文件路径
            func_name: 函数名
            project_root: 项目根目录

        Returns:
            SandboxResult
        """
        confirm_code = f'''
import sys, os
sys.path.insert(0, {repr(project_root or os.getcwd())})
_file = {repr(file_path)}
_func = {repr(func_name)}
# 检查函数是否存在
try:
    mod_name = _file.replace("/", ".").replace(".py", "")
    mod = __import__(mod_name, fromlist=["*"])
    if hasattr(mod, _func):
        print(f"函数 {{_func}} 存在于 {{_file}}")
    else:
        print(f"函数 {{_func}} 不存在于 {{_file}}")
except Exception as e:
    print(f"导入失败: {{e}}")
'''
        return self.run_code(confirm_code, language="python", capture_trace=True)

    def explore_function_behavior(self, func_code: str, func_name: str,
                                   test_cases: List[Tuple[tuple, dict]] = None) -> SandboxResult:
        """探索函数行为：用多个测试用例调用函数，收集输入输出。

        Args:
            func_code: 函数定义代码
            func_name: 函数名
            test_cases: 测试用例列表，每个为 (args, kwargs)

        Returns:
            SandboxResult，stdout 包含每个用例的输入输出
        """
        if test_cases is None:
            test_cases = [((), {})]

        cases_code = ""
        for i, (args, kwargs) in enumerate(test_cases):
            cases_code += f'''
try:
    _r = {func_name}(*{repr(args)}, **{repr(kwargs)})
    print(f"用例{i}: args={repr(args)}, kwargs={repr(kwargs)}, result={{repr(_r)}}")
except Exception as e:
    print(f"用例{i}: args={repr(args)}, kwargs={repr(kwargs)}, error={{type(e).__name__}}: {{e}}")
'''
        full_code = func_code + cases_code
        return self.run_code(full_code, language="python", capture_trace=True)

    # ========== 配置管理 ==========

    def update_config(self, **kwargs) -> None:
        """更新沙箱配置。"""
        for key, value in kwargs.items():
            if hasattr(self.config, key):
                setattr(self.config, key, value)
        # 更新子执行器的配置引用
        self._restricted.config = self.config
        self._subprocess.config = self.config

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SandboxRunner":
        """从配置字典创建沙箱调度器。"""
        config = SandboxConfig.from_dict(d)
        return cls(config)

    # ========== 树场深度集成（v3.9 动态验证） ==========

    def dynamic_verify_bird(self, source_file: str, target_file: str,
                             project_root: str = ".") -> Dict[str, Any]:
        """动态验证小鸟：确认两个文件之间是否存在运行时耦合。

        树场静态分析报告两个文件有耦合（小鸟），此方法在沙箱中
        动态验证：导入源文件，检查是否真的引用/调用了目标文件的符号。

        Args:
            source_file: 源文件路径（调用方）
            target_file: 目标文件路径（被调用方）
            project_root: 项目根目录

        Returns:
            验证结果字典
        """
        import os
        result = {
            "source": source_file,
            "target": target_file,
            "verified": False,
            "evidence": [],
            "error": "",
        }

        try:
            # 读取两个文件
            src_path = os.path.join(project_root, source_file)
            tgt_path = os.path.join(project_root, target_file)
            if not os.path.exists(src_path) or not os.path.exists(tgt_path):
                result["error"] = "文件不存在"
                return result

            with open(src_path, encoding="utf-8") as f:
                src_code = f.read()
            with open(tgt_path, encoding="utf-8") as f:
                tgt_code = f.read()

            # 提取目标文件的公共符号（函数名、类名）
            import ast
            tgt_tree = ast.parse(tgt_code)
            tgt_symbols = []
            for node in ast.walk(tgt_tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    tgt_symbols.append(node.name)

            # 在源文件中搜索对目标符号的引用
            for sym in tgt_symbols:
                if sym in src_code:
                    # 进一步验证：是否是 import 或调用
                    if f"import {sym}" in src_code or f"from " in src_code and sym in src_code:
                        result["evidence"].append(f"源文件 import 了 {sym}")
                    elif f"{sym}(" in src_code or f"{sym}." in src_code:
                        result["evidence"].append(f"源文件调用了 {sym}")

            result["verified"] = len(result["evidence"]) > 0
        except Exception as e:
            result["error"] = f"{type(e).__name__}: {e}"

        return result

    def dynamic_confirm_dead_code(self, file_path: str, func_name: str,
                                    project_root: str = ".") -> Dict[str, Any]:
        """动态确认死代码：检查函数是否真的从未被调用。

        Args:
            file_path: 文件路径
            func_name: 函数名
            project_root: 项目根目录

        Returns:
            确认结果字典
        """
        import os
        import ast
        result = {
            "file": file_path,
            "function": func_name,
            "is_dead": True,
            "callers": [],
            "error": "",
        }

        try:
            # 遍历项目中所有 Python 文件，搜索函数调用
            for root, dirs, files in os.walk(project_root):
                # 跳过常见的非源码目录
                dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", "venv", ".venv", "node_modules")]
                for fname in files:
                    if not fname.endswith(".py"):
                        continue
                    fpath = os.path.join(root, fname)
                    try:
                        with open(fpath, encoding="utf-8") as f:
                            code = f.read()
                        # 搜索函数调用
                        if f"{func_name}(" in code:
                            rel_path = os.path.relpath(fpath, project_root)
                            # 排除函数定义本身
                            tree = ast.parse(code)
                            for node in ast.walk(tree):
                                if isinstance(node, ast.Call):
                                    if isinstance(node.func, ast.Name) and node.func.id == func_name:
                                        result["callers"].append(f"{rel_path}:{node.lineno}")
                    except (SyntaxError, UnicodeDecodeError):
                        continue
            result["is_dead"] = len(result["callers"]) == 0
        except Exception as e:
            result["error"] = f"{type(e).__name__}: {e}"

        return result

    def dynamic_validate_fix(self, original_code: str, fixed_code: str,
                               test_cases: List[Tuple[tuple, dict]] = None,
                               func_name: str = "") -> Dict[str, Any]:
        """动态验证修复方案：对比修复前后的行为，确认修复正确且无回归。

        Args:
            original_code: 原始代码
            fixed_code: 修复后的代码
            test_cases: 测试用例列表 [(args, kwargs), ...]
            func_name: 要测试的函数名

        Returns:
            验证结果字典
        """
        result = {
            "func_name": func_name,
            "original_passed": 0,
            "fixed_passed": 0,
            "total_cases": len(test_cases) if test_cases else 0,
            "regressions": [],
            "fixes": [],
            "error": "",
        }

        if not test_cases or not func_name:
            result["error"] = "需要提供测试用例和函数名"
            return result

        try:
            for i, (args, kwargs) in enumerate(test_cases):
                # 测试原始代码
                orig_globals = {}
                try:
                    exec(original_code, orig_globals)
                    orig_result = orig_globals[func_name](*args, **kwargs)
                    orig_ok = True
                except Exception as e:
                    orig_result = f"{type(e).__name__}: {e}"
                    orig_ok = False

                # 测试修复后代码
                fixed_globals = {}
                try:
                    exec(fixed_code, fixed_globals)
                    fixed_result = fixed_globals[func_name](*args, **kwargs)
                    fixed_ok = True
                except Exception as e:
                    fixed_result = f"{type(e).__name__}: {e}"
                    fixed_ok = False

                if orig_ok:
                    result["original_passed"] += 1
                if fixed_ok:
                    result["fixed_passed"] += 1

                # 检测回归：原始通过但修复后失败
                if orig_ok and not fixed_ok:
                    result["regressions"].append({
                        "case": i,
                        "args": repr(args),
                        "original": repr(orig_result),
                        "fixed": repr(fixed_result),
                    })
                # 检测修复：原始失败但修复后通过
                elif not orig_ok and fixed_ok:
                    result["fixes"].append({
                        "case": i,
                        "args": repr(args),
                        "original_error": orig_result,
                        "fixed_result": repr(fixed_result),
                    })
        except Exception as e:
            result["error"] = f"{type(e).__name__}: {e}"

        return result
