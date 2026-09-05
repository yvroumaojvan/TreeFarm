# -*- coding: utf-8 -*-
"""
树场 v4.9.4 误报治理测试套件（unittest，零依赖）

背景：别的 AI 拿 TreeFarm v4.9.3 跑 BugsInPy tornado（顶级开源项目金标准），
116 文件扫出 100/100 F 分、六维健康度安全/逻辑/性能/质量全 0.0——大量误报。

修复（v4.9.4，analysis.py + core.py）：
  A. 污点来源分级（param 弱 < concat 拼接 < user 输入源），纯函数参数不直接报
     （template.execute(add=add)、def open(self, *args) 误报总根源）
  B. 命令注入：列表形态 Popen([sys.executable] + argv) 豁免
  C. 路径遍历：\bopen 词边界 + def 排除 + .open( 排除（Popen/develop 不再误伤）
  D. CRLF：只认真用户输入源拼接头；request.headers[ 自写豁免
  E. 临时文件：删 /tmp/xxx 字符串宽泛规则，只认 open(.../tmp/...)
  F. 开放重定向：删 url\b/next\b 参数名触发，改污点层强污点接管
  G. 除零："%s" % var 字符串格式化 ≠ 取模除法（web.py "%r" % value 误报）
  H. 竞态：RLock/Lock 同步原语不算并发源；异步文件线程池 offload 不算
  I. 展示层：测试代码问题 scope=test 移出核心段（严重/高危不混入）

运行：
  python3 -m unittest discover -s scripts/tests -v
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from treefarm.analysis import detect_security_issues  # noqa: E402
from treefarm.analysis import detect_logic_issues  # noqa: E402


def make_file(content: str) -> str:
    fd, path = tempfile.mkstemp(suffix=".py", prefix="treefarm_v494_")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def security_issues(content):
    """跑安全检测，返回全部 issues。"""
    path = make_file(content)
    try:
        return detect_security_issues([path])["issues"]
    finally:
        os.unlink(path)


def logic_issues(content):
    """跑逻辑检测，返回全部 issues。"""
    path = make_file(content)
    try:
        return detect_logic_issues([path])["issues"]
    finally:
        os.unlink(path)


def types_of(issues, t):
    return [i for i in issues if i["type"] == t]


class SqlTornadoScenarioTest(unittest.TestCase):
    """SQL 注入误报：tornado template.py:73 / blog.py:54 两处金标准场景。"""

    def test_template_execute_docstring_example(self):
        # template.py:73 文档示例：template.execute(add=add) 的 add 只是函数参数
        issues = security_issues('''\
def add(x, y):
    return x + y
template.execute(add=add)
''')
        self.assertEqual([], types_of(issues, "SQL注入"), issues)

    def test_execute_pure_param_sql_exempt(self):
        # execute(sql) 的 sql 是纯函数参数，无拼接/无用户输入源 → param 弱污点豁免
        issues = security_issues('''\
import psycopg2
async def handler(db, sql):
    async with db.cursor() as cur:
        await cur.execute(sql)
''')
        self.assertEqual([], types_of(issues, "SQL注入"), issues)

    def test_schema_static_read_exempt(self):
        # blog.py:54：schema = f.read()（读固定 schema.sql），不是用户输入 → 不报
        issues = security_issues('''\
import psycopg2
with open("schema.sql") as f:
    schema = f.read()
async def init_db(db):
    async with db.cursor() as cur:
        await cur.execute(schema)
''')
        self.assertEqual([], types_of(issues, "SQL注入"), issues)

    def test_param_concat_promoted_reported(self):
        # param 参与拼接 → concat 强污点 → 跨行拼接 SQL 必须保留检出（核心能力不丢）
        issues = security_issues('''\
import sqlite3
def search(table, key):
    conn = sqlite3.connect("app.db")
    cur = conn.cursor()
    sql = "SELECT * FROM " + table + " WHERE k='" + key + "'"
    cur.execute(sql)
''')
        self.assertTrue(types_of(issues, "SQL注入"), issues)


class CmdInjectionTest(unittest.TestCase):
    """命令注入：列表形态豁免（autoreload.py:239），真实拼接仍报。"""

    def test_popen_list_exempt(self):
        issues = security_issues('''\
import subprocess
import sys
import os
def restart(argv):
    subprocess.Popen([sys.executable] + argv)
    os._exit(0)
''')
        self.assertEqual([], types_of(issues, "命令注入"), issues)
        # Popen 含 "open(" 子串，不得再被误报成路径遍历
        self.assertEqual([], types_of(issues, "路径遍历"), issues)

    def test_popen_string_concat_reported(self):
        issues = security_issues('''\
import subprocess
def run(cmd):
    subprocess.Popen(cmd + " && echo hacked")
''')
        self.assertTrue(types_of(issues, "命令注入"), issues)


class PathTraversalTest(unittest.TestCase):
    """路径遍历：def open 豁免（websocket.py:400），真实用户输入仍报。"""

    def test_def_open_exempt(self):
        issues = security_issues('''\
class WebSocketHandler:
    def open(self, *args, **kwargs):
        return None
''')
        self.assertEqual([], types_of(issues, "路径遍历"), issues)

    def test_open_user_input_reported(self):
        issues = security_issues('''\
def download():
    fn = request.args.get("file")
    f = open(fn, "rb")
    return f.read()
''')
        self.assertTrue(types_of(issues, "路径遍历"), issues)


class TempFileTest(unittest.TestCase):
    """临时文件：配置默认值豁免（s3server.py:53），真实 open /tmp 仍报。"""

    def test_define_default_tmp_exempt(self):
        issues = security_issues('''\
define("root_directory", default="/tmp/s3", help="Root storage directory")
define("port", default=9888)
''')
        self.assertEqual([], types_of(issues, "临时文件竞争"), issues)

    def test_open_tmp_reported(self):
        issues = security_issues('''\
def save():
    f = open("/tmp/cache.log", "w")
    f.write("x")
''')
        self.assertTrue(types_of(issues, "临时文件竞争"), issues)


class OpenRedirectTest(unittest.TestCase):
    """开放重定向：url 参数名豁免（web.py:3171），强污点目标仍报。"""

    def test_redirect_url_param_exempt(self):
        issues = security_issues('''\
class Handler:
    def redirect(self, url):
        self.redirect(url)
''')
        self.assertEqual([], types_of(issues, "开放重定向"), issues)

    def test_redirect_tainted_target_reported(self):
        issues = security_issues('''\
def goto():
    target = request.args.get("next")
    return redirect(target)
''')
        self.assertTrue(types_of(issues, "开放重定向"), issues)


class CrlfInjectionTest(unittest.TestCase):
    """CRLF：set_cookie 参数名豁免（web.py:1421），真实用户输入拼接头仍报。"""

    def test_set_cookie_exempt(self):
        issues = security_issues('''\
class Handler:
    def set_cookie(self, name, value, **kwargs):
        self.set_cookie("_xsrf", self._xsrf_token)
''')
        self.assertEqual([], types_of(issues, "CRLF注入"), issues)

    def test_header_user_input_reported(self):
        issues = security_issues('''\
def setloc():
    resp.headers["Location"] = "http://" + request.args.get("host") + "/x"
''')
        self.assertTrue(types_of(issues, "CRLF注入"), issues)


class DivideByZeroTest(unittest.TestCase):
    """除零：%s 格式化豁免（web.py 误报），真除法保留。"""

    def test_percent_format_exempt(self):
        issues = logic_issues('''\
def err(value):
    raise TypeError("Unsupported header value %r" % value)
def missing(arg_name):
    super().__init__(400, "Missing argument %s" % arg_name)
''')
        self.assertEqual([], types_of(issues, "除零风险"), issues)

    def test_real_division_reported(self):
        issues = logic_issues('''\
def ratio(a, b):
    return a / b
''')
        self.assertTrue(types_of(issues, "除零风险"), issues)


class RaceConditionTest(unittest.TestCase):
    """竞态：RLock 同步原语 / 异步线程池 offload 均不算并发源。"""

    def test_rlock_not_threads(self):
        # template.py 场景：只有 threading.RLock + 自增，无真实线程创建
        issues = logic_issues('''\
import threading
class Template:
    def __init__(self):
        self.lock = threading.RLock()
        self._indent = 0
    def push(self):
        self.lock.acquire()
        self._indent += 1
        self.lock.release()
''')
        self.assertEqual([], types_of(issues, "竞态条件"), issues)

    def test_async_executor_not_race(self):
        # ioloop.py 场景：asyncio 事件循环 + ThreadPoolExecutor offload 阻塞任务
        issues = logic_issues('''\
import asyncio
from concurrent.futures import ThreadPoolExecutor
class IOLoop:
    def __init__(self):
        self._executor = ThreadPoolExecutor(1)
        self._next_timeout = 0
    async def add_timeout(self, sec):
        self._next_timeout += sec
        await asyncio.sleep(0)
        return self._next_timeout
''')
        self.assertEqual([], types_of(issues, "竞态条件"), issues)


class TestScopedDisplayTest(unittest.TestCase):
    """展示层：测试代码的问题打 scope=test，不拉低核心严重度。"""

    def _make_test_dir(self, code):
        d = tempfile.mkdtemp(prefix="treefarm_v494_scope_")
        os.makedirs(os.path.join(d, "tests"))
        path = os.path.join(d, "tests", "pickle_test.py")
        with open(path, "w", encoding="utf-8") as f:
            f.write(code)
        return d, path

    def test_security_test_issue_scoped(self):
        d, path = self._make_test_dir("import pickle\nx = pickle.loads(data)\n")
        try:
            res = detect_security_issues([path], root=d)
            deser = [i for i in res["issues"] if i["type"] == "不安全反序列化"]
            self.assertEqual(1, len(deser), res)
            self.assertEqual("test", deser[0].get("scope"))
            # 核心严重计数为 0（不拉低 risk_score）
            self.assertEqual(0, res["severity"]["critical"], res)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_logic_test_issue_scoped(self):
        d = tempfile.mkdtemp(prefix="treefarm_v494_scope_")
        os.makedirs(os.path.join(d, "tests"))
        path = os.path.join(d, "tests", "race_test.py")
        with open(path, "w", encoding="utf-8") as f:
            f.write('''\
import threading
c = {"n": 0}
def w():
    for _ in range(10):
        c["n"] += 1
ts = [threading.Thread(target=w) for _ in range(2)]
for t in ts:
    t.start()
''')
        try:
            res = detect_logic_issues([path], root=d)
            races = [i for i in res["issues"] if i["type"] == "竞态条件"]
            self.assertTrue(races, res)
            self.assertTrue(all(i.get("scope") == "test" for i in races), races)
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()