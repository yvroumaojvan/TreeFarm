# -*- coding: utf-8 -*-
"""
树场 v4.9.3 SQL 注入误报治理测试套件（unittest，零依赖）

背景：别的 AI 测试报告 + 本地 sql对照靶场实测暴露——参数化查询
`execute("...%s", params)` 和纯静态 SQL `execute("SELECT ...")` 被污点分析
误报为 SQL 注入（函数参数全标污点 + 子串匹配 + 无 ? 即报 三重叠加）。

修复（v4.9.3，analysis.py 污点层）：
  A. 单行拼接形态（f"/"+/%/.format）基础规则已覆盖 → 污点层跳过防重复刷屏
  B. 参数化绑定 execute(sql, params) 逗号分隔第二参数 → 豁免
  C. 纯静态 SQL 字符串字面量 → 豁免
  D. 跨行拼接 execute(query)（query = "..." + 污点）→ 保留必报

运行：
  python3 -m unittest discover -s scripts/tests -v
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from treefarm.analysis import detect_security_issues  # noqa: E402


def make_file(content: str) -> str:
    fd, path = tempfile.mkstemp(suffix=".py", prefix="treefarm_v493_")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def sql_issues(content):
    """跑安全检测，返回 SQL注入 类型的 issues。"""
    path = make_file(content)
    try:
        return [i for i in detect_security_issues([path])["issues"]
                if i["type"] == "SQL注入"]
    finally:
        os.unlink(path)


class SqlSafeFormsTest(unittest.TestCase):
    """安全写法：参数化查询 / 纯静态 SQL 一律不许误报。"""

    def test_parameterized_literal_first_arg(self):
        issues = sql_issues('''\
import sqlite3
def get_user(uid):
    conn = sqlite3.connect("app.db")
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE id = %s", uid)
    return cur.fetchone()
''')
        self.assertEqual([], issues, issues)

    def test_parameterized_variable_sql_and_bind(self):
        issues = sql_issues('''\
import sqlite3
def search_entries(keyword):
    conn = sqlite3.connect("app.db")
    cur = conn.cursor()
    stmt = "SELECT * FROM entries WHERE title LIKE %s"
    cur.execute(stmt, keyword)
    return cur.fetchall()
''')
        self.assertEqual([], issues, issues)

    def test_static_sql_no_variables(self):
        issues = sql_issues('''\
import sqlite3
def count_all():
    conn = sqlite3.connect("app.db")
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM entries LIMIT 1")
    return cur.fetchone()
''')
        self.assertEqual([], issues, issues)

    def test_placeholder_q_mark_style(self):
        issues = sql_issues('''\
import sqlite3
def get_post(post_id):
    conn = sqlite3.connect("app.db")
    cur = conn.cursor()
    cur.execute("SELECT * FROM posts WHERE id = ?", (post_id,))
    return cur.fetchone()
''')
        self.assertEqual([], issues, issues)


class SqlDangerFormsTest(unittest.TestCase):
    """危险写法：拼接 SQL 必须报，且同文件同行只报一次。"""

    def test_string_concat_inline(self):
        issues = sql_issues('''\
import sqlite3
def login(username, password):
    conn = sqlite3.connect("app.db")
    cur = conn.cursor()
    query = "SELECT * FROM users WHERE name = '" + username + "'"
    cur.execute(query)
    return cur.fetchone()
''')
        self.assertTrue(len(issues) >= 1, issues)

    def test_fstring_inline_reported_once(self):
        issues = sql_issues('''\
import sqlite3
def get_user_by_name(name):
    conn = sqlite3.connect("app.db")
    cur = conn.cursor()
    cur.execute(f"SELECT * FROM users WHERE name = '{name}'")
    return cur.fetchone()
''')
        self.assertEqual(1, len(issues), issues)  # 基础层报 1 次，污点层去重

    def test_cross_line_concat_still_detected(self):
        # v4.5 污点分析的核心价值：跨行拼接必须保留检出
        issues = sql_issues('''\
import sqlite3
def admin_delete(table, key):
    conn = sqlite3.connect("app.db")
    cur = conn.cursor()
    sql = "DELETE FROM " + table + " WHERE k = '" + key + "'"
    cur.execute(sql)
''')
        self.assertTrue(len(issues) >= 1, issues)


if __name__ == "__main__":
    unittest.main()
