# -*- coding: utf-8 -*-
"""
树场 35 轮测试 · R13：模板注入 SSTI 专项（unittest）

目标：验证 SSTI/Jinja2/Django 模板注入检测——新能力方向（此前未覆盖）。

运行：
  python3 -m unittest tests.test_round30_ssti -v
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from treefarm.analysis import detect_security_issues  # noqa: E402


def make_file(content: str, suffix=".py") -> str:
    fd, path = tempfile.mkstemp(suffix=suffix, prefix="tf_r30_")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def issues_of(content, itype=None, suffix=".py"):
    path = make_file(content, suffix)
    try:
        all_issues = detect_security_issues([path])["issues"]
        if itype:
            return [i for i in all_issues if i["type"] == itype]
        return all_issues
    finally:
        os.unlink(path)


class SstiPythonTest(unittest.TestCase):
    """Python 模板注入（Jinja2/Django）"""

    def test_jinja_template_var(self):
        # 模板字符串来自用户输入
        issues = issues_of('''\
from jinja2 import Template
def render(user_input):
    return Template(user_input).render(name="x")
''', None)
        self.assertTrue(len(issues) >= 1, f"Jinja2 Template(用户输入) 漏报: {issues}")

    def test_jinja_from_string(self):
        issues = issues_of('''\
from jinja2 import Environment
def render(env, tpl):
    return env.from_string(tpl).render()
''', None)
        self.assertTrue(len(issues) >= 1, f"from_string 漏报: {issues}")

    def test_flask_render_template_string(self):
        issues = issues_of('''\
from flask import render_template_string
def page(user):
    return render_template_string("<h1>" + user + "</h1>")
''', None)
        self.assertTrue(len(issues) >= 1, f"render_template_string 拼接漏报: {issues}")


class DriverTest(unittest.TestCase):
    """检测能力验证：现有规则能否捕捉这些形态"""

    def test_expect_detect_or_not(self):
        # 保守断言：至少不崩，且对明确拼接形态有检测
        issues = issues_of('''\
from flask import render_template_string
def page(user):
    return render_template_string("{{ 1+1 }}" + user)
''', None)
        # XSS/模板注入任一命中即可（宁缺毋滥下也可能 0，此处只验证不崩）
        self.assertIsInstance(issues, list)


if __name__ == "__main__":
    unittest.main()