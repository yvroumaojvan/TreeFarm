# -*- coding: utf-8 -*-
"""
安全盲区终结战 · P5 测试护城河（混淆对抗泛化 II）

覆盖 91 轮确认的绕过路径：编码函数族拼接（chr/fromCharCode/bytes.fromhex）、
二次注入（转义后拼接进危险函数）。每条 2-3 变体 + 1 负例。
当前应红（规则未覆盖）→ P5 规则落地后必须全绿。

运行：
  python3 -m unittest tests.test_security_obfuscation3 -v
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from treefarm.analysis import detect_security_issues  # noqa: E402


def make_file(content: str, suffix=".py") -> str:
    fd, path = tempfile.mkstemp(suffix=suffix, prefix="tf_obf3_")
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


class ChrConcatPythonTest(unittest.TestCase):
    """Python chr() 编码拼接 → 危险调用"""

    def test_chr_join_os_system(self):
        issues = issues_of('''\
import os

def run():
    cmd = "".join(chr(c) for c in [0x69, 0x64])
    os.system(cmd)
''', "命令注入")
        self.assertTrue(len(issues) >= 1, f"chr 拼接→os.system 漏报: {issues}")

    def test_chr_concat_popen(self):
        issues = issues_of('''\
import subprocess

def run(user):
    s = chr(0x6c) + chr(0x73)
    subprocess.Popen(s + " " + user, shell=True)
''', "命令注入")
        self.assertTrue(len(issues) >= 1, f"chr 拼接→Popen 漏报: {issues}")

    def test_bytes_fromhex_eval(self):
        issues = issues_of('''\
def calc():
    payload = bytes.fromhex("6964").decode()
    exec(payload)
''', "动态执行")
        self.assertTrue(len(issues) >= 1, f"bytes.fromhex→exec 漏报: {issues}")

    def test_safe_chr_no_fp(self):
        issues = issues_of('''\
def label(i):
    return "第" + chr(0x4E00 + i) + "项"
''', "命令注入")
        self.assertEqual([], issues, f"chr 纯字符串操作负例误报: {issues}")


class FromCharCodeJsTest(unittest.TestCase):
    """JS String.fromCharCode 编码拼接 → 危险调用"""

    def test_from_char_code_eval(self):
        issues = issues_of('''\
function go() {
    var code = String.fromCharCode(0x61, 0x6c, 0x65, 0x72, 0x74);
    eval(code);
}
''', "动态执行", ".js")
        self.assertTrue(len(issues) >= 1, f"fromCharCode→eval 漏报: {issues}")

    def test_from_char_code_works(self):
        issues = issues_of('''\
const { execSync } = require('child_process');
function run() {
    const bin = String.fromCharCode(0x6c, 0x73);
    execSync(bin);
}
''', "命令注入", ".js")
        self.assertTrue(len(issues) >= 1, f"fromCharCode→execSync 漏报: {issues}")


class DoubleInjectionTest(unittest.TestCase):
    """二次注入：转义后拼接进危险函数（HTML 转义不防 SQL/命令注入）"""

    def test_escaped_concat_sql(self):
        issues = issues_of('''\
import html
import sqlite3

def find(name):
    safe = html.escape(name)
    cur = sqlite3.connect("a.db").cursor()
    cur.execute("SELECT * FROM users WHERE name = '" + safe + "'")
''', "SQL注入")
        self.assertTrue(len(issues) >= 1, f"escape 后拼接 SQL（二次注入）漏报: {issues}")

    def test_quote_concat_cmd(self):
        issues = issues_of('''\
import shlex
import os

def run(user):
    safe = shlex.quote(user)
    os.system("echo " + safe + " > /tmp/x")
''', "命令注入")
        self.assertTrue(len(issues) >= 1, f"quote 后拼接命令漏报: {issues}")

    def test_safe_paramized_no_fp(self):
        issues = issues_of('''\
import sqlite3

def find(name):
    cur = sqlite3.connect("a.db").cursor()
    cur.execute("SELECT * FROM users WHERE name = ?", (name,))
''', "SQL注入")
        self.assertEqual([], issues, f"参数化负例误报: {issues}")


class JqueryHtmlTest(unittest.TestCase):
    """jQuery .html() 注入不可信数据（XSS）"""

    def test_jquery_html_request(self):
        issues = issues_of('''\
app.get("/search", (req, res) => {
    $("#result").html(req.query.q);
});
''', "XSS跨站脚本", ".js")
        self.assertTrue(len(issues) >= 1, f"jQuery .html() 注入漏报: {issues}")

    def test_jquery_html_template_concat(self):
        issues = issues_of('''\
function render(name) {
    $("#list").html("<li>" + name + "</li>");
}
''', "XSS跨站脚本", ".js")
        self.assertTrue(len(issues) >= 1, f"jQuery .html() 拼接漏报: {issues}")

    def test_safe_text_no_fp(self):
        issues = issues_of('''\
function render(name) {
    $("#list").text(name);
}
''', "XSS跨站脚本", ".js")
        self.assertEqual([], issues, f".text() 安全负例误报: {issues}")


if __name__ == "__main__":
    unittest.main()