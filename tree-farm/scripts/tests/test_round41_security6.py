# -*- coding: utf-8 -*-
"""
树场 50 轮测试 · R36：安全地狱 VI（unittest）

运行：
  python3 -m unittest tests.test_round41_security6 -v
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from treefarm.analysis import detect_security_issues  # noqa: E402


def make_file(content: str, suffix=".py") -> str:
    fd, path = tempfile.mkstemp(suffix=suffix, prefix="tf_r41_")
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


class XssVariantTest(unittest.TestCase):
    """XSS 变体 VI"""

    def test_srcdoc_iframe(self):
        issues = issues_of('''\
function show(data) {
    const iframe = document.createElement("iframe");
    iframe.srcdoc = data;
    document.body.appendChild(iframe);
}
''', "XSS跨站脚本", ".js")
        self.assertTrue(len(issues) >= 1, f"srcdoc 漏报: {issues}")

    def test_onerror_handler(self):
        issues = issues_of('''\
function loadImg(url) {
    const img = new Image();
    img.src = url;
    img.onerror = "alert(1)";
}
''', "XSS跨站脚本", ".js")
        self.assertTrue(len(issues) >= 1, f"onerror 注入漏报: {issues}")


class PathVariantTest(unittest.TestCase):
    """路径变体 VI"""

    def test_rmtree_concat(self):
        issues = issues_of('''\
import shutil


def clean(name):
    shutil.rmtree("/tmp/app/" + name)
''', "路径遍历")
        self.assertTrue(len(issues) >= 1, f"rmtree 拼接漏报: {issues}")


class DeserVariantTest(unittest.TestCase):
    def test_cloudpickle(self):
        issues = issues_of('''\
import cloudpickle


def load(b):
    return cloudpickle.loads(b)
''', "不安全反序列化")
        self.assertTrue(len(issues) >= 1, f"cloudpickle 漏报: {issues}")


class SsrfVariantTest(unittest.TestCase):
    def test_protocol_relative(self):
        # 协议相对 URL（//host）转发
        issues = issues_of('''\
import requests


def go(url):
    return requests.get("//internal" + url)
''', "SSRF")
        self.assertTrue(len(issues) >= 1, f"协议相对 URL 漏报: {issues}")


class CombinedAttackTest(unittest.TestCase):
    """组合攻击：模板注入 + 命令"""

    def test_ssti_cmd_chain(self):
        issues = issues_of('''\
from flask import render_template_string


def page(name):
    return render_template_string("{{ __import__('os').popen('id').read() }}" + name)
''', "模板注入")
        self.assertTrue(len(issues) >= 1, f"SSTI 组合漏报: {issues}")


class SafeGuardTest(unittest.TestCase):
    def test_safe_img_src(self):
        # 固定图片源不报
        issues = issues_of('''\
function logo() {
    const img = new Image();
    img.src = "/static/logo.png";
    img.onerror = handleError;
}
''', "XSS跨站脚本", ".js")
        self.assertEqual([], issues, f"安全图片误报: {issues}")


if __name__ == "__main__":
    unittest.main()