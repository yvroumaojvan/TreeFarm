# -*- coding: utf-8 -*-
"""
树场 20 轮测试 · 第 18 轮：全能力综合对抗（unittest，零依赖）

目标：混合项目（Python+JS+Java+HTML）同时含多种真实漏洞 + 大量安全代码，
验证 --all-checks 全开时：该报的全报、不该报的不报、报告结构完整。

运行：
  python3 -m unittest tests.test_round18_mixed -v
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tree_farm as tf  # noqa: E402


class MixedProjectTest(unittest.TestCase):
    """综合对抗：一个项目里 4 种语言、5 类真漏洞、5 类安全写法"""

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="treefarm_r18_")
        files = {
            "api.py": '''\
import sqlite3
import os


def login(username, password):
    # 真漏洞 1：SQL 拼接
    conn = sqlite3.connect("app.db")
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE name = '" + username + "'")
    return cur.fetchone()


def safe_query(user_id):
    # 安全写法：参数化
    conn = sqlite3.connect("app.db")
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    return cur.fetchone()


def run(cmd):
    # 真漏洞 2：命令注入
    os.system(cmd)


def render(name):
    # 安全写法：转义
    import html
    return "<div>" + html.escape(name) + "</div>"
''',
            "app.js": '''\
const fs = require("fs");

function read(fname) {
    // 真漏洞 3：路径遍历
    return fs.readFileSync("/var/data/" + fname);
}

function safeRead() {
    // 安全写法：固定路径
    return fs.readFileSync("/etc/hostname");
}

function show(name) {
    // 真漏洞 4：XSS
    document.getElementById("out").innerHTML = name;
}
''',
            "Config.java": '''\
public class Config {
    // 真漏洞 5：硬编码凭据
    private static final String API_KEY = "sk_live_51HxXyZ1234567890";

    public void connect(String url) throws Exception {
        // 真漏洞 6：SSRF
        java.net.HttpURLConnection conn = (java.net.HttpURLConnection)
            new java.net.URL(url).openConnection();
    }

    public void safeConnect() throws Exception {
        // 安全写法：固定 URL
        new java.net.URL("https://api.github.com/zen").openStream();
    }
}
''',
            "page.html": '''\
<!DOCTYPE html>
<html>
<body>
<script>
// 真漏洞 7：前端动态执行
function go() {
    eval(location.hash.slice(1));
}
</script>
</body>
</html>
''',
            "safe.py": '''\
# 纯安全文件：大量安全写法，必须零报
import json


def load_config(path):
    with open(path) as f:
        return json.load(f)


def find_user(session, name):
    return session.query(User).filter(User.name == name).all()


def dedupe(items, seen):
    out = []
    for x in items:
        if x in seen:
            continue
        seen.add(x)
        out.append(x)
    return out
''',
        }
        for name, content in files.items():
            with open(os.path.join(self.d, name), "w", encoding="utf-8") as f:
                f.write(content)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_all_checks_finds_all_types(self):
        tf_inst = tf.TreeFarm(self.d)
        tf_inst.plant()
        out = tf_inst.all_checks()
        # 7 类漏洞类型都应出现
        for kw in ("SQL注入", "命令注入", "路径遍历", "XSS跨站脚本",
                   "硬编码凭据", "SSRF", "动态执行"):
            self.assertIn(kw, out, f"缺 {kw} 检测: {out[:500]}")

    def test_security_detects_all(self):
        import tree_farm as tff
        files = [os.path.join(self.d, f) for f in
                 ("api.py", "app.js", "Config.java", "page.html", "safe.py")]
        res = tff.detect_security_issues(files, root=self.d)
        types = {i["type"] for i in res["issues"]}
        for t in ("SQL注入", "命令注入", "路径遍历", "XSS跨站脚本",
                  "硬编码凭据", "SSRF", "动态执行"):
            self.assertIn(t, types, f"缺 {t}: {types}")

    def test_safe_file_clean(self):
        import tree_farm as tff
        safe_path = os.path.join(self.d, "safe.py")
        res = tff.detect_security_issues([safe_path], root=self.d)
        self.assertEqual([], res["issues"], f"纯安全文件误报: {res['issues']}")


if __name__ == "__main__":
    unittest.main()
