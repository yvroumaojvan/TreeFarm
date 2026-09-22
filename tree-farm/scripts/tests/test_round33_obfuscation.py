# -*- coding: utf-8 -*-
"""
树场 35 轮测试 · R17：混淆对抗 II（unittest）

目标：静态分析最常见的绕过手法——编码字符串、拼接拆分、变量间接。
验证插件在恶意样本风格的代码上不漏关键靶点。

运行：
  python3 -m unittest tests.test_round33_obfuscation -v
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from treefarm.analysis import detect_security_issues  # noqa: E402


def make_file(content: str, suffix=".py") -> str:
    fd, path = tempfile.mkstemp(suffix=suffix, prefix="tf_r33_")
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


class ObfuscationTest(unittest.TestCase):
    """混淆变体——核心危险调用必须被识别"""

    def test_encode_string_concat(self):
        # 字符串分段拼接 eval（恶意样本常见）
        issues = issues_of('''\
def decode(s):
    part = "ex" + "ec"
    return __import__("builtins").__dict__[part](s)
''', "动态执行")
        self.assertTrue(len(issues) >= 1, f"拼接 exec 漏报: {issues}")

    def test_builtins_indirect(self):
        # getattr 拿 eval
        issues = issues_of('''\
def rce(data):
    f = getattr(__builtins__, "eval")
    return f(data)
''', "动态执行")
        self.assertTrue(len(issues) >= 1, f"getattr eval 漏报: {issues}")

    def test_str_join_eval(self):
        issues = issues_of('''\
def run(code):
    name = "".join(["e", "v", "a", "l"])
    globals()[name](code)
''', "动态执行")
        self.assertTrue(len(issues) >= 1, f"join eval 漏报: {issues}")

    def test_pickle_obfuscated(self):
        # 分段拼接 pickle.loads
        issues = issues_of('''\
def load(data):
    mod = "pick" + "le"
    return __import__(mod).loads(data)
''', "不安全反序列化")
        self.assertTrue(len(issues) >= 1, f"拼接 pickle 漏报: {issues}")

    def test_sql_hex_concat(self):
        # 十六进制 + 拼接 SQL
        issues = issues_of('''\
def q(cur, uid):
    sql = "SELECT * FROM users WHERE id = " + uid + " LIMIT 1"
    cur.execute(sql)
''', "SQL注入")
        self.assertTrue(len(issues) >= 1, f"拼接 SQL 漏报: {issues}")

    def test_nested_paren_cmd(self):
        # 嵌套括号 os.system（多层）
        issues = issues_of('''\
import os


def run(cmd):
    os.system(("echo " + cmd).strip())
''', "命令注入")
        self.assertTrue(len(issues) >= 1, f"嵌套括号命令漏报: {issues}")


class SafeStillSafeTest(unittest.TestCase):
    """这些混淆下安全代码仍不误报"""

    def test_safe_builtin(self):
        issues = issues_of('''\
def check(x):
    return getattr(x, "__class__").__name__
''', "动态执行")
        self.assertEqual([], issues, f"安全 getattr 误报: {issues}")

    def test_safe_sql_param(self):
        issues = issues_of('''\
def q(cur, uid):
    cur.execute("SELECT * FROM users WHERE id = ?", (uid,))
''', "SQL注入")
        self.assertEqual([], issues, f"参数化误报: {issues}")


if __name__ == "__main__":
    unittest.main()