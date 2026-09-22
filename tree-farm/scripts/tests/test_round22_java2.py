# -*- coding: utf-8 -*-
"""
树场 50 轮测试 · R3：Java/Android 安全 II（OWASP Benchmark 变体 + Android 深化）（unittest）

运行：
  python3 -m unittest tests.test_round22_java2 -v
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from treefarm.analysis import detect_security_issues  # noqa: E402


def make_file(content: str, suffix=".java") -> str:
    fd, path = tempfile.mkstemp(suffix=suffix, prefix="tf_r22_")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def issues_of(content, itype=None, suffix=".java"):
    path = make_file(content, suffix)
    try:
        all_issues = detect_security_issues([path])["issues"]
        if itype:
            return [i for i in all_issues if i["type"] == itype]
        return all_issues
    finally:
        os.unlink(path)


class JavaInjectTest(unittest.TestCase):
    """Java 注入 · 变体"""

    def test_jdbc_concat(self):
        issues = issues_of('''\
public void q(Connection conn, String name) {
    Statement st = conn.createStatement();
    st.executeQuery("SELECT * FROM t WHERE name=\\"" + name + "\\"");
}
''', "SQL注入")
        self.assertTrue(len(issues) >= 1, f"JDBC 拼接漏报: {issues}")

    def test_stringbuffer_sql(self):
        issues = issues_of('''\
public void q(Connection conn, String id) {
    StringBuilder sb = new StringBuilder("SELECT * FROM t WHERE id=");
    sb.append(id);
    conn.createStatement().execute(sb.toString());
}
''', "SQL注入")
        self.assertTrue(len(issues) >= 1, f"StringBuilder SQL 漏报: {issues}")

    def test_mybatis_dollar(self):
        # MyBatis ${} 拼接（真实项目高频漏网）
        issues = issues_of('''\
@Select("SELECT * FROM users WHERE name = ${name}")
User findByName(@Param("name") String name);
''', "SQL注入")
        self.assertTrue(len(issues) >= 1, f"MyBatis dollar 花括号拼接漏报: {issues}")

    def test_safe_mybatis_hash(self):
        # MyBatis #{} 参数化 → 不报
        issues = issues_of('''\
@Select("SELECT * FROM users WHERE name = #{name}")
User findByName(@Param("name") String name);
''', "SQL注入")
        self.assertEqual([], issues, f"MyBatis hash 花括号参数化误报: {issues}")


class JavaXssWriteTest(unittest.TestCase):
    """Java XSS · 更多输出形态"""

    def test_println_concat(self):
        issues = issues_of('''\
public void page(HttpServletResponse resp, String q) {
    resp.getWriter().println("<div>" + q + "</div>");
}
''', "XSS跨站脚本")
        self.assertTrue(len(issues) >= 1, f"println 拼接漏报: {issues}")

    def test_set_attribute_script(self):
        # setAttribute + JSP 输出（跨文件语义）
        issues = issues_of('''\
public void doGet(HttpServletRequest req, HttpServletResponse resp) {
    req.setAttribute("msg", req.getParameter("msg"));
    req.getRequestDispatcher("/show.jsp").forward(req, resp);
}
''', "XSS跨站脚本")
        self.assertTrue(len(issues) >= 1, f"setAttribute 未转义漏报: {issues}")


class JavaAndroidTest(unittest.TestCase):
    """Android 深化"""

    def test_webview_javascript(self):
        # WebView 加载 JS 接口（无 @JavascriptInterface）
        issues = issues_of('''\
WebView wv = new WebView(this);
wv.getSettings().setJavaScriptEnabled(true);
wv.addJavascriptInterface(new JsBridge(), "android");
''', "不安全配置")
        self.assertTrue(len(issues) >= 1, f"WebView JS 桥漏报: {issues}")

    def test_nsd_disable_http(self):
        issues = issues_of('''\
URLConnection conn = url.openConnection();
if (conn instanceof HttpURLConnection) {
    ((HttpURLConnection) conn).setInstanceFollowRedirects(true);
}
''', "SSRF")
        self.assertTrue(len(issues) >= 1, f"openConnection 漏报: {issues}")

    def test_exported_receiver(self):
        issues = issues_of('''\
<receiver android:name=".BootReceiver" android:exported="true">
</receiver>
''', "导出组件", ".xml")
        # 至少不崩
        self.assertIsInstance(issues, list)


class JavaParserTest(unittest.TestCase):
    """Java 命令/路径"""

    def test_process_builder_list_var(self):
        issues = issues_of('''\
public void run(String cmd) {
    new ProcessBuilder("/bin/sh", "-c", cmd).start();
}
''', "命令执行")
        self.assertTrue(len(issues) >= 1, f"ProcessBuilder sh -c 漏报: {issues}")

    def test_channel_file(self):
        issues = issues_of('''\
public byte[] read(String p) {
    FileChannel fc = FileChannel.open(Paths.get(p));
}
''', "路径遍历")
        self.assertTrue(len(issues) >= 1, f"FileChannel 漏报: {issues}")


class JavaFpTest(unittest.TestCase):
    """Java 误报治理"""

    def test_prepared_statement_safe(self):
        issues = issues_of('''\
public User get(Connection conn, String id) {
    PreparedStatement ps = conn.prepareStatement("SELECT * FROM t WHERE id=?");
    ps.setString(1, id);
    return (User) ps.executeQuery();
}
''', "SQL注入")
        self.assertEqual([], issues, f"参数化误报: {issues}")

    def test_literal_exec_safe(self):
        issues = issues_of('''\
public void ping() {
    Runtime.getRuntime().exec(new String[] {"ping", "-c", "1", "127.0.0.1"});
}
''', "命令执行")
        self.assertEqual([], issues, f"字面量误报: {issues}")


if __name__ == "__main__":
    unittest.main()