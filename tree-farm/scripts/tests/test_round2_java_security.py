# -*- coding: utf-8 -*-
"""
树场 20 轮测试 · 第 2 轮：Java/Android 安全地狱（unittest，零依赖）

目标：测 Java 安全扫描（_scan_java_security + java_extra_rules）的漏报/误报短板。
用例来源（权威）：
  - OWASP Benchmark v1.2 风格（Java Web：SQLi/XSS/Path/XXE/反序列化/SSRF）
  - Android 专属：SharedPreferences 明文、setTorchMode 权限、无界绑定、Binder 任意删除
  - 混淆对抗：跨行 getParameter、String.format、包装函数、转义变体

运行：
  python3 -m unittest tests.test_round2_java_security -v
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from treefarm.analysis import detect_security_issues  # noqa: E402


def make_file(content: str, suffix=".java") -> str:
    fd, path = tempfile.mkstemp(suffix=suffix, prefix="treefarm_r2_")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def issues_of(content, itype=None):
    path = make_file(content)
    try:
        all_issues = detect_security_issues([path])["issues"]
        if itype:
            return [i for i in all_issues if i["type"] == itype]
        return all_issues
    finally:
        os.unlink(path)


class JavaSqlInjectionTest(unittest.TestCase):
    """Java SQL 注入（CWE-89）"""

    def test_statement_concat(self):
        issues = issues_of('''\
public User getUser(String id) {
    Statement st = conn.createStatement();
    ResultSet rs = st.executeQuery("SELECT * FROM users WHERE id = " + id);
}
''', "SQL注入")
        self.assertTrue(len(issues) >= 1, f"Statement 拼接漏报: {issues}")

    def test_prepare_statement_concat(self):
        issues = issues_of('''\
public void login(String name) {
    PreparedStatement ps = conn.prepareStatement("SELECT * FROM users WHERE name='" + name + "'");
}
''', "SQL注入")
        self.assertTrue(len(issues) >= 1, f"prepareStatement 拼接漏报: {issues}")

    def test_cross_line_get_param_sql(self):
        # 跨行：getParameter 先赋值，再拼进 SQL
        issues = issues_of('''\
public void q(HttpServletRequest req) {
    String id = req.getParameter("id");
    Statement st = conn.createStatement();
    st.executeQuery("SELECT * FROM t WHERE id = " + id);
}
''', "SQL注入")
        self.assertTrue(len(issues) >= 1, f"跨行 getParameter SQL 漏报: {issues}")

    def test_string_format_sql(self):
        issues = issues_of('''\
public void q(String name) {
    String sql = String.format("SELECT * FROM users WHERE name = '%s'", name);
    Statement st = conn.createStatement();
    st.execute(sql);
}
''', "SQL注入")
        self.assertTrue(len(issues) >= 1, f"String.format SQL 漏报: {issues}")

    def test_safe_prepared_statement(self):
        # 安全：参数化绑定 → 不报
        issues = issues_of('''\
public User getUser(String id) {
    PreparedStatement ps = conn.prepareStatement("SELECT * FROM users WHERE id = ?");
    ps.setString(1, id);
    return ps.executeQuery();
}
''', "SQL注入")
        self.assertEqual([], issues, f"参数化误报: {issues}")


class JavaXssTest(unittest.TestCase):
    """Java XSS（CWE-79）"""

    def test_get_writer_direct(self):
        issues = issues_of('''\
public void render(HttpServletRequest req, HttpServletResponse resp) {
    String name = req.getParameter("name");
    resp.getWriter().write("<h1>Hello " + name + "</h1>");
}
''', "XSS跨站脚本")
        self.assertTrue(len(issues) >= 1, f"getWriter 输出漏报: {issues}")

    def test_cross_line_xss(self):
        # 跨行：getParameter 变量 + 拼接输出
        issues = issues_of('''\
public void page(HttpServletRequest req, HttpServletResponse resp) {
    String q = req.getParameter("q");
    resp.getWriter().println("Search: " + q);
}
''', "XSS跨站脚本")
        self.assertTrue(len(issues) >= 1, f"跨行 XSS 漏报: {issues}")

    def test_safe_escape_jsp(self):
        # 安全：已转义 → 不报
        issues = issues_of('''\
public void page(HttpServletRequest req, HttpServletResponse resp) {
    String name = req.getParameter("name");
    resp.getWriter().write("<div>" + StringEscapeUtils.escapeHtml(name) + "</div>");
}
''', "XSS跨站脚本")
        self.assertEqual([], issues, f"转义后误报: {issues}")


class JavaPathTraversalTest(unittest.TestCase):
    """Java 路径遍历（CWE-22）"""

    def test_file_concat(self):
        issues = issues_of('''\
public byte[] read(String fname) {
    return Files.readAllBytes(new File("/data/" + fname).toPath());
}
''', "路径遍历")
        self.assertTrue(len(issues) >= 1, f"File 拼接漏报: {issues}")

    def test_cross_line_path(self):
        issues = issues_of('''\
public void download(HttpServletRequest req) {
    String f = req.getParameter("file");
    FileInputStream in = new FileInputStream("/var/www/" + f);
}
''', "路径遍历")
        self.assertTrue(len(issues) >= 1, f"跨行路径遍历漏报: {issues}")

    def test_files_paths_get(self):
        issues = issues_of('''\
public void save(String name) {
    Files.write(Paths.get("/tmp/" + name), data);
}
''', "路径遍历")
        self.assertTrue(len(issues) >= 1, f"Files.write 拼接漏报: {issues}")


class JavaXxeTest(unittest.TestCase):
    """Java XXE（CWE-611）"""

    def test_document_builder_default(self):
        issues = issues_of('''\
public Document parse(InputStream xml) throws Exception {
    DocumentBuilderFactory dbf = DocumentBuilderFactory.newInstance();
    return dbf.newDocumentBuilder().parse(xml);
}
''', "XXE外部实体注入")
        self.assertTrue(len(issues) >= 1, f"DocumentBuilderFactory 漏报: {issues}")

    def test_sax_factory(self):
        issues = issues_of('''\
public void parse(InputStream in) throws Exception {
    SAXParserFactory spf = SAXParserFactory.newInstance();
    spf.newSAXParser().parse(in, handler);
}
''', "XXE外部实体注入")
        self.assertTrue(len(issues) >= 1, f"SAXParserFactory 漏报: {issues}")


class JavaDeserTest(unittest.TestCase):
    """Java 反序列化（CWE-502）"""

    def test_object_input_stream(self):
        issues = issues_of('''\
public Object load(InputStream in) throws Exception {
    ObjectInputStream ois = new ObjectInputStream(in);
    return ois.readObject();
}
''', "不安全反序列化")
        self.assertTrue(len(issues) >= 1, f"ObjectInputStream 漏报: {issues}")


class JavaSsrfTest(unittest.TestCase):
    """Java SSRF（CWE-918）"""

    def test_new_url_var(self):
        issues = issues_of('''\
public String fetch(String url) throws Exception {
    return new String(new URL(url).openStream().readAllBytes());
}
''', "SSRF")
        self.assertTrue(len(issues) >= 1, f"new URL(变量) 漏报: {issues}")

    def test_http_url_connection(self):
        issues = issues_of('''\
public void call(String target) throws Exception {
    HttpURLConnection conn = (HttpURLConnection) new URL(target).openConnection();
}
''', "SSRF")
        self.assertTrue(len(issues) >= 1, f"HttpURLConnection 漏报: {issues}")

    def test_safe_const_url(self):
        # 安全：固定 URL → 不报
        issues = issues_of('''\
public void ping() throws Exception {
    URL u = new URL("https://api.github.com/zen");
}
''', "SSRF")
        self.assertEqual([], issues, f"固定 URL 误报: {issues}")


class JavaCmdExecTest(unittest.TestCase):
    """Java 命令执行（CWE-78）"""

    def test_process_builder_dynamic(self):
        issues = issues_of('''\
public void exec(String cmd) {
    ProcessBuilder pb = new ProcessBuilder(cmd);
    pb.start();
}
''', "命令执行")
        self.assertTrue(len(issues) >= 1, f"ProcessBuilder 动态漏报: {issues}")

    def test_runtime_exec_dynamic(self):
        issues = issues_of('''\
public void run(String arg) {
    Runtime.getRuntime().exec("ls -la " + arg);
}
''', "命令执行")
        self.assertTrue(len(issues) >= 1, f"Runtime.exec 拼接漏报: {issues}")

    def test_safe_process_builder_literal(self):
        # 安全：纯字面量 → 不报
        issues = issues_of('''\
public void ls() {
    new ProcessBuilder("ls", "-l").start();
}
''', "命令执行")
        self.assertEqual([], issues, f"纯字面量误报: {issues}")


class JavaHardcodeTest(unittest.TestCase):
    """Java 硬编码凭据/弱加密（CWE-798/327）"""

    def test_password_field(self):
        issues = issues_of('''\
public class Config {
    private String password = "P@ssw0rd123!";
}
''', "硬编码凭据")
        self.assertTrue(len(issues) >= 1, f"硬编码密码漏报: {issues}")

    def test_api_key(self):
        issues = issues_of('''\
String apiKey = "sk_live_51HxXyZ1234567890abcdef";
''', "硬编码凭据")
        self.assertTrue(len(issues) >= 1, f"apiKey 漏报: {issues}")

    def test_md5_weak_crypto(self):
        issues = issues_of('''\
import java.security.MessageDigest;
public String hash(String s) throws Exception {
    return MessageDigest.getInstance("MD5").digest(s.getBytes()).toString();
}
''', "弱加密算法")
        self.assertTrue(len(issues) >= 1, f"MD5 漏报: {issues}")


class AndroidSpecificTest(unittest.TestCase):
    """Android 专属漏洞"""

    def test_shared_prefs_plaintext_token(self):
        issues = issues_of('''\
public void saveToken(Context ctx, String token) {
    ctx.getSharedPreferences("app", 0).edit().putString("api_key", token).apply();
}
''', "敏感信息存储")
        self.assertTrue(len(issues) >= 1, f"SharedPreferences 明文漏报: {issues}")

    def test_torch_mode(self):
        issues = issues_of('''\
camera.setTorchMode(true);
''', "权限提示")
        self.assertTrue(len(issues) >= 1, f"setTorchMode 漏报: {issues}")

    def test_bind_all_interfaces(self):
        issues = issues_of('''\
ServerSocket ss = new ServerSocket();
ss.bind(new InetSocketAddress("0.0.0.0", 8080));
''', "无界绑定")
        self.assertTrue(len(issues) >= 1, f"0.0.0.0 绑定漏报: {issues}")

    def test_binder_arbitrary_delete(self):
        issues = issues_of('''\
public boolean delete(String path) {
    return new File(path).delete();
}
''', "任意文件删除")
        self.assertTrue(len(issues) >= 1, f"任意删除漏报: {issues}")

    def test_blacklist_contains(self):
        # 黑名单 contains 反模式（文件级弱信号）
        issues = issues_of('''\
String[] danger = {"rm -rf", "bank", "wallet", "alipay"};
public boolean isDanger(String s) {
    for (String d : danger) {
        if (s.contains(d)) return true;
    }
    return false;
}
''', "黑名单绕过风险")
        self.assertTrue(len(issues) >= 1, f"黑名单反模式漏报: {issues}")


class JavaFalsePositiveTest(unittest.TestCase):
    """Java 误报治理"""

    def test_string_literal_command(self):
        # 字符串字面量里的命令字样 → 不报
        issues = issues_of('''\
String HELP = "Usage: Runtime.getRuntime().exec(cmd) is dangerous";
''', "命令执行")
        self.assertEqual([], issues, f"字面量误报: {issues}")

    def test_safe_prepared_statement_full(self):
        issues = issues_of('''\
public void safe(HttpServletRequest req) {
    String id = req.getParameter("id");
    PreparedStatement ps = conn.prepareStatement("SELECT * FROM t WHERE id = ?");
    ps.setString(1, id);
    ps.executeQuery();
}
''', "SQL注入")
        self.assertEqual([], issues, f"安全参数化误报: {issues}")


if __name__ == "__main__":
    unittest.main()
