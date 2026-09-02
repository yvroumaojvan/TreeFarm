# -*- coding: utf-8 -*-
"""
树场 v4.6 新增能力测试套件（unittest，零依赖）

覆盖（对应插件优化清单 P1/P2）：
  1. XXE 检测（etree/lxml/minidom/sax/expat）
  2. 开放重定向检测（redirect/Location 接用户输入）
  3. 认证绕过检测（敏感路由无认证装饰器；有装饰器不误报）
  4. 前端 XSS（.js/.html 的 innerHTML/v-html/dangerouslySetInnerHTML —— 验证安全扫描扩展名扩展）
  5. 资源泄漏检测（open/socket 未用 with 且无 close；with/close 模式不误报）
  6. 不存在路径的 CLI 友好提示（不再静默/Traceback）
  7. restricted 模式下 JS/Shell 自动走 subprocess 沙箱
  8. 干净文件对新规则的零误报

运行：
  python3 -m unittest discover -s scripts/tests -v
"""
import os
import sys
import tempfile
import unittest
from io import StringIO
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from treefarm.analysis import detect_performance_issues, detect_security_issues  # noqa: E402


def make_file(content: str, suffix: str = ".py") -> str:
    """造一个含目标代码的临时文件，返回路径。"""
    fd, path = tempfile.mkstemp(suffix=suffix, prefix="treefarm_v46_")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def types_of(issues, itype):
    """取 issues 中指定 type 的列表。"""
    return [i for i in issues if i["type"] == itype]


class SecurityXxeTest(unittest.TestCase):
    """XXE：XML 解析无防护应被检出。"""

    def _run(self, content):
        path = make_file(content)
        try:
            return detect_security_issues([path])["issues"]
        finally:
            os.unlink(path)

    def test_elementtree_parse(self):
        issues = self._run('''\
import xml.etree.ElementTree as ET
def load_xml(data):
    root = ET.parse(data)
    return root
''')
        self.assertTrue(types_of(issues, "XXE"), issues)

    def test_lxml_fromstring_and_minidom(self):
        issues = self._run('''\
from lxml import etree
def a(data):
    return etree.fromstring(data)
def b(path):
    from xml.dom import minidom
    return minidom.parse(path)
''')
        self.assertEqual(len(types_of(issues, "XXE")), 2, issues)

    def test_sax_parse(self):
        issues = self._run('''\
import xml.sax
def parse_file(path):
    handler = xml.sax.ContentHandler()
    xml.sax.parse(path, handler)
''')
        self.assertTrue(types_of(issues, "XXE"), issues)

    def test_clean_file_no_xxe(self):
        issues = self._run('''\
def add(a, b):
    return a + b
''')
        self.assertEqual(types_of(issues, "XXE"), [])


class OpenRedirectTest(unittest.TestCase):
    """开放重定向：跳转目标来自用户输入应被检出。"""

    def test_redirect_from_request(self):
        path = make_file('''\
from flask import redirect, request

@app.route("/go")
def go():
    return redirect(request.args.get("next"))
''')
        try:
            issues = detect_security_issues([path])["issues"]
            self.assertTrue(types_of(issues, "开放重定向"), issues)
        finally:
            os.unlink(path)

    def test_location_header_user_input(self):
        path = make_file('''\
from flask import Response, request

@app.route("/jump")
def jump():
    resp = Response()
    resp.headers["Location"] = request.args.get("url")
    return resp
''')
        try:
            issues = detect_security_issues([path])["issues"]
            self.assertTrue(types_of(issues, "开放重定向"), issues)
        finally:
            os.unlink(path)

    def test_fixed_redirect_not_reported(self):
        path = make_file('''\
from flask import redirect

@app.route("/home")
def home():
    return redirect("/index")
''')
        try:
            issues = detect_security_issues([path])["issues"]
            self.assertEqual(types_of(issues, "开放重定向"), [], issues)
        finally:
            os.unlink(path)


class AuthBypassTest(unittest.TestCase):
    """认证绕过：敏感操作路由缺少认证装饰器。"""

    def test_sensitive_route_without_auth(self):
        path = make_file('''\
from flask import Flask
app = Flask(__name__)

@app.route("/api/delete_user", methods=["POST"])
def delete_user():
    db.delete_all()
    return "ok"

@app.route("/admin/reset")
def reset_admin():
    return "reset"
''')
        try:
            issues = detect_security_issues([path])["issues"]
            self.assertTrue(types_of(issues, "认证绕过"), issues)
        finally:
            os.unlink(path)

    def test_sensitive_route_with_auth_decorator_no_report(self):
        path = make_file('''\
from flask import Flask
app = Flask(__name__)

@app.route("/api/delete_user", methods=["POST"])
@login_required
def delete_user():
    db.delete_all()
    return "ok"

@app.route("/api/delete_user")
def get_user():
    return "view"
''')
        try:
            issues = detect_security_issues([path])["issues"]
            self.assertEqual(types_of(issues, "认证绕过"), [], issues)
        finally:
            os.unlink(path)

    def test_no_route_no_report(self):
        path = make_file('''\
def helper():
    return "no routes here"
''')
        try:
            issues = detect_security_issues([path])["issues"]
            self.assertEqual(types_of(issues, "认证绕过"), [], issues)
        finally:
            os.unlink(path)


class FrontendXssTest(unittest.TestCase):
    """前端 XSS：安全扫描扩展到 JS/HTML（v4.6 扩展名扩展）。"""

    def test_js_innerhtml(self):
        path = make_file('''\
function render(userName) {
    document.getElementById("box").innerHTML = userName;
    document.write(location.hash);
}
''', suffix=".js")
        try:
            issues = detect_security_issues([path])["issues"]
            xss = types_of(issues, "XSS跨站脚本")
            self.assertGreaterEqual(len(xss), 2, issues)
        finally:
            os.unlink(path)

    def test_vue_vhtml_and_jsx(self):
        path = make_file('''\
<template>
  <div v-html="userRichText"></div>
</template>
''', suffix=".html")
        try:
            issues = detect_security_issues([path])["issues"]
            self.assertTrue(types_of(issues, "XSS跨站脚本"), issues)
        finally:
            os.unlink(path)

        path2 = make_file('''\
const el = <div dangerouslySetInnerHTML={{ __html: rawHtml }} />;
''', suffix=".jsx")
        try:
            issues = detect_security_issues([path2])["issues"]
            self.assertTrue(types_of(issues, "XSS跨站脚本"), issues)
        finally:
            os.unlink(path2)

    def test_plain_js_no_xss(self):
        path = make_file('''\
function sum(a, b) {
    return a + b;
}
console.log(sum(1, 2));
''', suffix=".js")
        try:
            issues = detect_security_issues([path])["issues"]
            self.assertEqual(types_of(issues, "XSS跨站脚本"), [], issues)
        finally:
            os.unlink(path)


class ResourceLeakTest(unittest.TestCase):
    """资源泄漏：open/socket 未 with 且无 close。"""

    def _run(self, content):
        path = make_file(content)
        try:
            return detect_performance_issues([path])["issues"]
        finally:
            os.unlink(path)

    def test_open_without_close(self):
        issues = self._run('''\
def load(path):
    f = open(path)
    data = f.read()
    return data
''')
        self.assertTrue(types_of(issues, "资源泄漏"), issues)

    def test_socket_without_close(self):
        issues = self._run('''\
import socket

def connect(host):
    s = socket.socket()
    s.connect((host, 80))
    return s
''')
        self.assertTrue(types_of(issues, "资源泄漏"), issues)

    def test_with_statement_not_reported(self):
        issues = self._run('''\
def load(path):
    with open(path) as f:
        return f.read()
''')
        self.assertEqual(types_of(issues, "资源泄漏"), [], issues)

    def test_try_finally_close_not_reported(self):
        issues = self._run('''\
def load(path):
    f = open(path)
    try:
        return f.read()
    finally:
        f.close()
''')
        self.assertEqual(types_of(issues, "资源泄漏"), [], issues)

    def test_module_level_open_not_reported(self):
        issues = self._run('''\
f = open("/tmp/x.txt")
data = f.read()
''')
        self.assertEqual(types_of(issues, "资源泄漏"), [], issues)

    def test_open_in_for_with_not_reported(self):
        issues = self._run('''\
def read_all(paths):
    for p in paths:
        with open(p) as f:
            print(f.read())
''')
        self.assertEqual(types_of(issues, "资源泄漏"), [], issues)


class CleanFileNoFalsePositiveTest(unittest.TestCase):
    """干净健康代码对 v4.6 新规则零报告。"""

    def test_clean_py_zero_issues(self):
        path = make_file('''\
# -*- coding: utf-8 -*-
def process(items):
    result = []
    for item in items:
        if item is not None:
            result.append(str(item).upper())
    return result

def main():
    data = process(["a", "b"])
    print(data)

if __name__ == "__main__":
    main()
''')
        try:
            sec = detect_security_issues([path])
            perf = detect_performance_issues([path])
            self.assertEqual(sec["total"], 0, sec)
            self.assertEqual(perf["total"], 0, perf)
        finally:
            os.unlink(path)


class CliPathHintTest(unittest.TestCase):
    """P1：不存在路径时给出友好提示（不静默、不 Traceback）。"""

    def test_nonexistent_path_friendly_message(self):
        from treefarm.cli import main
        with mock.patch("sys.argv", ["tree_farm.py", "/nonexistent/tf_xyz_404"]):
            with mock.patch("sys.stdout", new_callable=StringIO) as buf:
                main()
        out = buf.getvalue()
        self.assertIn("路径不存在", out)
        self.assertIn("💡", out)


class SandboxMultiLangTest(unittest.TestCase):
    """P1：restricted 模式下 JS/Shell 自动走 subprocess 沙箱。"""

    def test_restricted_runs_javascript_and_shell(self):
        from treefarm.sandbox import SandboxRunner
        runner = SandboxRunner()
        runner.enable()
        runner.update_config(security_level="restricted")
        js = runner.run_code("console.log(1+2)", language="javascript")
        self.assertTrue(js.success and "3" in js.stdout, js.error)
        sh = runner.run_code("echo treefarm-ok", language="shell")
        self.assertTrue(sh.success and "treefarm-ok" in sh.stdout, sh.error)


if __name__ == "__main__":
    unittest.main(verbosity=2)