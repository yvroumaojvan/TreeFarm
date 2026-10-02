#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""experiment_runner.py —— 实验桥（科研分支 B3：灵感树候选 → 真实运行）
用 TreeFarm 沙箱执行一段 Python 代码并测量指标/验证正确性，
供 innovation_experiment.js 调用。

用法：
    python3 experiment_runner.py --code '<代码>' --measure time --repeat 3
    python3 experiment_runner.py --code '<代码>' --measure score
    python3 experiment_runner.py --code '<代码>' --verify '{"func":"f","cases":[{"input":[1,2],"expected":3}]}'
输出：JSON { "ok": bool, "times_ms": [...], "values": [...], "verified": {...}, "error": "..." }
  measure=time  ：返回每次执行的耗时（毫秒）
  measure=score ：返回 stdout 里最后一个数字（作为可比较指标）
  --verify      ：正确性验证（GPT 审 #6：指标比较器 → 科学实验验证器）——
                  运行 code 后调用 func(*input) 与 expected 对比，输出正确率
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


def verify_code(code: str, verify_json: str) -> dict:
    """正确性验证（GPT 审 #6：指标比较器 → 科学实验验证器）。
    verify_json: {"func":"函数名","cases":[{"input":[..],"expected":..}, ...]}
    在沙箱里定义函数后逐个用例断言，返回 {passed,total,failures}。
    """
    try:
        spec = json.loads(verify_json)
    except Exception as e:
        return {"ok": False, "error": "verify JSON 解析失败：" + str(e)}
    func = spec.get("func") or "f"
    cases = spec.get("cases") or []
    if not cases:
        return {"ok": True, "passed": 0, "total": 0, "note": "无验证用例"}
    # 组装验证代码：函数定义 + 对每个用例调用并比较，结果 print 为 JSON（沙箱不返回 return_value）
    lines = [code, ""]
    lines.append("def _try(f, args):")
    lines.append("    try: return f(*args)")
    lines.append("    except Exception as e: return ('EXC', str(e))")
    lines.append("import json as _j")
    lines.append("__r = [")
    for c in cases:
        inp = json.dumps(c.get("input") or [], ensure_ascii=False)
        exp = json.dumps(c.get("expected"), ensure_ascii=False)
        lines.append(f"        _try({func}, {inp}) == {exp},")
    lines.append("    ]")
    lines.append("print(_j.dumps(__r))")
    runner = SandboxRunner()
    runner.enable()
    result = runner.run_code("\n".join(lines), language="python")
    if not result.success:
        return {"ok": False, "error": result.error or result.stderr.strip()}
    try:
        rv = json.loads(result.stdout.strip().splitlines()[-1]) if result.stdout.strip() else []
    except Exception:
        rv = []
    if not isinstance(rv, list):
        return {"ok": False, "error": "验证代码未输出用例结果列表（stdout 尾行=" + repr(result.stdout[-80:]) + "）"}
    passed = sum(1 for x in rv if x is True)
    total = len(rv)
    return {"ok": passed == total, "passed": passed, "total": total,
            "failures": [i for i, x in enumerate(rv) if x is not True][:5]}


def run_workload(workload_json: str) -> dict:
    """workload 模式（GPT 审第五/六份：Benchmark 必须真正执行 workload + 真正重复测量）。
    workload_json: {
      "code": "def f(a): ...",     # 被测代码（函数定义）
      "func": "f",                  # 被测函数名
      "inputs": [ {"args": [...]}, ... ],  # 每组输入（真实数据）
      "repeat": 3                   # 每输入重复测量次数（v0.6：输入维度 × 重复维度彻底分开）
    }
    对每组输入：warmup 1 次（不计入，稳定解释器/缓存）→ repeat 次正式测量，
    输出 per_input = [{args, ms(median), ms_all, result, ok}]——
    测的是 algorithm(input) 的真性能 + 抗噪中位数，而不是单次抖动值。
    """
    try:
        spec = json.loads(workload_json)
    except Exception as e:
        return {"ok": False, "error": "workload JSON 解析失败：" + str(e)}
    code = spec.get("code") or ""
    func = spec.get("func") or "f"
    inputs = spec.get("inputs") or []
    if not inputs:
        return {"ok": True, "per_input": [], "error": ""}
    repeat = max(1, int(spec.get("repeat") or 1))
    import statistics
    per_input = []
    for i, inp in enumerate(inputs):
        if isinstance(inp, dict) and "args" in inp:
            args = inp["args"]
        else:
            args = inp if isinstance(inp, list) else [inp]
        if not isinstance(args, list):
            args = [args]
        # v0.7：warmup 与正式测量必须在同一进程/解释器状态里完成——
        # 拼成一段代码：先 warmup 1 次（不计入），再正式测量 repeat 次，
        # 输出全部原始测量（_TIMES_ 数组）+ 最终结果（_RESULT_）。
        call_code = "\n".join([
            code,
            "import time as _t, json as _j",
            "_a = " + json.dumps(args, ensure_ascii=False),
            "def _bench():",
            "    _t0 = _t.perf_counter()",
            "    _r = " + func + "(*_a)",
            "    return (_t.perf_counter() - _t0) * 1000, _r",
            "_ = _bench()  # warmup（不计入正式测量）",
            "_ms = []",
            "_r = None",
            "for _i in range(" + str(repeat) + "):",
            "    _m, _r = _bench()",
            "    _ms.append(_m)",
            "print('_TIMES_ ' + _j.dumps(_ms))",
            "print('_RESULT_ ' + _j.dumps(_r))",
        ])
        runner = SandboxRunner()
        runner.enable()
        result = runner.run_code(call_code, language="python")
        item = {"args": args, "ok": result.success, "ms": None, "result": None}
        if result.success:
            ms_all, res = [], None
            for line in result.stdout.strip().splitlines():
                if line.startswith("_TIMES_ "):
                    raw = line[len("_TIMES_ "):].strip()
                    try:
                        ms_all = [round(float(x), 3) for x in json.loads(raw)]
                    except Exception:
                        ms_all = []
                elif line.startswith("_RESULT_ "):
                    raw = line[len("_RESULT_ "):].strip()
                    try:
                        res = json.loads(raw)
                    except Exception:
                        res = raw
            if ms_all:
                item["ms"] = round(statistics.median(ms_all), 3)
                item["ms_all"] = ms_all
            else:
                item["ok"] = False
                item["error"] = "未产出测量数据"
            item["result"] = res
        else:
            item["error"] = result.error or result.stderr.strip()
        per_input.append(item)
    return {"ok": True, "per_input": per_input, "error": ""}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", required=False)
    ap.add_argument("--measure", choices=["time", "score"], default="time")
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--verify", default=None)
    ap.add_argument("--workload", default=None)
    args = ap.parse_args()

    if args.workload:
        out = run_workload(args.workload)
        print(json.dumps(out, ensure_ascii=False))
        return 0 if out.get("ok", False) else 1

    if args.verify:
        out = verify_code(args.code, args.verify)
        print(json.dumps(out, ensure_ascii=False))
        return 0 if out.get("ok", False) else 1

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
