# -*- coding: utf-8 -*-
"""
树场 20 轮测试 · 第 4 轮：跨文件污点传播复杂链（unittest，零依赖）

目标：测跨文件污点引擎在「复杂真实场景」的短板——三层透传 / from 别名 /
包装函数 / 条件透传 / 循环透传 / 误报治理。

运行：
  python3 -m unittest tests.test_round4_cross_file_chain -v
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from treefarm.analysis import detect_security_issues  # noqa: E402


def project_issues(files):
    d = tempfile.mkdtemp(prefix="treefarm_r4_")
    try:
        paths = []
        for name, content in files.items():
            p = os.path.join(d, name)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                f.write(content)
            paths.append(p)
        return detect_security_issues(paths, root=d)["issues"]
    finally:
        shutil.rmtree(d, ignore_errors=True)


def issues_of_type(files, itype):
    return [i for i in project_issues(files) if i["type"] == itype]


class ThreeLayerChainTest(unittest.TestCase):
    """三层透传链 a→b→c：a 强污点实参 → b 透传 → c 流入 sink"""

    def test_three_layer_sql(self):
        issues = issues_of_type({
            "a.py": '''\
import b


def handler(request):
    q = request.args.get("q")
    b.entry(q)
''',
            "b.py": '''\
import c


def entry(data):
    c.query(data)
''',
            "c.py": '''\
import sqlite3


def query(sql):
    conn = sqlite3.connect("db.sqlite")
    conn.execute(sql)
''',
        }, "SQL注入")
        self.assertTrue(len(issues) >= 1, f"三层透传 SQL 漏报: {issues}")
        self.assertTrue(any("跨文件" in i["desc"] for i in issues),
                        f"无跨文件溯源: {issues}")


class FromImportChainTest(unittest.TestCase):
    """from 导入别名 + 包装函数"""

    def test_from_import_direct(self):
        issues = issues_of_type({
            "a.py": '''\
from db import run_query


def handler(request):
    run_query(request.args.get("q"))
''',
            "db.py": '''\
def run_query(sql):
    cursor.execute(sql)
''',
        }, "SQL注入")
        self.assertTrue(len(issues) >= 1, f"from 导入直传漏报: {issues}")

    def test_from_import_alias(self):
        issues = issues_of_type({
            "a.py": '''\
from db import run_query as rq


def handler(request):
    rq(request.form.get("q"))
''',
            "db.py": '''\
def run_query(sql):
    cursor.execute(sql)
''',
        }, "SQL注入")
        self.assertTrue(len(issues) >= 1, f"from 别名漏报: {issues}")


class WrapperChainTest(unittest.TestCase):
    """包装函数：b 把参数包一层再传 c"""

    def test_wrapper_passthrough(self):
        issues = issues_of_type({
            "a.py": '''\
import lib


def handler(request):
    lib.wrap(request.args.get("q"))
''',
            "lib.py": '''\
import core


def wrap(x):
    return core.sink(x)
''',
            "core.py": '''\
def sink(sql):
    execute(sql)
''',
        }, "SQL注入")
        self.assertTrue(len(issues) >= 1, f"包装函数透传漏报: {issues}")


class ConditionalChainTest(unittest.TestCase):
    """条件透传：if 分支里 return 参数"""

    def test_conditional_return(self):
        issues = issues_of_type({
            "a.py": '''\
import b


def handler(request):
    b.maybe(request.args.get("q"))
''',
            "b.py": '''\
import sqlite3


def maybe(x):
    if x:
        conn = sqlite3.connect("db.sqlite")
        conn.execute(x)
    return None
''',
        }, "SQL注入")
        self.assertTrue(len(issues) >= 1, f"条件分支 sink 漏报: {issues}")


class NoFalsePositiveTest(unittest.TestCase):
    """误报治理：无强污点实参的调用不报"""

    def test_string_literal_call(self):
        issues = issues_of_type({
            "a.py": '''\
import b


def handler():
    b.exec_sql("SELECT 1")
''',
            "b.py": '''\
def exec_sql(sql):
    execute(sql)
''',
        }, "SQL注入")
        self.assertEqual([], issues, f"字符串字面量误报: {issues}")

    def test_param_weak_call(self):
        # 调用方实参是普通局部变量（非用户源）→ 不报
        issues = issues_of_type({
            "a.py": '''\
import b


def handler():
    x = "static"
    b.exec_sql(x)
''',
            "b.py": '''\
def exec_sql(sql):
    execute(sql)
''',
        }, "SQL注入")
        self.assertEqual([], issues, f"普通变量误报: {issues}")

    def test_orm_exempt(self):
        # ORM 白名单豁免
        issues = issues_of_type({
            "a.py": '''\
import b


def handler(request):
    b.find(request.args.get("q"))
''',
            "b.py": '''\
def find(name):
    return session.query(User).filter(User.name == name).all()
''',
        }, "SQL注入")
        self.assertEqual([], issues, f"ORM 误报: {issues}")

    def test_param_bind_exempt(self):
        # 参数化绑定豁免（跨文件）
        issues = issues_of_type({
            "a.py": '''\
import b


def handler(request):
    b.find(request.args.get("q"))
''',
            "b.py": '''\
def find(name):
    return cursor.execute("SELECT * FROM t WHERE name = ?", (name,))
''',
        }, "SQL注入")
        self.assertEqual([], issues, f"参数化误报: {issues}")


class TaintSourceVariantsTest(unittest.TestCase):
    """污点源变体：跨文件调用时用户源的各种写法"""

    def test_sys_argv_source(self):
        issues = issues_of_type({
            "a.py": '''\
import sys
import b

q = sys.argv[1]
b.exec_sql(q)
''',
            "b.py": '''\
def exec_sql(sql):
    execute(sql)
''',
        }, "SQL注入")
        self.assertTrue(len(issues) >= 1, f"sys.argv 源漏报: {issues}")

    def test_request_json_source(self):
        issues = issues_of_type({
            "a.py": '''\
import b


def handler(request):
    data = request.json.get("q")
    b.exec_sql(data)
''',
            "b.py": '''\
def exec_sql(sql):
    execute(sql)
''',
        }, "SQL注入")
        self.assertTrue(len(issues) >= 1, f"request.json 源漏报: {issues}")


class CrossFilePathCmdTest(unittest.TestCase):
    """跨文件路径遍历 / 命令注入"""

    def test_cross_file_path(self):
        issues = issues_of_type({
            "a.py": '''\
import b


def handler(request):
    b.read(request.args.get("f"))
''',
            "b.py": '''\
def read(fname):
    return open(fname).read()
''',
        }, "路径遍历")
        self.assertTrue(len(issues) >= 1, f"跨文件路径遍历漏报: {issues}")

    def test_cross_file_cmd(self):
        issues = issues_of_type({
            "a.py": '''\
import b


def handler(request):
    b.run(request.args.get("c"))
''',
            "b.py": '''\
import os


def run(cmd):
    os.system(cmd)
''',
        }, "命令注入")
        self.assertTrue(len(issues) >= 1, f"跨文件命令注入漏报: {issues}")


if __name__ == "__main__":
    unittest.main()
