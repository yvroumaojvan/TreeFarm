# -*- coding: utf-8 -*-
"""树场小沙箱 —— 数据类型定义（v3.8 新增）。
包含：SandboxResult（执行结果）/ CallTrace（调用轨迹）/ CoverageData（覆盖率数据）
/ SandboxConfig（沙箱配置）/ SecurityLevel（安全等级枚举）。
所有类型均为纯数据类，零依赖，可 JSON 序列化。
"""
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple
from enum import Enum


class SecurityLevel(str, Enum):
    """安全等级：auto 自动选择 / restricted Python 受限执行 / subprocess 资源限制 / container 容器隔离。"""
    AUTO = "auto"
    RESTRICTED = "restricted"
    SUBPROCESS = "subprocess"
    CONTAINER = "container"


@dataclass
class CallTrace:
    """单次函数调用轨迹（v3.8 沙箱新增）。"""
    func: str               # 函数名
    args: List[Any] = field(default_factory=list)   # 位置参数（repr 后字符串）
    kwargs: Dict[str, Any] = field(default_factory=dict)  # 关键字参数
    return_value: Any = None   # 返回值（repr 后字符串）
    lineno: int = 0            # 行号
    filename: str = ""         # 文件名
    duration_ms: float = 0.0   # 执行耗时（毫秒）

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CoverageData:
    """代码覆盖率数据（v3.8 沙箱新增）。"""
    files: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    # files[filepath] = {"lines_covered": [...], "lines_total": [...], "coverage": 0.85}

    def add_file(self, filepath: str, lines_covered: List[int], lines_total: List[int]) -> None:
        """添加一个文件的覆盖率数据。"""
        covered = set(lines_covered)
        total = set(lines_total)
        ratio = len(covered & total) / len(total) if total else 0.0
        self.files[filepath] = {
            "lines_covered": sorted(covered),
            "lines_total": sorted(total),
            "coverage": round(ratio, 4),
        }

    @property
    def overall_coverage(self) -> float:
        """整体覆盖率（所有文件的加权平均）。"""
        total_lines = 0
        covered_lines = 0
        for f in self.files.values():
            total_lines += len(f["lines_total"])
            covered_lines += len(set(f["lines_covered"]) & set(f["lines_total"]))
        return round(covered_lines / total_lines, 4) if total_lines else 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "files": self.files,
            "overall_coverage": self.overall_coverage,
        }


@dataclass
class SandboxResult:
    """沙箱执行结果（v3.8 沙箱核心返回类型）。"""
    success: bool = False              # 是否成功执行
    stdout: str = ""                   # 标准输出
    stderr: str = ""                   # 标准错误
    return_code: int = -1              # 返回码
    execution_time_ms: float = 0.0     # 执行耗时（毫秒）
    memory_peak_kb: int = 0            # 峰值内存（KB）
    error: str = ""                    # 错误信息（如有）
    security_level: str = "auto"       # 实际使用的安全等级
    call_trace: List[Dict[str, Any]] = field(default_factory=list)  # 调用轨迹
    coverage: Optional[Dict[str, Any]] = None   # 覆盖率数据
    return_value: Any = None           # Python 代码的返回值（如有）
    variables: Optional[Dict[str, Any]] = None   # 变量追踪与数据流分析（v3.9）
    error_category: str = ""              # 错误分类（v3.9）：timeout/memory/syntax/runtime/security/resource/unknown
    recovery_suggestion: str = ""         # 恢复建议（v3.9）

    def to_dict(self) -> Dict[str, Any]:
        """转为可 JSON 序列化的字典。"""
        d = asdict(self)
        # return_value 可能不可序列化，转字符串
        if d.get("return_value") is not None:
            try:
                json.dumps(d["return_value"])
            except (TypeError, ValueError):
                d["return_value"] = repr(d["return_value"])
        return d

    def classify_error(self) -> None:
        """根据错误信息自动分类并生成恢复建议（v3.9 新增）。"""
        if self.success:
            self.error_category = ""
            self.recovery_suggestion = ""
            return

        err = (self.error or self.stderr or "").lower()

        # 超时
        if any(kw in err for kw in ("timeout", "timed out", "时间超限", "超过最大执行时间")):
            self.error_category = "timeout"
            self.recovery_suggestion = "执行超时。建议：1) 优化代码减少循环；2) 增大 max_cpu_seconds；3) 检查是否有无限循环。"
        # 内存
        elif any(kw in err for kw in ("memory", "memoryerror", "out of memory", "内存", "killed", "oom")):
            self.error_category = "memory"
            self.recovery_suggestion = "内存不足。建议：1) 优化数据结构减少内存占用；2) 增大 max_memory_mb；3) 分块处理大数据。"
        # 语法错误
        elif any(kw in err for kw in ("syntaxerror", "语法错误", "invalid syntax", "unexpected")):
            self.error_category = "syntax"
            self.recovery_suggestion = "语法错误。建议：检查代码语法，特别是缩进、括号匹配、关键字拼写。"
        # 运行时错误
        elif any(kw in err for kw in ("nameerror", "typeerror", "valueerror", "indexerror", "keyerror", "attributeerror", "zerodivisionerror")):
            self.error_category = "runtime"
            self.recovery_suggestion = "运行时错误。建议：检查变量定义、类型转换、数组越界、空值访问等问题。"
        # 安全限制
        elif any(kw in err for kw in ("security", "forbidden", "not allowed", "禁止", "受限", "blocked", "denied")):
            self.error_category = "security"
            self.recovery_suggestion = "安全限制。代码尝试访问受限资源。建议：使用允许的模块和操作，或调整 allowed_modules 配置。"
        # 资源限制
        elif any(kw in err for kw in ("recursion", "recursionerror", "递归", "output", "输出超限")):
            self.error_category = "resource"
            self.recovery_suggestion = "资源限制。建议：1) 减少递归深度；2) 减少输出量；3) 调整 max_recursion_depth/max_output_kb。"
        else:
            self.error_category = "unknown"
            self.recovery_suggestion = "未知错误。请检查 stderr 输出获取详细信息。"

    def format_summary(self) -> str:
        """格式化人类可读的执行摘要。"""
        status = "✅ 成功" if self.success else "❌ 失败"
        lines = [
            f"🔬 沙箱执行结果：{status}",
            f"   返回码: {self.return_code}",
            f"   耗时: {self.execution_time_ms:.1f}ms",
            f"   峰值内存: {self.memory_peak_kb}KB",
            f"   安全等级: {self.security_level}",
        ]
        if self.stdout:
            lines.append(f"   stdout: {self.stdout[:200]}{'...' if len(self.stdout) > 200 else ''}")
        if self.stderr:
            lines.append(f"   stderr: {self.stderr[:200]}{'...' if len(self.stderr) > 200 else ''}")
        if self.error:
            lines.append(f"   错误: {self.error[:200]}")
        if self.call_trace:
            lines.append(f"   调用轨迹: {len(self.call_trace)} 次函数调用")
        if self.coverage:
            lines.append(f"   覆盖率: {self.coverage.get('overall_coverage', 0):.1%}")
        return "\n".join(lines)


@dataclass
class SandboxConfig:
    """沙箱配置（v3.8 新增，可从 .treefarm.toml 的 [sandbox] 段读取）。"""
    enabled: bool = False                    # 沙箱总开关（默认关闭，用户说「开启沙箱」才开启）
    max_memory_mb: int = 256                 # 最大内存（MB）
    max_cpu_seconds: int = 10                # 最大 CPU 时间（秒）
    max_output_kb: int = 64                   # 最大输出（KB）
    max_recursion_depth: int = 100            # 最大递归深度
    network: bool = False                     # 是否允许网络访问（默认禁止）
    filesystem: str = "ro"                    # 文件系统权限：ro 只读 / rw 读写 / tmpfs 临时
    security_level: str = "auto"              # 安全等级：auto/restricted/subprocess/container
    allowed_modules: List[str] = field(default_factory=lambda: [
        "math", "statistics", "itertools", "collections", "re", "json",
        "string", "random", "datetime", "time", "copy", "functools",
        "operator", "typing", "dataclasses", "enum",
    ])  # RestrictedPython 允许导入的模块白名单
    temp_dir: str = ""                        # 临时目录（空则用系统默认）
    auto_cleanup: bool = True                 # 执行后自动清理临时文件

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SandboxConfig":
        """从字典创建配置（忽略未知字段）。"""
        valid_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in d.items() if k in valid_fields}
        return cls(**filtered)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def validate(self) -> Tuple[bool, List[str]]:
        """验证配置是否合法（v3.9 新增）。"""
        errors = []
        if self.max_memory_mb < 16:
            errors.append(f"max_memory_mb 过小: {self.max_memory_mb}MB")
        elif self.max_memory_mb > 4096:
            errors.append(f"max_memory_mb 过大: {self.max_memory_mb}MB")
        if self.max_cpu_seconds < 1:
            errors.append(f"max_cpu_seconds 过小: {self.max_cpu_seconds}s")
        elif self.max_cpu_seconds > 300:
            errors.append(f"max_cpu_seconds 过大: {self.max_cpu_seconds}s")
        if self.max_output_kb < 1:
            errors.append(f"max_output_kb 过小: {self.max_output_kb}KB")
        elif self.max_output_kb > 102400:
            errors.append(f"max_output_kb 过大: {self.max_output_kb}KB")
        if self.max_recursion_depth < 10:
            errors.append(f"max_recursion_depth 过小: {self.max_recursion_depth}")
        elif self.max_recursion_depth > 10000:
            errors.append(f"max_recursion_depth 过大: {self.max_recursion_depth}")
        valid_levels = {"auto", "restricted", "subprocess", "container"}
        if self.security_level not in valid_levels:
            errors.append(f"security_level 无效: {self.security_level}")
        valid_fs = {"ro", "rw", "tmpfs"}
        if self.filesystem not in valid_fs:
            errors.append(f"filesystem 无效: {self.filesystem}")
        if not self.allowed_modules:
            errors.append("allowed_modules 不能为空")
        return len(errors) == 0, errors

    def get_warnings(self) -> List[str]:
        """获取配置警告（v3.9 新增）。"""
        warnings = []
        if self.max_memory_mb > 512:
            warnings.append(f"max_memory_mb={self.max_memory_mb}MB 手机端可能OOM")
        if self.security_level == "restricted" and self.max_cpu_seconds > 30:
            warnings.append("restricted模式长时间执行可能阻塞主进程")
        if self.network:
            warnings.append("network=true 存在安全风险")
        if self.filesystem == "rw":
            warnings.append("filesystem=rw 存在安全风险")
        return warnings
