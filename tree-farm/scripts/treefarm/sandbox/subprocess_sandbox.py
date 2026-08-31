# -*- coding: utf-8 -*-
"""树场小沙箱 —— 多语言 subprocess 沙箱（v3.8 新增）。
L1 安全等级：用子进程 + ulimit 资源限制 + 超时杀死 + 内存监控执行代码。
支持语言：Python / JavaScript (Node.js) / Shell (bash/sh) / Ruby / Perl / Lua。
自动检测系统中可用的解释器，不可用的语言返回明确错误。
全部纯标准库实现，零依赖，兼容 Linux/macOS/Termux。
"""
import os
import sys
import subprocess
import shutil
import tempfile
import time
from typing import Any, Dict, List, Optional, Tuple

from .types import SandboxResult, SandboxConfig
from .resource import (
    ResourceLimiter, TimeoutKiller, MemoryMonitor,
    OutputLimiter, TempDir, detect_platform, is_termux,
)


# ========== Python 静态安全检查（v4.5 修复：subprocess 路径此前完全无代码级防护） ==========
# restricted 路径是「白名单严模式」；subprocess 路径（跑测试/覆盖率/多语言）此前只做资源限制，
# 导致 os.system / open('/etc/shadow') 等危险操作可直接执行。这里补一层调用级黑名单检查。
_NETWORK_MODULES = {
    "socket", "urllib", "urllib3", "requests", "httpx", "aiohttp", "http",
    "ftplib", "telnetlib", "smtplib", "poplib", "imaplib", "ssl", "asyncio",
}
_UNSAFE_IMPORT_MODULES = {
    "ctypes", "pickle", "cPickle", "marshal", "shelve", "dbm", "anydbm",
    "code", "codeop", "pty", "resource", "fcntl", "mmap",
}
_DANGEROUS_OS_CALLS = {
    "system", "popen", "spawnl", "spawnle", "spawnlp", "spawnlpe",
    "spawnv", "spawnve", "spawnvp", "spawnvpe", "execl", "execle",
    "execlp", "execlpe", "execv", "execve", "execvp", "execvpe",
    "fork", "forkpty", "kill", "killpg", "remove", "unlink", "rmdir",
    "removedirs", "rename", "renames", "replace", "chmod", "chown",
    "chroot", "mknod", "mkfifo", "mount", "umount", "symlink", "link",
}
_UNCALLABLE_BUILTIN_CALLS = {"eval", "exec", "compile", "__import__", "input", "breakpoint"}
_DANGEROUS_DUNDER_ATTRS = {
    "__class__", "__bases__", "__subclasses__", "__mro__", "__globals__",
    "__builtins__", "__dict__", "__code__", "__func__", "__self__",
    "__closure__", "__init__", "__getattribute__", "__setattr__",
}


def _static_check_python(code: str, allow_file_io: bool = False) -> Tuple[bool, str]:
    """调用级静态安全检查（subprocess 路径用）。
    返回 (是否安全, 拒绝原因)。通过 = 放行执行；不通过 = 返回明确错误。
    allow_file_io=True 时放行 open（对应配置 filesystem=rw/tmpfs，用户主动开放）。
    """
    import ast
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return False, f"语法错误: {e}"

    def _call_name(node):
        """提取调用名（'os.system' / 'subprocess.run' / 'eval' / None）。"""
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            parts = []
            cur = node
            while isinstance(cur, ast.Attribute):
                parts.append(cur.attr)
                cur = cur.value
            if isinstance(cur, ast.Name):
                parts.append(cur.id)
            return ".".join(reversed(parts))
        return None

    for node in ast.walk(tree):
        # 1) import 网络/反序列化/系统级模块 → 拒绝
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in _NETWORK_MODULES:
                    return False, f"禁止导入网络模块: {alias.name}（沙箱禁止网络访问）"
                if root in _UNSAFE_IMPORT_MODULES:
                    return False, f"禁止导入危险模块: {alias.name}"
        if isinstance(node, ast.ImportFrom):
            if node.module:
                root = node.module.split(".")[0]
                if root in _NETWORK_MODULES:
                    return False, f"禁止导入网络模块: {node.module}（沙箱禁止网络访问）"
                if root in _UNSAFE_IMPORT_MODULES:
                    return False, f"禁止导入危险模块: {node.module}"

        # 2) 危险调用 → 拒绝
        if isinstance(node, ast.Call):
            name = _call_name(node.func)
            if not name:
                continue
            # os.system / os.popen 等系统操作
            if name.startswith("os.") and name.split(".")[1] in _DANGEROUS_OS_CALLS:
                return False, f"禁止调用危险系统操作: {name}"
            # subprocess 全系列
            if name.startswith("subprocess"):
                return False, f"禁止调用子进程执行: {name}"
            # 动态执行 / 内置危险函数
            if name in _UNCALLABLE_BUILTIN_CALLS:
                return False, f"禁止调用危险函数: {name}"
            # globals/locals/vars 拿 builtins 逃逸
            if name in ("globals", "locals", "vars", "dir", "help"):
                return False, f"禁止调用内省函数: {name}（可用于沙箱逃逸）"
            # socket/urllib 网络
            if name.startswith(("socket.", "urllib.", "requests.", "httpx.", "ssl.")):
                return False, f"禁止网络调用: {name}"
            # getattr 动态取属性（常见逃逸手法）
            if name == "getattr" or name == "setattr":
                return False, f"禁止动态属性访问: {name}"
            # open 文件 IO：默认（filesystem=ro）禁止；rw/tmpfs 才放行
            if name == "open" and not allow_file_io:
                return False, f"禁止文件操作: open（filesystem=ro，如需文件读写请在配置里改为 rw/tmpfs）"

        # 3) 危险属性访问（__class__ 链 / __globals__ 等）
        if isinstance(node, ast.Attribute):
            if node.attr in _DANGEROUS_DUNDER_ATTRS:
                return False, f"禁止访问危险属性: {node.attr}"
            if node.attr.startswith("__") and node.attr.endswith("__"):
                return False, f"禁止访问双下划线属性: {node.attr}"

    # 4) 文本级兜底：lambda/推导式里的 __import__( 逃逸（AST 可能被压缩躲过）
    for pat in ("__import__(", "getattr(__builtins__", "().__class__", ".__subclasses__()"):
        if pat in code:
            return False, f"检测到疑似沙箱逃逸模式: {pat}"
    return True, ""


# 支持的语言及其解释器、文件扩展名、执行命令模板、最小内存需求
_LANGUAGE_CONFIG: Dict[str, Dict[str, Any]] = {
    "python": {
        "interpreters": ["python3", "python"],
        "extension": ".py",
        "cmd_template": "{interp} {file}",
        "description": "Python 3",
        "min_memory_mb": 64,
    },
    "javascript": {
        "interpreters": ["node", "nodejs"],
        "extension": ".js",
        "cmd_template": "{interp} {file}",
        "description": "JavaScript (Node.js)",
        "min_memory_mb": 2048,  # V8 引擎需要大量虚拟内存（Node.js 22+）
    },
    "shell": {
        "interpreters": ["bash", "sh"],
        "extension": ".sh",
        "cmd_template": "{interp} {file}",
        "description": "Shell (bash/sh)",
        "min_memory_mb": 32,
    },
    "ruby": {
        "interpreters": ["ruby"],
        "extension": ".rb",
        "cmd_template": "{interp} {file}",
        "description": "Ruby",
        "min_memory_mb": 128,
    },
    "perl": {
        "interpreters": ["perl"],
        "extension": ".pl",
        "cmd_template": "{interp} {file}",
        "description": "Perl",
        "min_memory_mb": 64,
    },
    "lua": {
        "interpreters": ["lua", "luajit"],
        "extension": ".lua",
        "cmd_template": "{interp} {file}",
        "description": "Lua",
        "min_memory_mb": 32,
    },
    "php": {
        "interpreters": ["php"],
        "extension": ".php",
        "cmd_template": "{interp} {file}",
        "description": "PHP",
        "min_memory_mb": 128,
    },
    "r": {
        "interpreters": ["Rscript", "R"],
        "extension": ".R",
        "cmd_template": "{interp} {file}",
        "description": "R (统计计算)",
        "min_memory_mb": 256,
    },
    "go": {
        "interpreters": ["go"],
        "extension": ".go",
        "cmd_template": "{interp} run {file}",
        "description": "Go (先编译再运行)",
        "min_memory_mb": 512,  # 编译需要较多内存
    },
    "c": {
        "interpreters": ["gcc", "cc"],
        "extension": ".c",
        "cmd_template": "{interp} {file} -o /tmp/tf_sandbox_out && /tmp/tf_sandbox_out",
        "description": "C (gcc编译运行)",
        "min_memory_mb": 256,
    },
}


def detect_available_languages() -> Dict[str, str]:
    """检测系统中可用的编程语言解释器，返回 {语言: 解释器路径}。"""
    available = {}
    for lang, cfg in _LANGUAGE_CONFIG.items():
        for interp in cfg["interpreters"]:
            path = shutil.which(interp)
            if path:
                available[lang] = path
                break
    return available


def is_language_available(language: str) -> bool:
    """检查指定语言是否可用。"""
    return language.lower() in detect_available_languages()


class SubprocessSandbox:
    """多语言 subprocess 沙箱（v3.8 沙箱 L1 安全等级）。
    用子进程执行代码，配合 ulimit 资源限制、超时杀死、内存监控。
    隔离强度高于 RestrictedExecutor（进程级隔离），
    但低于容器隔离（L3）。
    """

    def __init__(self, config: SandboxConfig):
        self.config = config
        self._available = detect_available_languages()
        self._platform = detect_platform()

    @property
    def available_languages(self) -> Dict[str, str]:
        """当前系统可用的语言及解释器路径。"""
        return self._available.copy()

    def _write_code_file(self, code: str, language: str, temp_dir: str) -> str:
        """将代码写入临时文件，返回文件路径。"""
        cfg = _LANGUAGE_CONFIG.get(language.lower())
        if not cfg:
            raise ValueError(f"不支持的语言: {language}")
        ext = cfg["extension"]
        filepath = os.path.join(temp_dir, f"sandbox_code{ext}")
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(code)
        return filepath

    def _build_command(self, language: str, filepath: str) -> List[str]:
        """构建执行命令。"""
        cfg = _LANGUAGE_CONFIG[language.lower()]
        interp = self._available.get(language.lower())
        if not interp:
            raise RuntimeError(f"语言 {language} 的解释器不可用")
        # 用 shlex.split 处理模板（模板很简单，直接格式化即可）
        cmd_str = cfg["cmd_template"].format(interp=interp, file=filepath)
        return cmd_str.split()

    def _make_preexec(self, resource_limiter: ResourceLimiter):
        """创建子进程 preexec 函数（应用资源限制）。"""
        def _preexec():
            # 子进程中设置资源限制
            resource_limiter.apply()
            # 改变工作目录到临时目录
            # os.chdir(temp_dir)  # 在父进程中设置 cwd 参数更安全
        return _preexec

    def execute(self, code: str, language: str = "python",
                stdin: str = "", env: Optional[Dict[str, str]] = None) -> SandboxResult:
        """在子进程沙箱中执行代码。

        Args:
            code: 要执行的代码字符串
            language: 编程语言（python/javascript/shell/ruby/perl/lua）
            stdin: 标准输入内容
            env: 额外的环境变量（会合并到干净环境中）

        Returns:
            SandboxResult 执行结果
        """
        result = SandboxResult(security_level="subprocess")
        language = language.lower()

        # 1. 检查语言是否可用
        if language not in self._available:
            result.success = False
            result.error = (f"语言 {language} 的解释器不可用。"
                             f"当前可用: {', '.join(self._available.keys()) or '无'}")
            result.return_code = -1
            return result

        # 1.5 Python 代码先做静态安全检查（v4.5 修复：此前 subprocess 路径无代码级防护）
        if language == "python":
            allow_file_io = self.config.filesystem in ("rw", "tmpfs")
            ok, reason = _static_check_python(code, allow_file_io=allow_file_io)
            if not ok:
                result.success = False
                result.error = f"沙箱安全拦截: {reason}"
                result.return_code = -1
                result.error_category = "security"
                result.recovery_suggestion = "代码包含被沙箱禁止的危险操作。请移除后重试。"
                return result

        # 2. 创建临时目录
        with TempDir(prefix="treefarm_sandbox_", auto_cleanup=self.config.auto_cleanup) as temp_dir:
            try:
                # 3. 写入代码文件
                filepath = self._write_code_file(code, language, temp_dir)

                # 4. 构建命令
                cmd = self._build_command(language, filepath)

                # 5. 准备资源限制器（根据语言最小内存需求自动调整）
                lang_cfg = _LANGUAGE_CONFIG.get(language, {})
                min_mem = lang_cfg.get("min_memory_mb", 64)
                effective_mem = max(self.config.max_memory_mb, min_mem)
                resource_limiter = ResourceLimiter(
                    max_memory_mb=effective_mem,
                    max_cpu_seconds=self.config.max_cpu_seconds,
                )

                # 6. 准备环境变量（干净环境，只保留必要的）
                clean_env = {
                    "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                    "HOME": temp_dir,  # HOME 指向临时目录，防止写用户目录
                    "LANG": "C.UTF-8",
                    "PYTHONIOENCODING": "utf-8",
                }
                if env:
                    clean_env.update(env)
                # Termux 下需要保留 PREFIX 等环境变量
                if is_termux():
                    for key in ("PREFIX", "LD_LIBRARY_PATH", "TMPDIR"):
                        if key in os.environ:
                            clean_env[key] = os.environ[key]

                # 7. 启动子进程
                t0 = time.time()
                proc = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    cwd=temp_dir,
                    env=clean_env,
                    preexec_fn=self._make_preexec(resource_limiter) if resource_limiter.available else None,
                    text=False,  # 用 bytes 读取，避免编码问题
                )

                # 8. 启动超时监控和内存监控
                timeout_killer = TimeoutKiller(timeout_seconds=self.config.max_cpu_seconds + 2)
                timeout_killer.start(proc.pid)

                memory_monitor = MemoryMonitor(interval_ms=50)
                memory_monitor.start(proc.pid)

                # 9. 读取输出（带限制）
                output_limiter = OutputLimiter(max_kb=self.config.max_output_kb)
                try:
                    stdout_bytes, stderr_bytes = proc.communicate(
                        input=stdin.encode("utf-8") if stdin else None,
                        timeout=self.config.max_cpu_seconds + 5,
                    )
                except subprocess.TimeoutExpired:
                    proc.kill()
                    stdout_bytes, stderr_bytes = proc.communicate()
                    result.error = "执行超时（communicate timeout）"

                # 10. 停止监控
                timed_out = timeout_killer.stop()
                peak_memory = memory_monitor.stop()

                # 11. 收集结果
                result.return_code = proc.returncode if proc.returncode is not None else -1
                result.stdout = stdout_bytes.decode("utf-8", errors="replace")[:self.config.max_output_kb * 1024]
                result.stderr = stderr_bytes.decode("utf-8", errors="replace")[:self.config.max_output_kb * 1024]
                result.execution_time_ms = (time.time() - t0) * 1000
                result.memory_peak_kb = peak_memory

                if timed_out:
                    result.success = False
                    result.error = f"执行超时（超过 {self.config.max_cpu_seconds} 秒）"
                    result.return_code = 124
                elif result.return_code == 0:
                    result.success = True
                else:
                    result.success = False
                    if not result.error:
                        result.error = f"进程退出码 {result.return_code}"

            except Exception as e:
                result.success = False
                result.error = f"{type(e).__name__}: {e}"
                result.return_code = -1
                result.execution_time_ms = (time.time() - t0) * 1000 if 't0' in dir() else 0

        return result

    def execute_python(self, code: str, stdin: str = "") -> SandboxResult:
        """快捷方法：执行 Python 代码。"""
        return self.execute(code, language="python", stdin=stdin)

    def execute_javascript(self, code: str, stdin: str = "") -> SandboxResult:
        """快捷方法：执行 JavaScript 代码。"""
        return self.execute(code, language="javascript", stdin=stdin)

    def execute_shell(self, code: str, stdin: str = "") -> SandboxResult:
        """快捷方法：执行 Shell 代码。"""
        return self.execute(code, language="shell", stdin=stdin)

    def run_tests(self, test_code: str, language: str = "python") -> SandboxResult:
        """运行测试代码（自动添加测试框架的最小支持）。

        Args:
            test_code: 测试代码
            language: 编程语言

        Returns:
            SandboxResult，stdout 包含测试结果
        """
        if language == "python":
            # 自动包装 unittest
            wrapped = (
                "import sys\n"
                "import unittest\n"
                f"{test_code}\n"
                "if __name__ == '__main__':\n"
                "    unittest.main(verbosity=2, exit=False)\n"
            )
            return self.execute(wrapped, language="python")
        return self.execute(test_code, language=language)
