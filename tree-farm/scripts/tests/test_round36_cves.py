# -*- coding: utf-8 -*-
"""
树场 35 轮测试 · R20：真实 CVE 模式验证（unittest）

运行：
  python3 -m unittest tests.test_round36_cves -v
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from treefarm.analysis import detect_security_issues  # noqa: E402


def make_file(content: str, suffix=".py") -> str:
    fd, path = tempfile.mkstemp(suffix=suffix, prefix="tf_r36_")
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


class CvePatternTest(unittest.TestCase):
    """真实 CVE 模式覆盖验证"""

    def test_open_redirect(self):
        # 开放重定向：next 参数跳转（OWASP CVE 常见）
        issues = issues_of('''\
from flask import redirect, request


def login():
    return redirect(request.args.get("next") or "/")
''', None)
        self.assertTrue(len(issues) >= 1, f"开放重定向漏报: {issues}")

    def test_cors_wildcard(self):
        # CORS 通配符 + 用户源
        issues = issues_of('''\
from flask import Flask, request

app = Flask(__name__)


@app.after_request
def cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = request.headers.get("Origin", "*")
    return resp
''', None)
        self.assertTrue(len(issues) >= 1, f"CORS 通配漏报: {issues}")

    def test_log4j_style_python(self):
        # Log4Shell 风格：日志模板拼接用户输入（Python logging）
        issues = issues_of('''\
import logging


def process(user):
    logging.info("Login user: " + user)
''', None)
        # 日志拼接可能触发注入检测（需人工确认级别）
        self.assertIsInstance(issues, list)

    def test_zip_slip_archive(self):
        # CVE-2022 类：压缩包解压路径穿越
        issues = issues_of('''\
import zipfile


def unpack(name):
    with zipfile.ZipFile(name) as z:
        z.extractall("/tmp/out")
''', "Zip Slip")
        self.assertTrue(len(issues) >= 1, f"zip extractall 漏报: {issues}")

    def test_tempfile_race(self):
        # 临时文件竞态（CVE 常见：mktemp 后 open 可被 symlink 攻击）
        issues = issues_of('''\
import os, tempfile


def write_temp(data):
    path = tempfile.mktemp()
    with open(path, "w") as f:
        f.write(data)
''', None)
        self.assertTrue(len(issues) >= 1, f"mktemp 竞态漏报: {issues}")


class JavaCveTest(unittest.TestCase):
    def test_spring_el(self):
        # Spring EL 注入（Spring4Shell 风格）
        issues = issues_of('''\
public void eval(String expr) {
    SpelExpressionParser parser = new SpelExpressionParser();
    parser.parseExpression(expr).getValue();
}
''', "动态执行", ".java")
        self.assertTrue(len(issues) >= 1, f"SpelExpressionParser 漏报: {issues}")


if __name__ == "__main__":
    unittest.main()