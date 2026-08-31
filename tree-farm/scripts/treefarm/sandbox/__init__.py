# -*- coding: utf-8 -*-
"""treefarm.sandbox 包 —— 树场小沙箱（v3.9 迭代优化版）。
轻量级动态代码分析沙箱，让 AI 在安全环境中执行代码做动态分析。
模块划分：
  types              数据类型
  resource           资源限制
  restricted         Python 受限执行环境（变量追踪/污点跟踪）
  subprocess_sandbox 多语言 subprocess 沙箱（10种语言）
  tracer             调用追踪器
  coverage           轻量覆盖率分析器
  pattern_matcher    AST 模式匹配器（对标 Semgrep）
  code_quality       代码质量分析器（对标 SonarQube）
  profiler           轻量级性能剖析器（对标 cProfile）
  runner             统一调度器（树场集成/动态验证）
零依赖，纯标准库实现，手机 Termux 可运行。
"""
from .types import (
    SandboxResult, SandboxConfig, CallTrace, CoverageData, SecurityLevel,
)
from .resource import (
    ResourceLimiter, TimeoutKiller, MemoryMonitor, OutputLimiter, TempDir,
    detect_platform, is_termux, get_available_memory_mb, recommend_mobile_config, optimize_for_mobile,
)
from .restricted import RestrictedExecutor, VariableTracker
from .subprocess_sandbox import (
    SubprocessSandbox, detect_available_languages, is_language_available,
)
from .tracer import CallTracer, inject_tracer, parse_trace_output
from .coverage import CoverageCollector, inject_coverage, parse_coverage_output
from .pattern_matcher import CodePatternMatcher, PatternMatch, scan_security, SECURITY_PATTERNS
from .code_quality import CodeQualityAnalyzer, CodeIssue, analyze_code_quality, extract_dependencies, detect_duplicate_code
from .profiler import Profiler, FunctionProfile, profile_code
from .project_analyzer import ProjectAnalyzer, analyze_project
from .runner import SandboxRunner

__all__ = [
    "SandboxRunner", "SandboxResult", "SandboxConfig", "CallTrace",
    "CoverageData", "SecurityLevel", "ResourceLimiter", "TimeoutKiller",
    "MemoryMonitor", "OutputLimiter", "TempDir", "detect_platform",
    "is_termux", "RestrictedExecutor", "VariableTracker", "SubprocessSandbox",
    "detect_available_languages", "is_language_available", "CallTracer",
    "inject_tracer", "parse_trace_output", "CoverageCollector",
    "inject_coverage", "parse_coverage_output", "CodePatternMatcher",
    "PatternMatch", "scan_security", "SECURITY_PATTERNS",
    "CodeQualityAnalyzer", "CodeIssue", "analyze_code_quality",
    "Profiler", "FunctionProfile", "profile_code",
]
__version__ = "3.9.1"
