# -*- coding: utf-8 -*-
"""
树场 v4.8.3 新增能力测试套件（unittest，零依赖）

覆盖（DSHA 实测反馈 → 优化）：
  1. Java 安全规则（_scan_java_security）：
     - ProcessBuilder 参数含变量 → 命中「命令执行」（DSHA ShellService#4 实测）
     - ProcessBuilder 纯字符串字面量 → 零误报
     - Runtime.exec 接变量 → 命中「命令执行」
  2. 无界绑定：
     - ServerSocket/InetSocketAddress 绑 0.0.0.0 / :: → 命中「无界绑定」
       （DSHA LanProxyService 0.0.0.0:3081 实测）
     - 绑 127.0.0.1 / localhost → 零误报
  3. 传感器高速采样：
     - registerListener 第 4 参 2(SENSOR_DELAY_FASTEST) → 命中
     - 正常采样率（1/3）→ 零误报
  4. 安全文件白名单（_is_security_core）：
     - DangerShellGuard.java / ShizukuShell.java / LanProxyService.java → True
     - DshaApp.java / Constants.java / BuildConfig.java → False

运行：
  python3 -m unittest discover -s scripts/tests -v
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from treefarm.analysis import detect_security_issues  # noqa: E402
from treefarm.core import _is_security_core  # noqa: E402


def make_java(content: str) -> str:
    fd, path = tempfile.mkstemp(suffix=".java", prefix="treefarm_v483_")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def types_of(issues, itype):
    return [i for i in issues if i["type"] == itype]


class JavaCmdExecTest(unittest.TestCase):
    """Java 命令执行规则：动态参数命中，纯字面量零误报。"""

    def _run(self, content):
        path = make_java(content)
        try:
            return detect_security_issues([path])["issues"]
        finally:
            os.unlink(path)

    def test_processbuilder_with_variable(self):
        issues = self._run('''\
import java.io.*;
public class ShellService {
    public String exec(String str) throws Exception {
        ProcessBuilder pb = new ProcessBuilder("sh", "-c", str);
        return "ok";
    }
}
''')
        self.assertEqual(len(types_of(issues, "命令执行")), 1, issues)

    def test_processbuilder_literal_only_no_false_positive(self):
        issues = self._run('''\
import java.io.*;
public class SafeCmd {
    public void run() throws Exception {
        ProcessBuilder pb = new ProcessBuilder("ls", "-l");
        pb.start();
    }
}
''')
        self.assertEqual(types_of(issues, "命令执行"), [], issues)

    def test_runtime_exec_with_variable(self):
        issues = self._run('''\
public class ExecThing {
    public void go(String cmd) throws Exception {
        Runtime.getRuntime().exec(cmd);
    }
}
''')
        self.assertEqual(len(types_of(issues, "命令执行")), 1, issues)

    def test_multi_statement_one_hit(self):
        issues = self._run('''\
public class Multi {
    public void go(String s) throws Exception {
        ProcessBuilder a = new ProcessBuilder("echo", s);
        ProcessBuilder b = new ProcessBuilder("date");
    }
}
''')
        hits = types_of(issues, "命令执行")
        self.assertEqual(len(hits), 1, issues)  # 每行只报一条


class JavaBindTest(unittest.TestCase):
    """无界绑定：0.0.0.0 / :: 命中，loopback 零误报。"""

    def _run(self, content):
        path = make_java(content)
        try:
            return detect_security_issues([path])["issues"]
        finally:
            os.unlink(path)

    def test_bind_0_0_0_0(self):
        issues = self._run('''\
import java.net.*;
public class LanProxy {
    void start() throws Exception {
        ServerSocket server = new ServerSocket();
        server.bind(new InetSocketAddress("0.0.0.0", 3081));
    }
}
''')
        self.assertEqual(len(types_of(issues, "无界绑定")), 1, issues)

    def test_loopback_no_false_positive(self):
        issues = self._run('''\
import java.net.*;
public class LocalOnly {
    void start() throws Exception {
        ServerSocket server = new ServerSocket();
        server.bind(new InetSocketAddress("127.0.0.1", 3090));
    }
}
''')
        self.assertEqual(types_of(issues, "无界绑定"), [], issues)


class JavaSensorTest(unittest.TestCase):
    """传感器高速采样：FASTEST(2) 命中，正常档零误报。"""

    def _run(self, content):
        path = make_java(content)
        try:
            return detect_security_issues([path])["issues"]
        finally:
            os.unlink(path)

    def test_fastest_hit(self):
        issues = self._run('''\
import android.hardware.*;
public class Sense {
    void read(SensorManager sm, Sensor s, SensorEventListener l) {
        sm.registerListener(l, s, 2, new Handler(Looper.getMainLooper()));
    }
}
''')
        self.assertEqual(len(types_of(issues, "传感器高速采样")), 1, issues)

    def test_normal_rate_no_false_positive(self):
        issues = self._run('''\
import android.hardware.*;
public class Sense2 {
    void read(SensorManager sm, Sensor s, SensorEventListener l) {
        sm.registerListener(l, s, 3, null);
        sm.registerListener(l, s, 1, null);
    }
}
''')
        self.assertEqual(types_of(issues, "传感器高速采样"), [], issues)


class JavaCleanFileTest(unittest.TestCase):
    """干净 Java 文件零误报（三规则都不该命中）。"""

    def _run(self, content):
        path = make_java(content)
        try:
            return detect_security_issues([path])["issues"]
        finally:
            os.unlink(path)

    def test_clean_java(self):
        issues = self._run('''\
package com.example;

public class Hello {
    private int count = 0;

    public String greet(String name) {
        return "Hello " + name + " times=" + count++;
    }
}
''')
        self.assertEqual(issues, [], issues)


class SecurityCoreWhitelistTest(unittest.TestCase):
    """安全文件白名单：关键文件强制核心，普通文件保持杂草。"""

    def test_security_files_are_core(self):
        for name in ("DangerShellGuard.java", "ShizukuShell.java",
                     "LanProxyService.java", "ExecBridge.java",
                     "HttpShellService.java"):
            self.assertTrue(_is_security_core("/proj/" + name), name)

    def test_normal_files_are_not_core(self):
        for name in ("DsaApp.java", "DshaApp.java", "Constants.java",
                     "BuildConfig.java", "MainActivity.java", "README.md"):
            self.assertFalse(_is_security_core("/proj/" + name), name)

    def test_path_segment_hint(self):
        self.assertTrue(_is_security_core("/proj/auth/Login.java"))
        self.assertTrue(_is_security_core("/proj/security/util.py"))


class JavaSecV49Test(unittest.TestCase):
    """v4.9.0 新增规则：敏感存储 / 硬编码 key / 权限提示。"""

    def _run(self, content):
        path = make_java(content)
        try:
            return detect_security_issues([path])["issues"]
        finally:
            os.unlink(path)

    def test_sharedprefs_api_key_hit(self):
        issues = self._run('''\
import android.content.*;
public class Cfg {
    void save(SharedPreferences.Editor e, String key) {
        e.putString("api_key", key).apply();
    }
}
''')
        self.assertEqual(len(types_of(issues, "敏感信息存储")), 1, issues)

    def test_sharedprefs_normal_name_no_false_positive(self):
        issues = self._run('''\
import android.content.*;
public class Cfg2 {
    void save(SharedPreferences.Editor e, String name) {
        e.putString("user_name", name).apply();
    }
}
''')
        self.assertEqual(types_of(issues, "敏感信息存储"), [], issues)

    def test_hardcoded_sk_key(self):
        issues = self._run('''\
public class Keys {
    static final String KEY = "sk-a1b2c3d4e5f6g7h8i9j0k1l2m3n";
    void go() { System.out.println(KEY); }
}
''')
        self.assertEqual(len(types_of(issues, "硬编码密钥")), 1, issues)

    def test_set_torch_mode_hint(self):
        issues = self._run('''\
import android.hardware.camera2.*;
public class Torch {
    void on(CameraManager cm, String id) throws Exception {
        cm.setTorchMode(id, true);
    }
}
''')
        self.assertEqual(len(types_of(issues, "权限提示")), 1, issues)

    def test_exec_guarded_only_by_blacklist(self):
        issues = self._run('''\
public class Bridge {
    String run(String line) {
        String param = getParam(line, "cmd", "");
        return (DangerShellGuard.isDangerous(param) && confirmEnabled())
                ? awaitConfirm(param) : ShizukuShell.exec(param);
    }
}
''')
        self.assertEqual(len(types_of(issues, "命令执行保护薄弱")), 1, issues)

    def test_plain_exec_no_weak_protection_flag(self):
        issues = self._run('''\
public class Normal {
    void run(String cmd) throws Exception {
        ProcessBuilder pb = new ProcessBuilder("sh", "-c", cmd);
        pb.start();
    }
}
''')
        self.assertEqual(types_of(issues, "命令执行保护薄弱"), [], issues)

    def test_sensitive_data_written_to_file(self):
        issues = self._run('''\
import java.nio.file.*;
public class Backup {
    void save(String apiKey) throws Exception {
        Files.write(Paths.get("/root/.dsh/.dsha-apikey"),
                    encryptKeyForBackup(apiKey).getBytes());
    }
}
''')
        self.assertEqual(len(types_of(issues, "敏感数据落盘")), 1, issues)

    def test_file_write_non_sensitive_no_false_positive(self):
        issues = self._run('''\
import java.nio.file.*;
public class Logs {
    void save(String log) throws Exception {
        Files.write(Paths.get("/root/dsh-web.log"), log.getBytes());
    }
}
''')
        self.assertEqual(types_of(issues, "敏感数据落盘"), [], issues)

    def test_blacklist_contains_antipattern(self):
        issues = self._run('''\
public class Guard {
    private static final String[] PATTERNS = {"rm -rf", "rm -r", "wipe", "erase"};
    static boolean isDangerous(String str) {
        for (String p : PATTERNS) {
            if (str.contains(p)) return true;
        }
        return false;
    }
}
''')
        self.assertEqual(len(types_of(issues, "黑名单绕过风险")), 1, issues)

    def test_blacklist_antipattern_requires_array_and_contains(self):
        issues = self._run('''\
public class Normal {
    private static final String[] NAMES = {"alice", "bob", "carol"};
    boolean known(String n) {
        for (String s : NAMES) if (n.equalsIgnoreCase(s)) return true;
        return false;
    }
}
''')
        self.assertEqual(types_of(issues, "黑名单绕过风险"), [], issues)

    def test_arbitrary_file_delete(self):
        issues = self._run('''\
import java.io.*;
public class OsService {
    public final void remove(String path) {
        new File(path).delete();
    }
}
''')
        self.assertEqual(len(types_of(issues, "任意文件删除")), 1, issues)

    def test_literal_file_delete_no_false_positive(self):
        issues = self._run('''\
import java.io.*;
public class Cleanup {
    void clean() {
        new File("/data/local/tmp/cache.tmp").delete();
    }
}
''')
        self.assertEqual(types_of(issues, "任意文件删除"), [], issues)

    def test_jiagu_shell_hint(self):
        issues = self._run('''\
package com.stub;
import android.app.Application;
public class StubApp extends Application {
    static String getDir() { return "libjiagu"; }
}
''')
        self.assertEqual(len(types_of(issues, "加固提示")), 1, issues)


class LlmEnvAutoDetectTest(unittest.TestCase):
    """v4.9.0：环境变量自动检测——OPENAI_API_KEY 复用 OPENAI_BASE_URL/MODEL。"""

    def test_openai_base_url_override(self):
        from treefarm.config import LLMClient
        import os
        fake = {
            "OPENAI_API_KEY": "sk-test123",
            "OPENAI_BASE_URL": "https://api.deepseek.com/v1",
            "OPENAI_MODEL": "deepseek-chat",
        }
        stash = {k: os.environ.get(k) for k in fake}
        try:
            os.environ.update(fake)
            llm = LLMClient("/nonexistent")
            self.assertTrue(llm.available())
            self.assertEqual(llm.base_url, "https://api.deepseek.com/v1")
            self.assertEqual(llm.model, "deepseek-chat")
        finally:
            for k, v in stash.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_no_key_dry_run(self):
        from treefarm.config import LLMClient
        import os
        # 清掉所有已知 key 环境变量
        keys = ["OPENAI_API_KEY", "DEEPSEEK_API_KEY", "DASHSCOPE_API_KEY",
                "MOONSHOT_API_KEY", "ARK_API_KEY", "TREEFARM_API_KEY",
                "ANTHROPIC_API_KEY", "GEMINI_API_KEY"]
        stash = {k: os.environ.get(k) for k in keys}
        try:
            for k in keys:
                os.environ.pop(k, None)
            llm = LLMClient("/nonexistent")
            llm2 = LLMClient("/nonexistent")
            self.assertFalse(llm.available())
        finally:
            for k, v in stash.items():
                if v is not None:
                    os.environ[k] = v


if __name__ == "__main__":
    unittest.main()