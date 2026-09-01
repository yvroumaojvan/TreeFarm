# -*- coding: utf-8 -*-
"""
树场小沙箱 v3.8 测试套件（unittest，零依赖）
运行：
  cd tree-farm/scripts && python3 -m unittest tests.test_sandbox -v
  或直接 python3 tests/test_sandbox.py
覆盖：
  1. 数据类型（SandboxResult / SandboxConfig / CallTrace / CoverageData）
  2. 资源限制（ResourceLimiter / TimeoutKiller / MemoryMonitor / OutputLimiter / TempDir）
  3. 受限执行（AST 安全检查 / 正常执行 / 危险代码拦截 / 模块白名单）
  4. subprocess 沙箱（多语言执行 / 资源限制 / 超时 / 输出限制）
  5. 调用追踪（CallTracer / inject_tracer / parse_trace_output）
  6. 覆盖率（CoverageCollector / inject_coverage / parse_coverage_output）
  7. 统一调度器（开关管理 / 自动选择安全等级 / run_code / run_function / run_tests）
"""
import os
import sys
import json
import time
import unittest
import tempfile
import shutil

# 确保能导入 treefarm 包
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from treefarm.sandbox import (
    SandboxResult, SandboxConfig, CallTrace, CoverageData, SecurityLevel,
    ResourceLimiter, TimeoutKiller, MemoryMonitor, OutputLimiter, TempDir,
    detect_platform, is_termux,
    RestrictedExecutor,
    SubprocessSandbox, detect_available_languages, is_language_available,
    CallTracer, inject_tracer, parse_trace_output,
    CoverageCollector, inject_coverage, parse_coverage_output,
    SandboxRunner,
)


# ========== 1. 数据类型测试 ==========
class TestTypes(unittest.TestCase):
    """数据类型测试。"""

    def test_sandbox_result_defaults(self):
        r = SandboxResult()
        self.assertFalse(r.success)
        self.assertEqual(r.return_code, -1)
        self.assertEqual(r.execution_time_ms, 0.0)
        self.assertEqual(r.call_trace, [])
        self.assertIsNone(r.coverage)

    def test_sandbox_result_to_dict(self):
        r = SandboxResult(success=True, stdout="hello", return_code=0)
        d = r.to_dict()
        self.assertTrue(d["success"])
        self.assertEqual(d["stdout"], "hello")
        self.assertEqual(d["return_code"], 0)

    def test_sandbox_result_format_summary(self):
        r = SandboxResult(success=True, stdout="test output", return_code=0,
                          execution_time_ms=123.4, memory_peak_kb=5678)
        s = r.format_summary()
        self.assertIn("成功", s)
        self.assertIn("123.4ms", s)
        self.assertIn("5678KB", s)

    def test_sandbox_config_defaults(self):
        c = SandboxConfig()
        self.assertFalse(c.enabled)
        self.assertEqual(c.max_memory_mb, 256)
        self.assertEqual(c.max_cpu_seconds, 10)
        self.assertEqual(c.max_output_kb, 64)
        self.assertFalse(c.network)
        self.assertEqual(c.security_level, "auto")
        self.assertIn("math", c.allowed_modules)

    def test_sandbox_config_from_dict(self):
        d = {"enabled": True, "max_memory_mb": 128, "max_cpu_seconds": 5,
             "unknown_field": "should_be_ignored"}
        c = SandboxConfig.from_dict(d)
        self.assertTrue(c.enabled)
        self.assertEqual(c.max_memory_mb, 128)
        self.assertEqual(c.max_cpu_seconds, 5)

    def test_call_trace(self):
        ct = CallTrace(func="test_func", args=[1, 2], return_value=3, lineno=10)
        d = ct.to_dict()
        self.assertEqual(d["func"], "test_func")
        self.assertEqual(d["args"], [1, 2])
        self.assertEqual(d["return_value"], 3)

    def test_coverage_data(self):
        cov = CoverageData()
        cov.add_file("/tmp/test.py", [1, 2, 3], [1, 2, 3, 4, 5])
        self.assertEqual(cov.overall_coverage, 0.6)
        d = cov.to_dict()
        self.assertIn("files", d)
        self.assertEqual(d["overall_coverage"], 0.6)

    def test_security_level_enum(self):
        self.assertEqual(SecurityLevel.AUTO.value, "auto")
        self.assertEqual(SecurityLevel.RESTRICTED.value, "restricted")
        self.assertEqual(SecurityLevel.SUBPROCESS.value, "subprocess")


# ========== 2. 资源限制测试 ==========
class TestResource(unittest.TestCase):
    """资源限制工具测试。"""

    def test_resource_limiter_available(self):
        rl = ResourceLimiter()
        # Linux 下应该可用
        if sys.platform.startswith("linux") or sys.platform == "darwin":
            self.assertTrue(rl.available)

    def test_timeout_killer(self):
        tk = TimeoutKiller(timeout_seconds=0.1)
        # 创建一个子进程测试
        import subprocess
        proc = subprocess.Popen(["sleep", "5"])
        tk.start(proc.pid)
        time.sleep(0.3)
        killed = tk.stop()
        proc.wait()
        self.assertTrue(killed)
        self.assertNotEqual(proc.returncode, 0)  # 被杀死

    def test_output_limiter(self):
        ol = OutputLimiter(max_kb=1)  # 1KB 限制
        # 模拟大量输出
        import io
        pipe = io.BytesIO(b"x" * 5000)
        result = ol.read_limited(pipe)
        self.assertTrue(ol.truncated)
        self.assertLessEqual(len(result), 1024)

    def test_temp_dir_context(self):
        with TempDir(prefix="test_sandbox_") as d:
            self.assertTrue(os.path.isdir(d))
            test_file = os.path.join(d, "test.txt")
            with open(test_file, "w") as f:
                f.write("hello")
            self.assertTrue(os.path.isfile(test_file))
        # 退出后应该被清理
        self.assertFalse(os.path.isdir(d))

    def test_temp_dir_manual_cleanup(self):
        td = TempDir(prefix="test_sandbox_", auto_cleanup=False)
        path = td.__enter__()
        self.assertTrue(os.path.isdir(path))
        td.cleanup()
        self.assertFalse(os.path.isdir(path))

    def test_detect_platform(self):
        p = detect_platform()
        self.assertIn(p, ("linux", "macos", "windows", "android", "unknown"))

    def test_memory_monitor(self):
        mm = MemoryMonitor(interval_ms=10)
        # 监控当前进程
        mm.start(os.getpid())
        time.sleep(0.1)
        peak = mm.stop()
        self.assertGreaterEqual(peak, 0)


# ========== 3. 受限执行测试 ==========
class TestRestrictedExecutor(unittest.TestCase):
    """Python 受限执行环境测试。"""

    def setUp(self):
        self.config = SandboxConfig(max_cpu_seconds=5, max_memory_mb=128)
        self.executor = RestrictedExecutor(self.config)

    def test_normal_execution(self):
        result = self.executor.execute("print('hello world')\nx = 1 + 2\nprint(x)")
        self.assertTrue(result.success)
        self.assertEqual(result.return_code, 0)
        self.assertIn("hello world", result.stdout)
        self.assertIn("3", result.stdout)

    def test_import_blocked(self):
        result = self.executor.execute("import os\nprint(os.getcwd())")
        self.assertFalse(result.success)
        self.assertIn("禁止", result.error)

    def test_open_blocked(self):
        result = self.executor.execute("f = open('/etc/passwd')\nprint(f.read())")
        self.assertFalse(result.success)
        self.assertIn("禁止", result.error)

    def test_eval_blocked(self):
        result = self.executor.execute("eval('__import__(\"os\")')")
        self.assertFalse(result.success)
        self.assertIn("禁止", result.error)

    def test_exec_blocked(self):
        result = self.executor.execute("exec('print(1)')")
        self.assertFalse(result.success)
        self.assertIn("禁止", result.error)

    def test_private_attribute_blocked(self):
        result = self.executor.execute("class A:\n    pass\na = A()\nprint(a.__class__)")
        self.assertFalse(result.success)
        self.assertIn("禁止", result.error)

    def test_dunder_globals_blocked(self):
        result = self.executor.execute("def f():\n    pass\nprint(f.__globals__)")
        self.assertFalse(result.success)

    def test_safe_module_allowed(self):
        result = self.executor.execute("import math\nprint(math.sqrt(16))")
        self.assertTrue(result.success)
        self.assertIn("4.0", result.stdout)

    def test_unsafe_module_blocked(self):
        result = self.executor.execute("import sys\nprint(sys.exit)")
        self.assertFalse(result.success)

    def test_syntax_error(self):
        result = self.executor.execute("def f(:\n    pass")
        self.assertFalse(result.success)
        self.assertIn("语法错误", result.error)

    def test_runtime_error(self):
        result = self.executor.execute("x = 1 / 0")
        self.assertFalse(result.success)
        self.assertIn("ZeroDivisionError", result.error)

    def test_infinite_loop_timeout(self):
        # 受限执行器用 trace 回调超时检查，测试是否能终止
        self.config.max_cpu_seconds = 0.5
        executor = RestrictedExecutor(self.config)
        result = executor.execute("while True:\n    pass")
        self.assertFalse(result.success)
        self.assertIn("超时", result.error)

    def test_return_value(self):
        result = self.executor.execute("_result = 42\nprint('done')")
        self.assertTrue(result.success)
        self.assertEqual(result.return_value, 42)

    def test_execute_function(self):
        func_code = "def add(a, b):\n    return a + b\n"
        result = self.executor.execute_function(func_code, "add", args=(3, 4))
        self.assertTrue(result.success)
        self.assertEqual(result.return_value, 7)

    def test_capture_trace(self):
        code = "def foo():\n    return 1\ndef bar():\n    return foo()\nbar()"
        result = self.executor.execute(code, capture_trace=True)
        self.assertTrue(result.success)
        # call_trace 可能为空（受限执行器的追踪实现），但不应该报错

    def test_capture_coverage(self):
        code = "def foo():\n    return 1\ndef bar():\n    return 2\nfoo()"
        result = self.executor.execute(code, capture_coverage=True)
        self.assertTrue(result.success)
        # 覆盖率数据可能不完整，但不应该报错


# ========== 4. subprocess 沙箱测试 ==========
class TestSubprocessSandbox(unittest.TestCase):
    """多语言 subprocess 沙箱测试。"""

    def setUp(self):
        self.config = SandboxConfig(max_cpu_seconds=10, max_memory_mb=256, max_output_kb=64)
        self.sandbox = SubprocessSandbox(self.config)

    def test_detect_available_languages(self):
        langs = detect_available_languages()
        self.assertIsInstance(langs, dict)
        # Python 应该可用
        self.assertIn("python", langs)

    def test_is_language_available(self):
        self.assertTrue(is_language_available("python"))
        self.assertFalse(is_language_available("nonexistent_lang"))

    def test_python_execution(self):
        result = self.sandbox.execute("print('hello from subprocess')", language="python")
        self.assertTrue(result.success)
        self.assertEqual(result.return_code, 0)
        self.assertIn("hello from subprocess", result.stdout)

    def test_python_error(self):
        result = self.sandbox.execute("raise ValueError('test error')", language="python")
        self.assertFalse(result.success)
        self.assertNotEqual(result.return_code, 0)
        self.assertIn("ValueError", result.stderr)

    def test_shell_execution(self):
        if is_language_available("shell"):
            result = self.sandbox.execute("echo 'hello shell'", language="shell")
            self.assertTrue(result.success)
            self.assertIn("hello shell", result.stdout)

    def test_javascript_execution(self):
        if is_language_available("javascript"):
            result = self.sandbox.execute("console.log('hello js')", language="javascript")
            self.assertTrue(result.success)
            self.assertIn("hello js", result.stdout)

    def test_timeout(self):
        self.config.max_cpu_seconds = 1
        sandbox = SubprocessSandbox(self.config)
        result = sandbox.execute("import time\ntime.sleep(10)", language="python")
        self.assertFalse(result.success)
        self.assertIn("超时", result.error)

    def test_output_limit(self):
        self.config.max_output_kb = 1
        sandbox = SubprocessSandbox(self.config)
        result = sandbox.execute("print('x' * 10000)", language="python")
        # 输出应该被截断
        self.assertLessEqual(len(result.stdout), 2048)  # 1KB * 2 容错

    def test_unsupported_language(self):
        result = self.sandbox.execute("test", language="nonexistent")
        self.assertFalse(result.success)
        self.assertIn("不可用", result.error)

    def test_run_tests_python(self):
        test_code = """
import unittest
class TestMath(unittest.TestCase):
    def test_add(self):
        self.assertEqual(1 + 1, 2)
    def test_multiply(self):
        self.assertEqual(2 * 3, 6)
"""
        result = self.sandbox.run_tests(test_code, language="python")
        self.assertTrue(result.success)
        # unittest 详细输出可能在 stdout 或 stderr
        output = result.stdout + result.stderr
        self.assertIn("test_add", output)
        self.assertIn("test_multiply", output)

    def test_memory_peak(self):
        result = self.sandbox.execute("x = [1] * 10000\nprint(len(x))", language="python")
        self.assertTrue(result.success)
        self.assertGreater(result.memory_peak_kb, 0)


# ========== 5. 调用追踪测试 ==========
class TestCallTracer(unittest.TestCase):
    """调用追踪器测试。"""

    def test_tracer_basic(self):
        tracer = CallTracer(max_calls=100)
        tracer.start()
        def foo():
            return 1
        def bar():
            return foo()
        bar()
        tracer.stop()
        d = tracer.to_dict()
        self.assertIn("calls", d)
        self.assertGreaterEqual(d["total_calls"], 0)

    def test_tracer_truncation(self):
        tracer = CallTracer(max_calls=2)
        tracer.start()
        for i in range(10):
            def f(x=i):
                return x
            f()
        tracer.stop()
        d = tracer.to_dict()
        self.assertTrue(d["truncated"])

    def test_inject_tracer(self):
        code = "def foo():\n    return 1\nfoo()"
        wrapped = inject_tracer(code)
        self.assertIn("sys.settrace", wrapped)
        self.assertIn("SANDBOX_TRACE", wrapped)

    def test_parse_trace_output(self):
        stdout = 'hello\n===SANDBOX_TRACE===\n{"calls": [{"func": "foo"}]}\n===END_TRACE===\n'
        result = parse_trace_output(stdout)
        self.assertIsNotNone(result)
        self.assertEqual(len(result["calls"]), 1)

    def test_parse_trace_output_no_marker(self):
        result = parse_trace_output("just normal output")
        self.assertIsNone(result)


# ========== 6. 覆盖率测试 ==========
class TestCoverage(unittest.TestCase):
    """轻量覆盖率分析器测试。"""

    def test_coverage_collector(self):
        code = "def foo():\n    return 1\ndef bar():\n    return 2\nfoo()"
        collector = CoverageCollector(source_code=code)
        collector.start()
        exec(code)
        collector.stop()
        report = collector.report()
        self.assertIn("coverage", report)
        self.assertGreater(report["total_lines"], 0)

    def test_coverage_format(self):
        code = "x = 1\nprint(x)"
        collector = CoverageCollector(source_code=code)
        collector.start()
        exec(code)
        collector.stop()
        s = collector.format_report()
        self.assertIn("覆盖率", s)

    def test_inject_coverage(self):
        code = "print('hello')"
        wrapped = inject_coverage(code)
        self.assertIn("sys.settrace", wrapped)
        self.assertIn("SANDBOX_COVERAGE", wrapped)

    def test_parse_coverage_output(self):
        stdout = 'output\n===SANDBOX_COVERAGE===\n{"total": 10, "covered": 8, "coverage": 0.8}\n===END_COVERAGE===\n'
        result = parse_coverage_output(stdout)
        self.assertIsNotNone(result)
        self.assertEqual(result["total"], 10)
        self.assertEqual(result["covered"], 8)

    def test_parse_coverage_no_marker(self):
        result = parse_coverage_output("normal output")
        self.assertIsNone(result)


# ========== 7. 统一调度器测试 ==========
class TestSandboxRunner(unittest.TestCase):
    """沙箱统一调度器测试。"""

    def setUp(self):
        self.runner = SandboxRunner()

    def test_default_disabled(self):
        self.assertFalse(self.runner.enabled)

    def test_enable_disable(self):
        self.runner.enable()
        self.assertTrue(self.runner.enabled)
        self.runner.disable()
        self.assertFalse(self.runner.enabled)

    def test_status(self):
        s = self.runner.status()
        self.assertIn("enabled", s)
        self.assertIn("security_level", s)
        self.assertIn("platform", s)
        self.assertIn("available_languages", s)

    def test_format_status(self):
        s = self.runner.format_status()
        self.assertIn("小沙箱", s)
        self.assertIn("关闭", s)

    def test_run_code_disabled(self):
        result = self.runner.run_code("print('hello')")
        self.assertFalse(result.success)
        self.assertIn("未开启", result.error)

    def test_run_code_python_restricted(self):
        self.runner.enable()
        self.runner.config.security_level = "restricted"
        result = self.runner.run_code("print('hello restricted')", language="python")
        self.assertTrue(result.success)
        self.assertIn("hello restricted", result.stdout)

    def test_run_code_python_subprocess(self):
        self.runner.enable()
        self.runner.config.security_level = "subprocess"
        result = self.runner.run_code("print('hello subprocess')", language="python")
        self.assertTrue(result.success)
        self.assertIn("hello subprocess", result.stdout)

    def test_run_code_auto(self):
        self.runner.enable()
        self.runner.config.security_level = "auto"
        result = self.runner.run_code("print('hello auto')", language="python")
        self.assertTrue(result.success)

    def test_run_code_dangerous_blocked(self):
        self.runner.enable()
        self.runner.config.security_level = "restricted"
        result = self.runner.run_code("import os\nprint(os.getcwd())", language="python")
        self.assertFalse(result.success)

    def test_run_function(self):
        self.runner.enable()
        func_code = "def multiply(a, b):\n    return a * b\n"
        result = self.runner.run_function(func_code, "multiply", args=(6, 7))
        self.assertTrue(result.success)

    def test_run_tests(self):
        self.runner.enable()
        test_code = """
import unittest
class TestSimple(unittest.TestCase):
    def test_ok(self):
        self.assertTrue(True)
"""
        result = self.runner.run_tests(test_code, language="python")
        self.assertTrue(result.success)

    def test_capture_trace_subprocess(self):
        self.runner.enable()
        self.runner.config.security_level = "subprocess"
        code = "def foo():\n    return 1\nfoo()"
        result = self.runner.run_code(code, language="python", capture_trace=True)
        self.assertTrue(result.success)
        # 追踪数据可能被解析到 call_trace
        self.assertIsInstance(result.call_trace, list)

    def test_capture_coverage_subprocess(self):
        self.runner.enable()
        self.runner.config.security_level = "subprocess"
        code = "def foo():\n    return 1\ndef bar():\n    return 2\nfoo()"
        result = self.runner.run_code(code, language="python", capture_coverage=True)
        self.assertTrue(result.success)
        # 覆盖率数据统一为 {files, overall_coverage}（v4.5 与 restricted 路径一致）
        if result.coverage:
            self.assertIn("files", result.coverage)
            self.assertIn("overall_coverage", result.coverage)

    def test_update_config(self):
        self.runner.update_config(max_memory_mb=512, max_cpu_seconds=20)
        self.assertEqual(self.runner.config.max_memory_mb, 512)
        self.assertEqual(self.runner.config.max_cpu_seconds, 20)

    def test_from_dict(self):
        runner = SandboxRunner.from_dict({"enabled": True, "max_memory_mb": 128})
        self.assertTrue(runner.config.enabled)
        self.assertEqual(runner.config.max_memory_mb, 128)

    def test_strip_markers(self):
        stdout = "hello\n===SANDBOX_TRACE===\ndata\n===END_TRACE===\nworld"
        cleaned = SandboxRunner._strip_markers(stdout)
        self.assertNotIn("SANDBOX_TRACE", cleaned)
        self.assertIn("hello", cleaned)
        self.assertIn("world", cleaned)


# ========== v3.9 新增模块测试 ==========
class TestPatternMatcher(unittest.TestCase):
    def test_simple_match(self):
        from treefarm.sandbox.pattern_matcher import CodePatternMatcher
        matcher = CodePatternMatcher()
        matches = matcher.search("result = eval(user_input)", "eval($EXPR)")
        self.assertEqual(len(matches), 1)

    def test_no_match(self):
        from treefarm.sandbox.pattern_matcher import CodePatternMatcher
        matcher = CodePatternMatcher()
        matches = matcher.search("result = safe_eval(x)", "eval($EXPR)")
        self.assertEqual(len(matches), 0)

    def test_security_scan(self):
        from treefarm.sandbox.pattern_matcher import scan_security
        code = "password = 'secret123'\nresult = eval(user_input)\nos.system('rm -rf /')\n"
        issues = scan_security(code)
        self.assertGreaterEqual(len(issues), 2)

    def test_match_lineno(self):
        from treefarm.sandbox.pattern_matcher import CodePatternMatcher
        matcher = CodePatternMatcher()
        matches = matcher.search("x = 1\ny = eval(x)\nz = 2", "eval($EXPR)")
        self.assertGreaterEqual(len(matches), 1)


class TestCodeQuality(unittest.TestCase):
    def test_basic_analysis(self):
        from treefarm.sandbox.code_quality import analyze_code_quality
        report = analyze_code_quality("def add(a, b):\n    return a + b\n", "test.py")
        self.assertEqual(report["metrics"]["functions"], 1)

    def test_long_function(self):
        from treefarm.sandbox.code_quality import analyze_code_quality
        code = "def long_func():\n" + "\n".join([f"    x{i} = {i}" for i in range(60)]) + "\n"
        report = analyze_code_quality(code, "test.py")
        self.assertGreater(report["complexity"]["total_functions"], 0)

    def test_technical_debt(self):
        from treefarm.sandbox.code_quality import analyze_code_quality
        report = analyze_code_quality("def f(a,b,c,d,e,f):\n    pass\n", "test.py")
        self.assertIn("technical_debt", report)


class TestProfiler(unittest.TestCase):
    def test_basic_profiling(self):
        from treefarm.sandbox.profiler import Profiler
        profiler = Profiler()
        profiler.enable()
        total = sum(range(1000))
        profiler.disable()
        self.assertEqual(total, 499500)
        report = profiler.get_report()
        self.assertIsNotNone(report)

    def test_profile_code(self):
        from treefarm.sandbox.profiler import profile_code
        result = profile_code("total = sum(range(100))")
        self.assertIsNotNone(result)


class TestConfigValidation(unittest.TestCase):
    def test_valid_config(self):
        from treefarm.sandbox.types import SandboxConfig
        valid, errors = SandboxConfig().validate()
        self.assertTrue(valid)
        self.assertEqual(len(errors), 0)

    def test_invalid_memory(self):
        from treefarm.sandbox.types import SandboxConfig
        valid, errors = SandboxConfig(max_memory_mb=4).validate()
        self.assertFalse(valid)

    def test_warnings(self):
        from treefarm.sandbox.types import SandboxConfig
        warnings = SandboxConfig(max_memory_mb=1024, network=True).get_warnings()
        self.assertGreater(len(warnings), 0)


class TestErrorClassification(unittest.TestCase):
    def test_timeout(self):
        from treefarm.sandbox.types import SandboxResult
        r = SandboxResult(success=False, error="Execution timed out")
        r.classify_error()
        self.assertEqual(r.error_category, "timeout")

    def test_memory(self):
        from treefarm.sandbox.types import SandboxResult
        r = SandboxResult(success=False, error="MemoryError: out of memory")
        r.classify_error()
        self.assertEqual(r.error_category, "memory")

    def test_syntax(self):
        from treefarm.sandbox.types import SandboxResult
        r = SandboxResult(success=False, error="SyntaxError: invalid syntax")
        r.classify_error()
        self.assertEqual(r.error_category, "syntax")

    def test_success(self):
        from treefarm.sandbox.types import SandboxResult
        r = SandboxResult(success=True)
        r.classify_error()
        self.assertEqual(r.error_category, "")


# ========== 运行测试 ==========
if __name__ == "__main__":
    unittest.main(verbosity=2)
