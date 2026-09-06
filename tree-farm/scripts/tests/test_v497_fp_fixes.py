# -*- coding: utf-8 -*-
"""
树场 v4.9.7 误报/漏报治理测试套件（unittest，零依赖）

来源：TraeAI v4.9.6 金标准复测报告 OWASP 对抗靶场（已本地实锤逐项复核）：
  sample9 参数化 SQL 误报（★最重要）：execute("静态SQL模板", (绑定参数,)) 中
    「+」出现在参数值里（("%" + kw + "%",)），SQL 模板本身无拼接 → 安全写法，
    之前被 1428 行基础正则 + 1666 行增强正则双重误报为「SQL注入 15/B」
  sample3 os.system 拼接漏报：os.system("ping " + cmd) 引号开头拼接形态
    被 [^"'] 挡掉（只覆盖变量直传形态）
  sample5 meta refresh 开放重定向漏报：<meta http-equiv="refresh" content="0;url=" + t>
    形态未覆盖（且要容忍 HTML 属性转义引号 \"）

修复（v4.9.7，analysis.py）：
  A. 基础层 + 增强层 SQL 注入同步加 _param_bind_exempt() 参数化绑定豁免
  B. cmd_patterns 补 os.system/os.popen/subprocess 引号拼接形态（单文件+跨文件层）
  C. open_redirect_patterns 补 meta refresh 形态（容忍 \\* 转义引号）

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
    fd, path = tempfile.mkstemp(suffix=".py", prefix="treefarm_v497_")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def issues_of(content, itype=None):
    """跑安全检测，返回指定类型（或全部）的 issues。"""
    path = make_file(content)
    try:
        all_issues = detect_security_issues([path])["issues"]
        if itype:
            return [i for i in all_issues if i["type"] == itype]
        return all_issues
    finally:
        os.unlink(path)


class ParamBindSqlNoFpTest(unittest.TestCase):
    """参数化绑定 SQL：SQL 模板静态、拼接在参数值里 → 严禁误报。"""

    def test_sample9_like_placeholder_bind(self):
        # TraeAI 对照样例 sample9：db.execute("... LIKE ?", ("%" + kw + "%",))
        issues = issues_of('''\
def search(db, keyword):
    # parameterized safe form
    rows = db.execute("SELECT * FROM products WHERE name LIKE ?", ("%" + keyword + "%",))
    return rows
''', "SQL注入")
        self.assertEqual([], issues, issues)

    def test_named_placeholder_bind(self):
        issues = issues_of('''\
import sqlite3
def get(name):
    conn = sqlite3.connect("app.db")
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE name = ?", (name,))
    return cur.fetchone()
''', "SQL注入")
        self.assertEqual([], issues, issues)

    def test_multiple_params_tuple(self):
        issues = issues_of('''\
def q(db, a, b):
    rows = db.execute("SELECT * FROM t WHERE x = ? AND y = ?", (a, b))
    return rows
''', "SQL注入")
        self.assertEqual([], issues, issues)

    def test_static_template_with_percent_bind_value(self):
        # 占位符 ? 模板 + 参数值里的 % 拼接（sample9 变体）→ 安全
        issues = issues_of('''\
def q(db, kw):
    return db.execute("SELECT * FROM p WHERE name LIKE ?", ("%100%" + kw,))
''', "SQL注入")
        self.assertEqual([], issues, issues)


class RealSqlConcatStillReportedTest(unittest.TestCase):
    """真危险 SQL 拼接：模板本身拼接用户输入 → 必须还报。"""

    def test_first_arg_concat_var(self):
        # 第一参数（SQL 模板）内拼接污点 → 必报
        issues = issues_of('''\
def login(username):
    cur.execute("SELECT * FROM users WHERE name = '" + username + "'")
''', "SQL注入")
        self.assertTrue(len(issues) >= 1, issues)

    def test_fstring_first_arg(self):
        issues = issues_of('''\
def get_user(user_id):
    cur.execute(f"SELECT * FROM users WHERE id = {user_id}")
''', "SQL注入")
        self.assertTrue(len(issues) >= 1, issues)

    def test_cross_line_concat(self):
        issues = issues_of('''\
def admin_delete(table, key):
    conn = sqlite3.connect("app.db")
    cur = conn.cursor()
    sql = "DELETE FROM " + table + " WHERE k = '" + key + "'"
    cur.execute(sql)
''', "SQL注入")
        self.assertTrue(len(issues) >= 1, issues)


class OsSystemInjectionTest(unittest.TestCase):
    """os.system 命令注入：引号拼接形态必须捕获。"""

    def test_sample3_os_system_concat(self):
        # TraeAI 样例 sample3：os.system("ping " + cmd)
        issues = issues_of('''\
import os

def run(cmd):
    result = os.system("ping " + cmd)
    return result
''', "命令注入")
        self.assertTrue(len(issues) >= 1, issues)

    def test_os_popen_concat(self):
        issues = issues_of('''\
import os

def run(cmd):
    return os.popen("cat " + cmd).read()
''', "命令注入")
        self.assertTrue(len(issues) >= 1, issues)

    def test_subprocess_call_string_concat(self):
        issues = issues_of('''\
import subprocess

def run(cmd):
    subprocess.call("ls -la " + cmd)
''', "命令注入")
        self.assertTrue(len(issues) >= 1, issues)

    def test_os_system_static_not_reported(self):
        # 纯静态字符串 → 不能报
        issues = issues_of('''\
import os

def run():
    os.system("ping 127.0.0.1")
''', "命令注入")
        self.assertEqual([], issues, issues)


class MetaRefreshOpenRedirectTest(unittest.TestCase):
    """meta refresh 开放重定向：拼接形态捕获，静态形态不报。"""

    def test_sample5_meta_refresh_concat(self):
        # TraeAI 样例 sample5（含 HTML 属性转义引号 \"）
        issues = issues_of('''\
def render_redirect(request):
    target = request.args.get("next")
    return "<html><meta http-equiv=\\"refresh\\" content=\\"0;url=" + target + "\\"></html>"
''', "开放重定向")
        self.assertTrue(len(issues) >= 1, issues)

    def test_meta_refresh_static_not_reported(self):
        # 静态 meta refresh（无拼接、无变量）→ 不报
        issues = issues_of('''\
def page():
    return '<html><meta http-equiv="refresh" content="0;url=/home"></html>'
''', "开放重定向")
        self.assertEqual([], issues, issues)

    def test_meta_refresh_fstring_var(self):
        issues = issues_of('''\
def render(request):
    target = request.args.get("next")
    return f'<meta http-equiv="refresh" content="0;url={target}">'
''', "开放重定向")
        self.assertTrue(len(issues) >= 1, issues)

    def test_meta_refresh_bind_var_no_concat(self):
        # 已知局限（v4.9.7 不追）：url= 前缀被拆进局部变量 body 再拼模板，
        # 属「字符串常量语义传播」，需语义级分析——当前引擎单行正则 + 简单
        # 污点变量名匹配抓不到。宁漏不误（报得准 > 报得全）。
        issues = issues_of('''\
def render(request):
    target = request.args.get("next")
    body = 'url=' + target
    return '<meta http-equiv="refresh" content="0;' + body + '">'
''', "开放重定向")
        self.assertEqual([], issues, "已知局限：跨行语义传播形态暂不追（宁漏不误）")


class CrossFileRegressionTest(unittest.TestCase):
    """跨文件污点：v4.9.5 能力不能因豁免回归。"""

    def test_cross_file_sql_inject_still_detected(self):
        d = tempfile.mkdtemp(prefix="treefarm_v497_")
        try:
            app = os.path.join(d, "app.py")
            routes = os.path.join(d, "routes.py")
            with open(app, "w", encoding="utf-8") as f:
                f.write('''\
from routes import search

def handle(request, db):
    keyword = request.args.get("q")
    return search(db, keyword)
''')
            with open(routes, "w", encoding="utf-8") as f:
                f.write('''\
def search(db, keyword):
    query = "SELECT * FROM products WHERE name LIKE '%" + keyword + "%'"
    return db.execute(query)
''')
            issues = detect_security_issues([app, routes])["issues"]
            sql_issues = [i for i in issues if i["type"] == "SQL注入"]
            self.assertTrue(any("routes.py" in i["file"] for i in sql_issues), sql_issues)
        finally:
            import shutil
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()