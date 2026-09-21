# -*- coding: utf-8 -*-
"""
树场 20 轮测试 · 第 1 轮：Python 安全地狱模式（unittest，零依赖）

目标：测「修 bug 能力」——用真实 CVE 模式变体 + 静态分析混淆对抗 + 边界豁免
构造极难用例，找出 detect_security_issues 的漏报/误报短板。

用例来源（权威）：
  - OWASP Top 10 / OWASP Benchmark 变体
  - 真实 CVE 模式：Log4Shell 模板注入、二次注入、pickle RCE 混淆
  - CWE-78/89/79/22/918/502/94（命令注入/SQLi/XSS/路径遍历/SSRF/反序列化/代码注入）
  - 混淆对抗：别名、属性链、拼接、编码、多行、推导式藏污

运行：
  python3 -m unittest tests.test_round1_python_security -v
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from treefarm.analysis import detect_security_issues  # noqa: E402


def make_file(content: str, suffix=".py") -> str:
    fd, path = tempfile.mkstemp(suffix=suffix, prefix="treefarm_r1_")
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


class CmdInjectionObfuscationTest(unittest.TestCase):
    """命令注入 · 混淆对抗（CWE-78）"""

    def test_eval_alias_os_system(self):
        # e = eval 别名后执行代码（代码注入混淆）
        issues = issues_of('''\
def run(code):
    e = eval
    e(code)
''', "动态执行")
        self.assertTrue(len(issues) >= 1, f"eval 别名漏报: {issues}")

    def test_exec_in_lambda(self):
        # lambda 内藏 exec
        issues = issues_of('''\
def f(payload):
    g = lambda x: exec(x)
    g(payload)
''', "动态执行")
        self.assertTrue(len(issues) >= 1, f"lambda exec 漏报: {issues}")

    def test_os_system_upper_case(self):
        # 大小写混淆（Python 大小写敏感，os.System 不会执行，但 os.system 必须报）
        issues = issues_of('''\
import os
os.system(user_cmd)
''', "命令注入")
        self.assertTrue(len(issues) >= 1, f"os.system 变量直传漏报: {issues}")

    def test_subprocess_run_shell_true_list(self):
        # subprocess.run([...], shell=True) —— shell=True 且参数列表同样危险
        issues = issues_of('''\
import subprocess
subprocess.run(["echo", user_input], shell=True)
''', "命令注入")
        self.assertTrue(len(issues) >= 1, f"shell=True 列表漏报: {issues}")

    def test_subprocess_popen_env_concat(self):
        # 拼接进 env 再 Popen（间接注入形态）
        issues = issues_of('''\
import subprocess, os
cmd = "ls -la " + user_dir
subprocess.Popen(cmd, shell=True)
''', "命令注入")
        self.assertTrue(len(issues) >= 1, f"拼接+shell=True 漏报: {issues}")

    def test_implicit_concat_multi_line_paren(self):
        # 隐式续行（括号内多行拼接）——单行正则最常见的漏网之鱼
        issues = issues_of('''\
import os
cmd = ("ping -c 1 "
       + target)
os.system(cmd)
''', "命令注入")
        self.assertTrue(len(issues) >= 1, f"多行拼接漏报: {issues}")

    def test_safe_subprocess_list(self):
        # 安全写法：列表参数 + 无 shell → 不报
        issues = issues_of('''\
import subprocess
subprocess.run(["ls", "-la", user_dir])
''', "命令注入")
        self.assertEqual([], issues, f"安全列表误报: {issues}")


class SqlInjectionHardTest(unittest.TestCase):
    """SQL 注入 · 极难变体（CWE-89）"""

    def test_second_order_concat(self):
        # 二次注入：先拼出 sql 变量，再拼一次
        issues = issues_of('''\
def q(conn, user_id):
    t1 = "SELECT * FROM users WHERE id = " + user_id
    sql = t1 + " LIMIT 1"
    conn.execute(sql)
''', "SQL注入")
        self.assertTrue(len(issues) >= 1, f"二次拼接漏报: {issues}")

    def test_sql_in_format_string(self):
        # format 字符串拼接 SQL
        issues = issues_of('''\
def q(cur, name):
    sql = "SELECT * FROM users WHERE name = '{}'".format(name)
    cur.execute(sql)
''', "SQL注入")
        self.assertTrue(len(issues) >= 1, f"format 拼接漏报: {issues}")

    def test_sql_percent_format_var(self):
        # % 格式化拼接 SQL（非参数化）
        issues = issues_of('''\
def q(cur, name):
    cur.execute("SELECT * FROM users WHERE name = '%s'" % name)
''', "SQL注入")
        self.assertTrue(len(issues) >= 1, f"% 拼接漏报: {issues}")

    def test_safe_param_bind_tuple(self):
        # 安全：参数化绑定
        issues = issues_of('''\
def q(cur, name):
    cur.execute("SELECT * FROM users WHERE name = ?", (name,))
''', "SQL注入")
        self.assertEqual([], issues, f"参数化误报: {issues}")

    def test_safe_orm_query(self):
        # 安全：ORM
        issues = issues_of('''\
def q(session, name):
    return session.query(User).filter(User.name == name).all()
''', "SQL注入")
        self.assertEqual([], issues, f"ORM 误报: {issues}")

    def test_dict_sql_build(self):
        # 字典值拼接成 SQL
        issues = issues_of('''\
def q(conn, params):
    sql = "SELECT * FROM users WHERE " + params["filter"]
    conn.execute(sql)
''', "SQL注入")
        self.assertTrue(len(issues) >= 1, f"字典拼接漏报: {issues}")


class XssHardTest(unittest.TestCase):
    """XSS · 前端注入变体（CWE-79）"""

    def test_innerhtml_concat(self):
        issues = issues_of('''\
function render(name) {
    document.getElementById("out").innerHTML = "<b>" + name + "</b>";
}
''', "XSS跨站脚本", ".js")
        self.assertTrue(len(issues) >= 1, f"innerHTML 拼接漏报: {issues}")

    def test_jquery_html_var(self):
        issues = issues_of('''\
function show(msg) {
    $("#box").html(msg);
}
''', "XSS跨站脚本", ".js")
        self.assertTrue(len(issues) >= 1, f"jQuery .html() 漏报: {issues}")

    def test_react_dangerously(self):
        issues = issues_of('''\
function App({user}) {
    return <div dangerouslySetInnerHTML={{__html: user.bio}} />;
}
''', "XSS跨站脚本", ".js")
        self.assertTrue(len(issues) >= 1, f"dangerouslySetInnerHTML 漏报: {issues}")

    def test_safe_escape(self):
        # 安全：已转义 → 不报（宁缺毋滥）
        issues = issues_of('''\
import html
def render(name):
    return "<div>" + html.escape(name) + "</div>"
''', "XSS跨站脚本")
        self.assertEqual([], issues, f"转义后误报: {issues}")


class PathTraversalHardTest(unittest.TestCase):
    """路径遍历 · 变体（CWE-22）"""

    def test_open_join_concat(self):
        issues = issues_of('''\
import os
def read(fname):
    path = os.path.join("/var/data", fname)
    return open(path).read()
''', "路径遍历")
        self.assertTrue(len(issues) >= 1, f"join 拼接漏报: {issues}")

    def test_open_multi_concat(self):
        issues = issues_of('''\
def read(base, name):
    return open(base + "/" + name).read()
''', "路径遍历")
        self.assertTrue(len(issues) >= 1, f"多变量拼接漏报: {issues}")

    def test_tarfile_user_input(self):
        issues = issues_of('''\
import tarfile
def unpack(archive):
    with tarfile.open(archive) as t:
        t.extractall()
''', "路径遍历")
        self.assertTrue(len(issues) >= 1, f"tarfile 用户输入漏报: {issues}")


class DeserializationHardTest(unittest.TestCase):
    """反序列化 · 混淆（CWE-502）"""

    def test_pickle_obfuscated_import(self):
        # import pickle 后再 loads（模块引用形态）
        issues = issues_of('''\
import pickle
def load(data):
    return pickle.loads(data)
''', "不安全反序列化")
        self.assertTrue(len(issues) >= 1, f"pickle.loads 漏报: {issues}")

    def test_yaml_load_no_loader(self):
        issues = issues_of('''\
import yaml
def parse(text):
    return yaml.load(text)
''', "不安全反序列化")
        self.assertTrue(len(issues) >= 1, f"yaml.load 漏报: {issues}")

    def test_safe_yaml_safe_load(self):
        issues = issues_of('''\
import yaml
def parse(text):
    return yaml.safe_load(text)
''', "不安全反序列化")
        self.assertEqual([], issues, f"safe_load 误报: {issues}")


class SsrfHardTest(unittest.TestCase):
    """SSRF · 变体（CWE-918）"""

    def test_requests_concat(self):
        issues = issues_of('''\
import requests
def fetch(url):
    return requests.get("http://api.example.com/" + url)
''', "SSRF")
        self.assertTrue(len(issues) >= 1, f"requests 拼接漏报: {issues}")

    def test_urllib_var(self):
        issues = issues_of('''\
import urllib.request
def fetch(url):
    return urllib.request.urlopen(url)
''', "SSRF")
        self.assertTrue(len(issues) >= 1, f"urlopen 变量漏报: {issues}")

    def test_safe_requests_const(self):
        # 安全：固定 URL → 不报
        issues = issues_of('''\
import requests
def ping():
    return requests.get("https://api.github.com/zen")
''', "SSRF")
        self.assertEqual([], issues, f"固定 URL 误报: {issues}")


class HardcodedSecretTest(unittest.TestCase):
    """硬编码凭据 · 变体（CWE-798）"""

    def test_sk_key(self):
        issues = issues_of('''\
API_KEY = "sk-1234567890abcdefghij"
''', "硬编码凭据")
        self.assertTrue(len(issues) >= 1, f"sk- key 漏报: {issues}")

    def test_jwt_secret(self):
        issues = issues_of('''\
JWT_SECRET = "my-super-secret-jwt-key-123456"
''', "硬编码凭据")
        self.assertTrue(len(issues) >= 1, f"jwt_secret 漏报: {issues}")

    def test_placeholder_not_secret(self):
        # 安全：占位符 → 不报
        issues = issues_of('''\
COOKIE_SECRET = "__TODO:_GENERATE_YOUR_OWN_RANDOM_VALUE_HERE__"
''', "硬编码凭据")
        self.assertEqual([], issues, f"占位符误报: {issues}")


class DynamicExecTest(unittest.TestCase):
    """动态执行（CWE-94）"""

    def test_compile_exec(self):
        issues = issues_of('''\
def run(src):
    code = compile(src, "<s>", "exec")
    exec(code)
''', "动态执行")
        self.assertTrue(len(issues) >= 1, f"compile+exec 漏报: {issues}")

    def test_import_dynamic(self):
        issues = issues_of('''\
def load(mod_name):
    return __import__(mod_name)
''', "动态执行")
        self.assertTrue(len(issues) >= 1, f"__import__ 漏报: {issues}")

    def test_safe_compile_never_exec(self):
        # 安全：compile 但从不 exec → 不报（或低危可报，此处仅确保不崩）
        issues = issues_of('''\
def check(src):
    try:
        compile(src, "<s>", "exec")
        return True
    except SyntaxError:
        return False
''', "动态执行")
        self.assertTrue(len(issues) <= 1, f"compile-only 过度报: {issues}")


class TimingRandomTest(unittest.TestCase):
    """时序攻击/弱随机数"""

    def test_password_eq(self):
        issues = issues_of('''\
def check(user, pwd):
    if user.password == pwd:
        return True
    return False
''', "时序攻击")
        self.assertTrue(len(issues) >= 1, f"== 比较密码漏报: {issues}")

    def test_random_token(self):
        issues = issues_of('''\
import random
def gen_token():
    return str(random.randint(100000, 999999))
''', "不安全随机数")
        self.assertTrue(len(issues) >= 1, f"random 安全场景漏报: {issues}")


class RedosTest(unittest.TestCase):
    """ReDoS（CWE-1333）"""

    def test_nested_quantifier(self):
        issues = issues_of('''\
import re
def check(s):
    return re.match(r"(a+)+$", s)
''', "ReDoS")
        self.assertTrue(len(issues) >= 1, f"嵌套量词漏报: {issues}")

    def test_safe_regex(self):
        # 安全：普通正则 → 不报
        issues = issues_of('''\
import re
def check(s):
    return re.match(r"^[a-z0-9]+$", s)
''', "ReDoS")
        self.assertEqual([], issues, f"普通正则误报: {issues}")


class FalsePositiveGuardTest(unittest.TestCase):
    """误报治理 · 安全代码必须不报"""

    def test_docstring_os_system(self):
        # 文档字符串里的 os.system 字样 → 不报
        issues = issues_of('''\
"""This module shows how NOT to use os.system("rm -rf /") in docs."""
def add(a, b):
    return a + b
''', "命令注入")
        self.assertEqual([], issues, f"文档字符串误报: {issues}")

    def test_string_literal_os_system(self):
        # 字符串字面量（非执行）→ 不报
        issues = issues_of('''\
CMD_HELP = "Usage: os.system(command) is dangerous. See docs."
''', "命令注入")
        self.assertEqual([], issues, f"字面量误报: {issues}")

    def test_logging_sql_keyword(self):
        # 日志里带 SQL 字样 → 不报
        issues = issues_of('''\
def log_query(q):
    logger.info("executing query: %s", q)
''', "SQL注入")
        self.assertEqual([], issues, f"日志误报: {issues}")


if __name__ == "__main__":
    unittest.main()
