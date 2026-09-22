# -*- coding: utf-8 -*-
"""
树场 35 轮测试 · R25：综合对抗（unittest）

运行：
  python3 -m unittest tests.test_round39_combined -v
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tree_farm as tf  # noqa: E402


class CombinedTest(unittest.TestCase):
    """四语言混合项目：安全+逻辑+性能全开"""

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="tf_r39_")
        files = {
            "api.py": '''\
import sqlite3
import os


def login(username, password):
    conn = sqlite3.connect("app.db")
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE name = '" + username + "'")
    return cur.fetchone()


def run(cmd):
    os.system(cmd)


def read(fname):
    return open("/var/data/" + fname).read()


def build(items):
    s = ""
    for i in items:
        s += str(i)
    return s
''',
            "app.js": '''\
const { exec } = require("child_process");
function runCmd(cmd) {
    exec("ls " + cmd);
}
''',
            "Config.java": '''\
public class Config {
    public void connect(String url) throws Exception {
        new java.net.URL(url).openStream();
    }
}
''',
            "page.html": '''\
<html><body><script>
function go() {
    eval(location.hash.slice(1));
}
</script></body></html>
''',
        }
        for name, content in files.items():
            with open(os.path.join(self.d, name), "w", encoding="utf-8") as f:
                f.write(content)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_security_all_types(self):
        import tree_farm as tff
        files = [os.path.join(self.d, f) for f in
                 ("api.py", "app.js", "Config.java", "page.html")]
        res = tff.detect_security_issues(files, root=self.d)
        types = {i["type"] for i in res["issues"]}
        for t in ("SQL注入", "命令注入", "SSRF", "动态执行", "路径遍历"):
            self.assertIn(t, types, f"缺 {t}: {types}")

    def test_logic_and_perf(self):
        tf_inst = tf.TreeFarm(self.d)
        tf_inst.plant()
        # 逻辑：无重要逻辑问题（api.py 无逻辑 bug 用例）——不全笼统
        l = tf.detect_logic_issues([os.path.join(self.d, "api.py")])
        self.assertIsInstance(l["issues"], list)
        # 性能：循环内拼接应命中
        p = tf.detect_performance_issues([os.path.join(self.d, "api.py")])
        types = {i["type"] for i in p["issues"]}
        self.assertIn("循环内字符串拼接", types, f"性能拼接漏报: {types}")


if __name__ == "__main__":
    unittest.main()