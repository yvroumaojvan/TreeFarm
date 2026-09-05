# -*- coding: utf-8 -*-
"""
树场 v4.7 Grader 化新增能力测试套件（unittest，零依赖）

运行：
  python3 -m unittest discover -s tests -v
  或直接 python3 tests/test_v47_grader.py

覆盖：
- spec 模块：功能画像构建（build_spec 栈能力判定/聚焦类型）、AI 自读（autodetect_spec）
- grader 综合评分：六维加权、等级边界、短板建议、趋势对比
- 契约检测：API 委托一致性（tornado#1 型）、抽象方法完整性
- 异步竞态降误报 + 测试目录降级
"""
import os
import shutil
import tempfile
import unittest

from treefarm.spec import (build_spec, build_bugspec, autodetect_spec,
                           format_spec, format_bugspec, grade_project,
                           format_grade)
from treefarm.analysis import detect_logic_issues, detect_security_issues
from treefarm import analysis


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


class TestBuildSpec(unittest.TestCase):
    def test_web_stack_detected(self):
        ctx = build_spec("这是一个 Flask 博客系统：用户可以注册登录、发布文章、评论，数据存 MySQL")
        self.assertIn("web", ctx["stack"])
        self.assertIn("database", ctx["stack"])
        self.assertIn("auth", ctx["stack"])
        self.assertTrue(ctx["focused"])  # 有重点检测类型

    def test_focused_contains_web_types(self):
        ctx = build_spec("Web 后端 API，支持登录和支付")
        for t in ("认证绕过", "硬编码密码"):
            self.assertIn(t, ctx["focused"])

    def test_empty_spec(self):
        ctx = build_spec("")
        self.assertEqual(ctx["stack"], [])
        self.assertEqual(ctx["focused"], [])

    def test_cli_detected(self):
        ctx = build_spec("命令行工具，接收参数解析文件")
        self.assertIn("cli", ctx["stack"])

    def test_format_spec(self):
        ctx = build_spec("Flask 登录系统")
        out = format_spec(ctx)
        self.assertIn("项目功能画像", out)
        self.assertIn("重点检测", out)


class TestBuildBugspec(unittest.TestCase):
    """v4.7.1：用户报 bug 症状 → 推断重点排查方向（--bug）。"""

    def test_login_symptom_detected(self):
        ctx = build_bugspec("登录功能有问题：点了登录没反应，验证还很慢，偶尔超时，金额也算错")
        self.assertIn("登录/认证问题", ctx["symptoms"])
        self.assertIn("功能无响应", ctx["symptoms"])
        self.assertIn("性能卡顿", ctx["symptoms"])
        self.assertIn("数据/逻辑错误", ctx["symptoms"])
        self.assertTrue(ctx["is_bug"])
        # 症状 → 重点检测类型联动
        self.assertIn("认证绕过", ctx["focused"])
        self.assertIn("协程未await", ctx["focused"])

    def test_crash_symptom(self):
        ctx = build_bugspec("打开就闪退崩溃，有时候白屏")
        self.assertIn("崩溃/闪退", ctx["symptoms"])
        self.assertIn("属性不存在", ctx["focused"])

    def test_empty_bugspec(self):
        ctx = build_bugspec("")
        self.assertEqual(ctx["symptoms"], [])
        self.assertEqual(ctx["focused"], [])
        self.assertTrue(ctx["is_bug"])

    def test_format_bugspec(self):
        ctx = build_bugspec("很卡，加载不出")
        out = format_bugspec(ctx)
        self.assertIn("bug 画像", out)
        self.assertIn("重点排查", out)

    def test_clean_code_no_symptom(self):
        ctx = build_bugspec("项目一切正常")
        self.assertEqual(ctx["symptoms"], [])


class TestAutodetectSpec(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_reads_readme(self):
        _write(os.path.join(self.tmp, "README.md"),
               "# 我的工具\n这是一个 Web API 服务，用户登录后调用接口。\n")
        _write(os.path.join(self.tmp, "app.py"), '"""Flask 应用入口。"""\nprint(1)\n')
        tree = [os.path.join(self.tmp, "README.md"),
                os.path.join(self.tmp, "app.py")]
        ctx = autodetect_spec(tree, root=self.tmp)
        self.assertIn("web", ctx["stack"])
        self.assertIn("auth", ctx["stack"])

    def test_empty_project_fallback(self):
        tree = [os.path.join(self.tmp, "main.py")]
        ctx = autodetect_spec(tree, root=self.tmp)
        self.assertIsInstance(ctx["stack"], list)


class TestGradeProject(unittest.TestCase):
    def test_composite_a_plus(self):
        g = grade_project({"security": 99, "logic": 98, "performance": 97,
                           "structure": 99, "quality": 96, "debt": 95})
        self.assertEqual(g["grade"], "A+")
        self.assertGreaterEqual(g["composite"], 95)

    def test_composite_f(self):
        g = grade_project({"security": 10, "logic": 10, "performance": 10,
                           "structure": 10, "quality": 10, "debt": 10})
        self.assertEqual(g["grade"], "F")
        self.assertLess(g["composite"], 50)

    def test_weakest_priority_suggestion(self):
        g = grade_project({"security": 90, "logic": 30, "performance": 90,
                           "structure": 90, "quality": 90, "debt": 90})
        self.assertTrue(any("逻辑正确性" in s and "30" in s for s in g["suggestions"]))

    def test_format_grade(self):
        g = grade_project({"security": 80, "logic": 80, "performance": 80,
                           "structure": 80, "quality": 80, "debt": 80})
        out = format_grade(g)
        self.assertIn("综合评分", out)
        self.assertIn("安全", out)

    def test_format_grade_with_trend(self):
        g = grade_project({"security": 85, "logic": 80, "performance": 80,
                           "structure": 80, "quality": 80, "debt": 80})
        prev = grade_project({"security": 70, "logic": 80, "performance": 80,
                              "structure": 80, "quality": 80, "debt": 80})
        out = format_grade(g, prev)
        self.assertIn("进步", out)


class TestContractDetection(unittest.TestCase):
    """API 契约一致性 + 抽象方法完整性（对应 tornado#1 型 bug）。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_delegate_consistency_bug(self):
        """同类 4 个方法委托 self.ws_connection，唯独 1 个用 self.stream（且 stream=None）→ 报。"""
        code = '''\
class Handler:
    def __init__(self):
        self.ws_connection = None
        self.stream = None

    def write_message(self, m):
        self.ws_connection.write_message(m)

    def write_ping(self, p):
        self.ws_connection.write_ping(p)

    def close(self):
        self.ws_connection.close()

    def select_subprotocol(self):
        return self.ws_connection.selected_subprotocol

    def set_nodelay(self, value):
        self.stream.set_nodelay(value)
'''
        path = os.path.join(self.tmp, "h.py")
        _write(path, code)
        result = detect_logic_issues([path], root=self.tmp)
        types = [i["type"] for i in result["issues"]]
        self.assertIn("API契约", types)
        hit = next(i for i in result["issues"] if i["type"] == "API契约")
        self.assertIn("ws_connection", hit["fix"])

    def test_clean_delegate_no_false_positive(self):
        """所有方法统一委托 self.ws_connection → 不报。"""
        code = '''\
class Handler:
    def __init__(self):
        self.ws_connection = None

    def a(self):
        self.ws_connection.a()

    def b(self):
        self.ws_connection.b()

    def c(self):
        self.ws_connection.c()

    def d(self):
        self.ws_connection.d()
'''
        path = os.path.join(self.tmp, "ok.py")
        _write(path, code)
        result = detect_logic_issues([path], root=self.tmp)
        self.assertNotIn("API契约", [i["type"] for i in result["issues"]])

    def test_abstract_method_missing(self):
        """具体子类未实现抽象方法 → 报。"""
        code = '''\
import abc

class Protocol(abc.ABC):
    @abc.abstractmethod
    def set_nodelay(self, x):
        raise NotImplementedError

    @abc.abstractmethod
    def close(self):
        raise NotImplementedError

class Protocol13(Protocol):
    def close(self):
        pass
'''
        path = os.path.join(self.tmp, "abs.py")
        _write(path, code)
        result = detect_logic_issues([path], root=self.tmp)
        types = [i["type"] for i in result["issues"]]
        self.assertIn("抽象方法未实现", types)
        hit = next(i for i in result["issues"] if i["type"] == "抽象方法未实现")
        self.assertIn("set_nodelay", hit["desc"])

    def test_abstract_complete_no_false_positive(self):
        """子类实现全部抽象方法 → 不报。"""
        code = '''\
import abc

class Protocol(abc.ABC):
    @abc.abstractmethod
    def go(self):
        pass

class Impl(Protocol):
    def go(self):
        return 1
'''
        path = os.path.join(self.tmp, "full.py")
        _write(path, code)
        result = detect_logic_issues([path], root=self.tmp)
        self.assertNotIn("抽象方法未实现", [i["type"] for i in result["issues"]])


class TestAsyncRaceDowngrade(unittest.TestCase):
    """异步框架竞态降误报：纯 asyncio + 无真线程 → 不报竞态。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_async_file_no_race(self):
        code = '''\
import asyncio

class AsyncCounter:
    def __init__(self):
        self._value = 0

    async def inc(self):
        self._value += 1
        await asyncio.sleep(0)
        return self._value
'''
        path = os.path.join(self.tmp, "async_c.py")
        _write(path, code)
        result = detect_logic_issues([path], root=self.tmp)
        self.assertNotIn("竞态条件", [i["type"] for i in result["issues"]])

    def test_real_thread_race_reported(self):
        code = '''\
import threading

class Counter:
    def __init__(self):
        self._value = 0

    def inc(self):
        t = threading.Thread(target=self._bump)
        t.start()
        self._value += 1

    def _bump(self):
        self._value += 1
'''
        path = os.path.join(self.tmp, "real_thread.py")
        _write(path, code)
        result = detect_logic_issues([path], root=self.tmp)
        self.assertIn("竞态条件", [i["type"] for i in result["issues"]])

    def test_rule_string_not_mistaken_for_thread(self):
        """规则字符串 'threading.Thread' 出现在字符串字面量 → 不算真线程（自检误报根源）。"""
        code = '''\
import re
RULE = r"threading.Thread"

class C:
    def __init__(self):
        self.n = 0

    def inc(self):
        self.n += 1
        re.match(RULE, "x")
'''
        path = os.path.join(self.tmp, "rule_str.py")
        _write(path, code)
        result = detect_logic_issues([path], root=self.tmp)
        self.assertNotIn("竞态条件", [i["type"] for i in result["issues"]])


class TestTestDirDowngrade(unittest.TestCase):
    """测试目录问题降级：test 文件的问题标记 scope=test，不拉低核心风险分。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_security_test_dir_downgrade(self):
        core = os.path.join(self.tmp, "app.py")
        _write(core, '''\
def login(user, pwd):
    if hashed_password == author.hashed_password:  # 时序攻击
        return True
''')
        testf = os.path.join(self.tmp, "tests", "test_app.py")
        _write(testf, '''\
def test_login():
    if password == stored_hash:  # 测试里的时序比较（故意构造）
        return True
''')
        result = detect_security_issues([core, testf], root=self.tmp)
        test_marked = [i for i in result["issues"] if i.get("scope") == "test"]
        self.assertTrue(test_marked)  # 测试问题被标记
        self.assertGreater(result.get("test_issues", 0), 0)

    def test_clean_code_zero(self):
        clean = os.path.join(self.tmp, "ok.py")
        _write(clean, "x = 1\ny = x + 2\n")
        result = detect_security_issues([clean], root=self.tmp)
        self.assertEqual(result["total"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
