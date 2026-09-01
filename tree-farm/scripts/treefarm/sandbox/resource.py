# -*- coding: utf-8 -*-
"""树场小沙箱 —— 资源限制工具（v3.8 新增）。
包含：ResourceLimiter（ulimit 资源限制）/ TimeoutKiller（超时杀死）
/ MemoryMonitor（内存监控）/ OutputLimiter（输出限制）/ TempDir（临时目录管理）。
全部纯标准库实现，零依赖，兼容 Linux/macOS/Termux。
Windows 下 ulimit 不可用，自动降级为仅超时+输出限制。
"""
import os
import sys
import time
import shutil
import tempfile
from typing import Any, Dict, Optional
import threading
import signal
import resource
from typing import Optional, Tuple


class ResourceLimiter:
    """资源限制器（v3.8 沙箱新增）。
    在子进程 preexec_fn 中调用 apply()，设置 ulimit 限制。
    兼容 POSIX 系统（Linux/macOS/Termux），Windows 下静默跳过。
    """

    def __init__(self, max_memory_mb: int = 256, max_cpu_seconds: int = 10,
                 max_files: int = 64, max_file_size_mb: int = 16):
        self.max_memory_mb = max_memory_mb
        self.max_cpu_seconds = max_cpu_seconds
        self.max_files = max_files
        self.max_file_size_mb = max_file_size_mb
        self._available = hasattr(resource, "RLIMIT_AS")

    @property
    def available(self) -> bool:
        """当前平台是否支持 ulimit 资源限制。"""
        return self._available

    def apply(self) -> None:
        """应用资源限制（在子进程 preexec_fn 中调用）。
        Windows 或不支持的平台静默跳过。
        """
        if not self._available:
            return
        try:
            # 虚拟内存限制（RLIMIT_AS = 地址空间，最可靠的内存限制）
            mem_bytes = self.max_memory_mb * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (mem_bytes, mem_bytes))
            # CPU 时间限制（秒）
            resource.setrlimit(resource.RLIMIT_CPU, (self.max_cpu_seconds, self.max_cpu_seconds))
            # 文件描述符数限制
            resource.setrlimit(resource.RLIMIT_NOFILE, (self.max_files, self.max_files))
            # 文件大小限制（防止写大文件）
            fsize_bytes = self.max_file_size_mb * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_FSIZE, (fsize_bytes, fsize_bytes))
            # 栈大小限制（防止栈溢出炸弹）
            stack_bytes = 8 * 1024 * 1024  # 8MB
            resource.setrlimit(resource.RLIMIT_STACK, (stack_bytes, stack_bytes))
        except (ValueError, OSError):
            # 某些限制在特定平台不可用，跳过即可
            pass


class TimeoutKiller:
    """超时杀死器（v3.8 沙箱新增）。
    用独立线程监控子进程，超时后发送 SIGKILL 强制终止。
    兼容所有平台（不依赖 signal.alarm，因为子进程中可能被覆盖）。
    """

    def __init__(self, timeout_seconds: float = 10.0):
        self.timeout = timeout_seconds
        self._timer: Optional[threading.Timer] = None
        self._killed = False

    def start(self, pid: int) -> None:
        """启动超时监控（在父进程中调用，传入子进程 pid）。"""
        self._killed = False

        def _kill():
            self._killed = True
            try:
                os.kill(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass  # 进程已退出

        self._timer = threading.Timer(self.timeout, _kill)
        self._timer.daemon = True
        self._timer.start()

    def stop(self) -> bool:
        """停止超时监控，返回是否因超时被杀死。"""
        if self._timer:
            self._timer.cancel()
            self._timer = None
        return self._killed


class MemoryMonitor:
    """内存监控器（v3.8 沙箱新增）。
    用独立线程定期读取子进程的 /proc/<pid>/status，记录峰值内存。
    仅 Linux 可用（含 Termux），macOS/Windows 下返回 0。
    """

    def __init__(self, interval_ms: int = 50):
        self.interval = interval_ms / 1000.0
        self._peak_kb = 0
        self._thread: Optional[threading.Thread] = None
        self._stop = False
        self._pid = 0

    def _read_rss(self, pid: int) -> int:
        """读取进程的 RSS 内存（KB），失败返回 0。"""
        try:
            with open(f"/proc/{pid}/status", "r") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        return int(line.split()[1])
        except (FileNotFoundError, PermissionError, ProcessLookupError, IndexError, OSError):
            # v4.5 修复：进程退出瞬间 /proc/<pid> 消失会抛 ProcessLookupError/OSError，
            # 之前只捕获 FileNotFoundError，监控线程会打印异常堆栈
            pass
        return 0

    def _monitor_loop(self) -> None:
        while not self._stop:
            rss = self._read_rss(self._pid)
            if rss > self._peak_kb:
                self._peak_kb = rss
            time.sleep(self.interval)

    def start(self, pid: int) -> None:
        """启动内存监控。"""
        self._peak_kb = 0
        self._stop = False
        self._pid = pid
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()

    def stop(self) -> int:
        """停止监控，返回峰值内存（KB）。"""
        self._stop = True
        if self._thread:
            self._thread.join(timeout=1.0)
        return self._peak_kb

    @property
    def peak_kb(self) -> int:
        return self._peak_kb


class OutputLimiter:
    """输出限制器（v3.8 沙箱新增）。
    限制子进程 stdout/stderr 的最大输出量，防止日志炸弹。
    用管道读取，超过限制后截断并标记。
    """

    def __init__(self, max_kb: int = 64):
        self.max_bytes = max_kb * 1024
        self.truncated = False

    def read_limited(self, pipe) -> str:
        """从管道读取，限制最大字节数。"""
        data = b""
        while True:
            chunk = pipe.read(4096)
            if not chunk:
                break
            data += chunk
            if len(data) >= self.max_bytes:
                self.truncated = True
                data = data[:self.max_bytes]
                break
        try:
            return data.decode("utf-8", errors="replace")
        except Exception:
            return repr(data)


class TempDir:
    """临时目录管理器（v3.8 沙箱新增）。
    创建隔离的临时工作目录，执行后自动清理。
    支持 with 语句：with TempDir() as d: ...
    """

    def __init__(self, prefix: str = "treefarm_sandbox_", auto_cleanup: bool = True):
        self.prefix = prefix
        self.auto_cleanup = auto_cleanup
        self.path = ""

    def __enter__(self) -> str:
        self.path = tempfile.mkdtemp(prefix=self.prefix)
        return self.path

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self.auto_cleanup and self.path and os.path.isdir(self.path):
            try:
                shutil.rmtree(self.path, ignore_errors=True)
            except Exception:
                pass

    def cleanup(self) -> None:
        """手动清理临时目录。"""
        if self.path and os.path.isdir(self.path):
            shutil.rmtree(self.path, ignore_errors=True)


def detect_platform() -> str:
    """检测运行平台，返回 linux/macos/windows/android(termux)/unknown。"""
    if sys.platform.startswith("linux"):
        # Termux 检测：存在 /data/data/com.termux 或 PREFIX 含 termux
        if "/com.termux/" in os.environ.get("PREFIX", "") or os.path.isdir("/data/data/com.termux"):
            return "android"
        return "linux"
    elif sys.platform == "darwin":
        return "macos"
    elif sys.platform.startswith("win"):
        return "windows"
    return "unknown"


def is_termux() -> bool:
    """是否运行在 Termux（安卓）环境。"""
    return detect_platform() == "android"


def get_available_memory_mb() -> int:
    """获取系统可用内存（MB），v3.9 新增。"""
    try:
        with open("/proc/meminfo", "r") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024
    except (IOError, ValueError):
        pass
    return 1024


def recommend_mobile_config() -> Dict[str, Any]:
    """根据手机硬件推荐沙箱配置（v3.9 第12轮手机端优化）。"""
    available_mb = get_available_memory_mb()
    is_mobile = is_termux()
    if is_mobile:
        if available_mb < 512:
            max_memory, max_cpu, max_output = 64, 5, 16
        elif available_mb < 1024:
            max_memory, max_cpu, max_output = 128, 8, 32
        else:
            max_memory, max_cpu, max_output = 256, 10, 64
        security_level = "subprocess"
    else:
        max_memory, max_cpu, max_output = 256, 10, 64
        security_level = "auto"
    return {
        "max_memory_mb": max_memory, "max_cpu_seconds": max_cpu,
        "max_output_kb": max_output, "security_level": security_level,
        "is_mobile": is_mobile, "available_memory_mb": available_mb,
    }


def optimize_for_mobile(config: Any) -> Any:
    """将配置优化为手机端友好配置（v3.9 新增）。"""
    if not is_termux():
        return config
    rec = recommend_mobile_config()
    config.max_memory_mb = min(config.max_memory_mb, rec["max_memory_mb"])
    config.max_cpu_seconds = min(config.max_cpu_seconds, rec["max_cpu_seconds"])
    config.max_output_kb = min(config.max_output_kb, rec["max_output_kb"])
    if config.security_level == "auto":
        config.security_level = rec["security_level"]
    return config
