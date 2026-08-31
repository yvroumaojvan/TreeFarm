# -*- coding: utf-8 -*-
"""树场小沙箱 —— 项目级分析器（v3.9.1 第3轮新增）。
支持扫描整个项目目录，多文件分析，生成汇总报告。
零依赖，纯标准库实现。
"""
import os
import json
import time
from typing import Any, Dict, List, Optional

from .pattern_matcher import scan_security, CodePatternMatcher
from .code_quality import analyze_code_quality, extract_dependencies, detect_duplicate_code


class ProjectAnalyzer:
    """项目级分析器，支持多文件扫描和汇总报告。"""

    def __init__(self, project_path: str, file_patterns: Optional[List[str]] = None):
        """初始化项目分析器。

        Args:
            project_path: 项目根目录路径
            file_patterns: 要分析的文件扩展名列表，默认 ['.py']
        """
        self.project_path = project_path
        self.file_patterns = file_patterns or ['.py']
        self.files = []
        self.results = []
        self.errors = []
        self.start_time = 0
        self.end_time = 0

    def discover_files(self) -> List[str]:
        """发现项目中的所有待分析文件。"""
        self.files = []
        for root, dirs, files in os.walk(self.project_path):
            # 跳过常见的忽略目录
            dirs[:] = [d for d in dirs if d not in (
                '__pycache__', '.git', '.venv', 'venv', 'node_modules',
                'dist', 'build', '.tox', '.mypy_cache', '.pytest_cache',
            )]
            for f in files:
                if any(f.endswith(p) for p in self.file_patterns):
                    self.files.append(os.path.join(root, f))
        return self.files

    def analyze_file(self, filepath: str) -> Dict[str, Any]:
        """分析单个文件。"""
        result = {
            "filepath": filepath,
            "relative_path": os.path.relpath(filepath, self.project_path),
            "size_bytes": 0,
            "lines": 0,
            "security_issues": [],
            "quality_issues": [],
            "dependencies": {"standard": [], "third_party": [], "local": []},
            "duplicate_blocks": 0,
            "analysis_time_ms": 0,
            "error": None,
        }

        start = time.time()
        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                code = f.read()

            result["size_bytes"] = len(code.encode("utf-8"))
            result["lines"] = code.count("\n") + 1

            # 安全扫描
            result["security_issues"] = scan_security(code)

            # 代码质量分析
            quality = analyze_code_quality(code, filepath)
            result["quality_issues"] = quality.get("issues", [])
            result["complexity"] = quality.get("complexity", {})
            result["metrics"] = quality.get("metrics", {})
            result["technical_debt"] = quality.get("technical_debt", {})

            # 依赖分析
            result["dependencies"] = extract_dependencies(code)

            # 重复代码检测
            dups = detect_duplicate_code(code)
            result["duplicate_blocks"] = len(dups)

        except Exception as e:
            result["error"] = str(e)
            self.errors.append({"file": filepath, "error": str(e)})

        result["analysis_time_ms"] = (time.time() - start) * 1000
        return result

    def analyze(self, max_files: Optional[int] = None) -> Dict[str, Any]:
        """分析整个项目。

        Args:
            max_files: 最大分析文件数（用于性能测试），None表示不限制

        Returns:
            项目分析汇总报告
        """
        self.start_time = time.time()
        self.discover_files()

        files_to_analyze = self.files[:max_files] if max_files else self.files

        for filepath in files_to_analyze:
            result = self.analyze_file(filepath)
            self.results.append(result)

        self.end_time = time.time()
        return self.generate_report()

    def generate_report(self) -> Dict[str, Any]:
        """生成汇总报告。"""
        total_security = sum(len(r["security_issues"]) for r in self.results)
        total_quality = sum(len(r["quality_issues"]) for r in self.results)
        total_lines = sum(r["lines"] for r in self.results)
        total_size = sum(r["size_bytes"] for r in self.results)
        total_time = sum(r["analysis_time_ms"] for r in self.results)

        # 按严重程度统计安全问题
        security_by_severity = {"critical": 0, "high": 0, "medium": 0, "low": 0}
        for r in self.results:
            for issue in r["security_issues"]:
                sev = issue.get("severity", "low")
                security_by_severity[sev] = security_by_severity.get(sev, 0) + 1

        # 按类型统计质量问题
        quality_by_type = {}
        for r in self.results:
            for issue in r["quality_issues"]:
                t = issue.get("type", "unknown")
                quality_by_type[t] = quality_by_type.get(t, 0) + 1

        # 合并所有依赖
        all_deps = {"standard": set(), "third_party": set(), "local": set()}
        for r in self.results:
            for k in all_deps:
                all_deps[k].update(r["dependencies"].get(k, []))

        # 安全问题最多的文件
        files_with_security = sorted(
            self.results,
            key=lambda x: len(x["security_issues"]),
            reverse=True
        )[:10]

        # 质量问题最多的文件
        files_with_quality = sorted(
            self.results,
            key=lambda x: len(x["quality_issues"]),
            reverse=True
        )[:10]

        return {
            "project_path": self.project_path,
            "summary": {
                "total_files": len(self.results),
                "files_with_errors": len(self.errors),
                "total_lines": total_lines,
                "total_size_mb": round(total_size / 1024 / 1024, 2),
                "total_security_issues": total_security,
                "total_quality_issues": total_quality,
                "total_duplicate_blocks": sum(r["duplicate_blocks"] for r in self.results),
                "total_analysis_time_ms": round(total_time, 2),
                "wall_clock_time_ms": round((self.end_time - self.start_time) * 1000, 2),
                "avg_time_per_file_ms": round(total_time / len(self.results), 2) if self.results else 0,
            },
            "security_by_severity": security_by_severity,
            "quality_by_type": quality_by_type,
            "dependencies": {k: sorted(list(v)) for k, v in all_deps.items()},
            "top_security_files": [
                {"file": r["relative_path"], "count": len(r["security_issues"])}
                for r in files_with_security if r["security_issues"]
            ],
            "top_quality_files": [
                {"file": r["relative_path"], "count": len(r["quality_issues"])}
                for r in files_with_quality if r["quality_issues"]
            ],
            "errors": self.errors,
            "files": self.results,
        }

    def export_json(self, output_path: str) -> None:
        """导出报告为JSON文件。"""
        report = self.generate_report()
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)

    def print_summary(self) -> None:
        """打印汇总报告到控制台。"""
        report = self.generate_report()
        s = report["summary"]
        print(f"\n{'='*60}")
        print(f"项目分析报告: {self.project_path}")
        print(f"{'='*60}")
        print(f"文件数: {s['total_files']} (错误: {s['files_with_errors']})")
        print(f"代码行数: {s['total_lines']:,}")
        print(f"代码大小: {s['total_size_mb']} MB")
        print(f"分析耗时: {s['wall_clock_time_ms']/1000:.2f}s (平均 {s['avg_time_per_file_ms']:.0f}ms/文件)")
        print(f"\n安全问题: {s['total_security_issues']} 个")
        for sev, count in report["security_by_severity"].items():
            if count > 0:
                print(f"  {sev.upper()}: {count}")
        print(f"\n质量问题: {s['total_quality_issues']} 个")
        for t, count in sorted(report["quality_by_type"].items(), key=lambda x: -x[1])[:5]:
            print(f"  {t}: {count}")
        print(f"\n重复代码块: {s['total_duplicate_blocks']} 个")
        print(f"\n依赖: {len(report['dependencies']['standard'])} 标准库, "
              f"{len(report['dependencies']['third_party'])} 第三方")
        if report["top_security_files"]:
            print(f"\n安全问题最多的文件:")
            for f in report["top_security_files"][:5]:
                print(f"  {f['count']:3d}  {f['file']}")
        print(f"{'='*60}\n")


def analyze_project(project_path: str, max_files: Optional[int] = None) -> Dict[str, Any]:
    """便捷函数：分析整个项目。"""
    analyzer = ProjectAnalyzer(project_path)
    return analyzer.analyze(max_files=max_files)
