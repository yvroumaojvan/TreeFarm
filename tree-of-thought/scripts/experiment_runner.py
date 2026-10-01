#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""experiment_runner.py —— 实验桥（科研分支 B3：灵感树候选 → 真实运行）
用 TreeFarm 沙箱执行一段 Python 代码并测量指标，供 innovation_experiment.js 调用。

用法：
    python3 experiment_runner.py --code '<代码>' --measure time --repeat 3
    python3 experiment_runner.py --code '<代码>' --measure score
输出：JSON { "ok": bool, "times_ms": [...], "values": [...], "error": "..." }
  measure=time  ：返回每次执行的耗时（毫秒）
  measure=score ：返回 stdout 里最后一个数字（作为可比较指标）
"""
import argparse
import json
import sys
from pathlib import Path

# tree-farm/scripts 加入 sys.path（允许从 tree-of-thought/scripts 直接调用）
TF_SCRIPTS = str(Path(__file__).resolve().parent.parent.parent / "tree-farm" / "scripts")
if TF_SCRIPTS not in sys.path:
    sys.path.insert(0, TF_SCRIPTS)

from treefarm.sandbox import SandboxRunner  # noqa: E402


def run_once(code: str, measure: str) -> dict:
    runner = SandboxRunner()
    runner.enable()  # 实验桥专用：默认开启沙箱（隔离执行是实验前提）
    result = runner.run_code(code, language="python")
    item = {
        "ok": result.success,
        "ms": round(result.execution_time_ms, 3),
        "error": result.error or result.stderr.strip(),
    }
    if measure == "score":
        # 从 stdout 提取最后一个数字（约定：被测代码最后 print 一个可比较数值）
        last = None
        for tok in result.stdout.strip().split():
            try:
                last = float(tok)
            except ValueError:
                pass
        item["value"] = last
    return item


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", required=True)
    ap.add_argument("--measure", choices=["time", "score"], default="time")
    ap.add_argument("--repeat", type=int, default=1)
    args = ap.parse_args()

    times, values, errors = [], [], []
    for _ in range(max(1, args.repeat)):
        r = run_once(args.code, args.measure)
        if r["ok"]:
            times.append(r["ms"])
            if "value" in r:
                values.append(r["value"])
        else:
            errors.append(r["error"])
    out = {"ok": not errors, "times_ms": times, "values": values, "error": errors[0] if errors else ""}
    print(json.dumps(out, ensure_ascii=False))
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
