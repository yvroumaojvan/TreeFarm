# -*- coding: utf-8 -*-
"""
树场 20 轮测试 · 第 5 轮：误报治理对抗（unittest，零依赖）

目标：验证「宁缺毋滥」防线——安全代码/正常写法绝不误报。
用例来源（权威）：OWASP 安全编码实践、tornado 金标准误报治理成果、
常见正常业务代码形态。

所有用例期望 0 报。若有真漏洞混入被报也接受，但安全部分必须静默。

运行：
  python3 -m unittest tests.test_round5_fp_guard -v
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from treefarm.analysis import detect_security_issues  # noqa: E402


def make_file(content: str, suffix=".py") -> str:
    fd, path = tempfile.mkstemp(suffix=suffix, prefix="treefarm_r5_")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def issues_of(content, suffix=".py"):
    path = make_file(content, suffix)
    try:
        return detect_security_issues([path])["issues"]
    finally:
        os.unlink(path)


class SafeSqlPatternsTest(unittest.TestCase):
    """安全 SQL 写法"""

    def test_param_bind_qmark(self):
        self.assertEqual([], issues_of('''\
import sqlite3


def get_user(name):
    conn = sqlite3.connect("app.db")
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE name = ?", (name,))
    return cur.fetchone()
'''), "参数化 ? 误报")

    def test_param_bind_named(self):
        self.assertEqual([], issues_of('''\
def q(conn, uid):
    return conn.execute("SELECT * FROM t WHERE id = :id", {"id": uid})
'''), "命名参数误报")

    def test_orm_filter(self):
        self.assertEqual([], issues_of('''\
def find(session, name):
    return session.query(User).filter(User.name == name).all()
'''), "ORM 误报")

    def test_logging_query(self):
        self.assertEqual([], issues_of('''\
def log_query(q):
    logger.info("executing: %s", q)
'''), "日志误报")


class SafeCmdPatternsTest(unittest.TestCase):
    """安全命令执行写法"""

    def test_subprocess_list_no_shell(self):
        self.assertEqual([], issues_of('''\
import subprocess


def run(args):
    return subprocess.run(["git", "status"], capture_output=True)
'''), "subprocess 列表误报")

    def test_os_system_literal(self):
        # 纯字面量 os.system（虽然不推荐，但不是注入）
        self.assertEqual([], issues_of('''\
import os


def reboot():
    os.system("reboot")
'''), "os.system 字面量误报")

    def test_popen_list(self):
        self.assertEqual([], issues_of('''\
import subprocess


def ping():
    return subprocess.Popen(["ping", "-c", "1", "127.0.0.1"])
'''), "Popen 列表误报")


class SafeUrlPatternsTest(unittest.TestCase):
    """安全网络请求写法"""

    def test_requests_const(self):
        self.assertEqual([], issues_of('''\
import requests


def ping():
    return requests.get("https://api.github.com/zen", timeout=5)
'''), "固定 URL 误报")

    def test_urllib_request_object(self):
        self.assertEqual([], issues_of('''\
import urllib.request


def fetch():
    req = urllib.request.Request("https://example.com/data.json")
    return urllib.request.urlopen(req)
'''), "Request 对象误报")


class SafeXssPatternsTest(unittest.TestCase):
    """安全前端输出写法"""

    def test_html_escape(self):
        self.assertEqual([], issues_of('''\
import html


def render(name):
    return "<div>" + html.escape(name) + "</div>"
''', ".py"), "escape 误报")

    def test_jinja_autoescape(self):
        self.assertEqual([], issues_of('''\
def render(name):
    return render_template("page.html", name=name)
'''), "render_template 误报")

    def test_text_content(self):
        self.assertEqual([], issues_of('''\
function show(name) {
    document.getElementById("out").textContent = name;
}
''', ".js"), "textContent 误报")

    def test_innerhtml_const(self):
        # innerHTML 写固定内容（无用户输入）→ 仍会报（保守），但这里验证不崩即可
        issues_of('''\
function render() {
    document.getElementById("out").innerHTML = "<p>Hello</p>";
}
''', ".js")


class SafeDeserPatternsTest(unittest.TestCase):
    """安全反序列化写法"""

    def test_yaml_safe_load(self):
        self.assertEqual([], issues_of('''\
import yaml


def parse(text):
    return yaml.safe_load(text)
'''), "safe_load 误报")

    def test_json_loads(self):
        self.assertEqual([], issues_of('''\
import json


def parse(text):
    return json.loads(text)
'''), "json.loads 误报")


class VariableNameCoincidenceTest(unittest.TestCase):
    """变量名巧合：name/path/url 等普通变量 ≠ 用户输入"""

    def test_def_open_method(self):
        # def open(...) 方法定义
        self.assertEqual([], issues_of('''\
class FileHelper:
    def open(self, path):
        return self._cache.get(path)
'''), "def open 误报")

    def test_normal_url_variable(self):
        self.assertEqual([], issues_of('''\
import requests


def monitor():
    url = "https://status.internal/health"
    return requests.get(url, timeout=2)
'''), "普通 url 变量误报")


class TestAndDocPatternsTest(unittest.TestCase):
    """测试/文档/示例代码"""

    def test_docstring_dangerous_words(self):
        self.assertEqual([], issues_of('''\
"""Security notes:
- Never call os.system(user_input) with untrusted data.
- Always use parameterized queries: execute("SELECT ... ?", (param,)).
- pickle.loads() on untrusted data is RCE.
- yaml.load() without Loader is unsafe.
"""
def add(a, b):
    return a + b
'''), "文档字符串误报")

    def test_comment_dangerous_words(self):
        self.assertEqual([], issues_of('''\
# TODO: migrate from eval() to ast.literal_eval
# FIXME: this exec path is used in legacy module
def legacy():
    return 42
'''), "注释误报")

    def test_string_help_text(self):
        self.assertEqual([], issues_of('''\
HELP = "Usage: curl -X POST http://api.example.com/v1/items -d '{\\"name\\": \\"...\\"}'"
'''), "帮助文案误报")


class RealisticBusinessCodeTest(unittest.TestCase):
    """真实业务代码（正常形态）"""

    def test_ecommerce_cart(self):
        self.assertEqual([], issues_of('''\
import logging
import time

logger = logging.getLogger(__name__)


class Cart:
    def __init__(self):
        self.items = []

    def add(self, item_id, qty):
        if qty <= 0:
            raise ValueError("qty must be positive")
        self.items.append((item_id, qty))
        logger.info("added %s x%d", item_id, qty)

    def total(self, prices):
        return sum(prices.get(i, 0) * q for i, q in self.items)

    def clear(self):
        self.items = []


def make_order(cart, user_id):
    order = {"user": user_id, "items": cart.items, "ts": int(time.time())}
    return order
'''), "正常业务代码误报")


if __name__ == "__main__":
    unittest.main()
