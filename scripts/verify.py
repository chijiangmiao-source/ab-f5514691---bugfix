#!/usr/bin/env python3
"""One-shot acceptance routine for the shim-correction review stack.

Order of checks (mirrors the acceptance contract):
  1. Confirm the no-solution evidence for a non-divisible constraint (and a
     zero-row constraint) via the review API, using values beyond the
     IEEE-754 safe-integer range.
  2. Run the code test-suite and the build checks.
  3. HTTP smoke-test the health path and the review endpoint.
  4. Submit a solvable review whose oversized coefficient multiplies a
     correction of 2, then recompute the per-constraint audit from exact
     integer text: every term product and every left-hand sum must equal
     its target.  Regress an ordinary safe-range solvable case too.
The process exit code reports the overall result: 0 = pass, 1 = fail.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_BASE_URL = os.environ.get("APP_BASE_URL", "http://127.0.0.1:8080").rstrip("/")

# 2**53 + 1 exceeds the JavaScript/IEEE-754 safe-integer range; the whole
# stack must still carry it as exact integer text.
BIG_TARGET = str(2**53 + 1)  # 9007199254740993

# A solvable coupled-displacement case: the oversized coefficient is paired
# with a correction of exactly 2 (solution [K1, K2, K3] = [2, 3, 1]).  The
# engineer must be able to recompute every product and left-hand sum.
BIG_COEFFICIENT = 2**64 + 3  # 18446744073709551619
INT_TEXT_RE = re.compile(r"^-?\d+$")

failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> bool:
    print(f"[{'PASS' if condition else 'FAIL'}] {name}", flush=True)
    if not condition:
        if detail:
            print(f"       {detail}", flush=True)
        failures.append(name)
    return condition


def http(method: str, url: str, payload=None) -> tuple[int, str]:
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as exc:
        return -1, str(exc)


def post_review(payload) -> tuple[int, dict | None]:
    status, text = http("POST", f"{APP_BASE_URL}/api/review", payload)
    try:
        return status, json.loads(text)
    except json.JSONDecodeError:
        return status, None


def wait_for_app(attempts: int = 30, delay: float = 1.0) -> bool:
    for _ in range(attempts):
        status, _ = http("GET", f"{APP_BASE_URL}/health")
        if status == 200:
            return True
        time.sleep(delay)
    return False


def step1_obstruction_evidence() -> None:
    print("\n== 步骤 1：不可整除约束的无解证据 ==", flush=True)
    status, body = post_review(
        {
            "variables": ["shim_north", "shim_south"],
            "matrix": [["2", "0"], ["0", "2"]],
            "target": [BIG_TARGET, "4"],
        }
    )
    check("不可整除约束复核返回 HTTP 200", status == 200, f"status={status}")
    check("判定为无整数解", bool(body) and body.get("solvable") is False,
          f"body={body!r}"[:400])
    obstruction = (body or {}).get("obstruction") or {}
    check("障碍类型为规范除尽障碍 non_divisible",
          obstruction.get("type") == "non_divisible", f"obstruction={obstruction!r}"[:400])
    check("主元精确为 2", obstruction.get("pivot") == "2")
    check("变换后目标以精确大整数文本呈现",
          obstruction.get("transformedTarget") == BIG_TARGET,
          f"got {obstruction.get('transformedTarget')!r}")
    check("余数精确为 1（主元不能整除变换后目标）",
          obstruction.get("remainder") == "1")
    u_terms = obstruction.get("uRowTerms") or []
    total = sum(int(term["product"]) for term in u_terms) if u_terms else None
    check("行变换各项乘积之和等于变换后目标",
          total is not None and str(total) == BIG_TARGET)


def step1b_zero_row_evidence() -> None:
    print("\n== 步骤 1b：零行目标非零的无解证据 ==", flush=True)
    status, body = post_review(
        {
            "variables": ["shim_a", "shim_b"],
            "matrix": [["1", "1"], ["2", "2"]],
            "target": ["1", "3"],
        }
    )
    check("零行约束复核返回 HTTP 200", status == 200, f"status={status}")
    obstruction = (body or {}).get("obstruction") or {}
    check("障碍类型为零行目标非零 zero_row",
          obstruction.get("type") == "zero_row", f"obstruction={obstruction!r}"[:400])
    transformed = obstruction.get("transformedTarget")
    check("零行变换后目标为非零精确整数文本",
          bool(transformed) and INT_TEXT_RE.match(transformed) and int(transformed) != 0,
          f"transformedTarget={transformed!r}")
    u_terms = obstruction.get("uRowTerms") or []
    total = sum(int(term["product"]) for term in u_terms) if u_terms else None
    check("零行行变换各项乘积之和等于变换后目标",
          total is not None and str(total) == transformed,
          f"total={total!r} transformed={transformed!r}")


def assert_exact_solvable_audit(payload: dict, expected_solution: list[str],
                                tag: str) -> None:
    """Submit a solvable review and recompute every audit value as exact int."""
    status, body = post_review(payload)
    check(f"[{tag}] 复核返回 HTTP 200", status == 200, f"status={status}")
    check(f"[{tag}] 判定为可解",
          bool(body) and body.get("solvable") is True, f"body={body!r}"[:400])
    check(f"[{tag}] 精确整数校正量为 {expected_solution}",
          bool(body) and body.get("solution") == expected_solution,
          f"solution={(body or {}).get('solution')!r}")

    constraints = (body or {}).get("constraints")
    ok_shape = isinstance(constraints, list) and len(constraints) == len(
        payload["matrix"]
    )
    check(f"[{tag}] 返回逐约束明细，条数一致", ok_shape,
          f"constraints={constraints!r}"[:300])
    if not ok_shape:
        return

    all_equal = True
    details: list[str] = []
    for i, constraint in enumerate(constraints):
        terms = constraint.get("terms")
        if not isinstance(terms, list) or len(terms) != len(payload["variables"]):
            all_equal = False
            details.append(f"约束 {i + 1} 缺少逐项明细")
            continue
        left_sum = 0
        for term in terms:
            coefficient = term.get("coefficient")
            correction = term.get("correction")
            product = term.get("product")
            texts = (coefficient, correction, product,
                     constraint.get("sum"), constraint.get("target"))
            if not all(isinstance(t, str) and INT_TEXT_RE.match(t) for t in texts):
                all_equal = False
                details.append(f"约束 {i + 1} 存在非精确整数文本：{texts!r}")
                continue
            recomputed = int(coefficient) * int(correction)
            if int(product) != recomputed:
                all_equal = False
                details.append(
                    f"约束 {i + 1} 乘积失真：{coefficient}×{correction}"
                    f"={recomputed} != {product}"
                )
            left_sum += recomputed
        if str(left_sum) != constraint.get("sum"):
            all_equal = False
            details.append(
                f"约束 {i + 1} 左侧和失真：逐项复算 {left_sum}"
                f" != {constraint.get('sum')}"
            )
        if constraint.get("sum") != constraint.get("target"):
            all_equal = False
            details.append(
                f"约束 {i + 1} 左侧和 {constraint.get('sum')}"
                f" != 目标 {constraint.get('target')}"
            )
        if constraint.get("satisfied") is not True:
            all_equal = False
            details.append(f"约束 {i + 1} 未稳定标记为已满足")
    check(f"[{tag}] 逐项乘积、左侧和均可精确复算且等于目标",
          all_equal, "; ".join(details)[:600])


def step4_solvable_exact_audit() -> None:
    print("\n== 步骤 4：含超大系数可解复核的逐项精确复算 ==", flush=True)
    big = BIG_COEFFICIENT
    assert_exact_solvable_audit(
        {
            "variables": ["K1", "K2", "K3"],
            "matrix": [
                [str(big), "1", "0"],
                ["1", "3", "1"],
                ["0", "2", "4"],
            ],
            "target": [str(2 * big + 3), "12", "10"],
        },
        ["2", "3", "1"],
        "超大系数",
    )

    print("\n-- 回归：普通安全范围整数可解约束 --", flush=True)
    assert_exact_solvable_audit(
        {
            "variables": ["a", "b"],
            "matrix": [["2", "1"], ["1", "-1"]],
            "target": ["7", "2"],
        },
        ["3", "1"],
        "普通整数",
    )

    print("\n-- 回归：欠定系统的自由校正方向 --", flush=True)
    status, body = post_review(
        {"variables": ["x", "y"], "matrix": [["2", "3"]], "target": ["1"]}
    )
    check("欠定系统判定为可解",
          status == 200 and bool(body) and body.get("solvable") is True,
          f"status={status} body={body!r}"[:300])
    basis = (body or {}).get("homogeneousBasis") or []
    direction_ok = (
        len(basis) == 1
        and all(isinstance(v, str) and INT_TEXT_RE.match(v) for v in basis[0])
        and 2 * int(basis[0][0]) + 3 * int(basis[0][1]) == 0
    )
    check("自由校正方向为精确整数且落在齐次零空间",
          direction_ok, f"homogeneousBasis={basis!r}")


def run_subprocess(name: str, argv: list[str]) -> None:
    proc = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True)
    output = (proc.stdout + proc.stderr).strip()
    check(name, proc.returncode == 0, output[-2000:] if proc.returncode != 0 else "")
    if proc.returncode == 0 and output:
        for line in output.splitlines()[-3:]:
            print(f"       {line}", flush=True)


def step2_tests_and_build_checks() -> None:
    print("\n== 步骤 2：代码测试与构建检查 ==", flush=True)
    run_subprocess(
        "单元测试（求解器与 HTTP 层）",
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", "."],
    )
    run_subprocess(
        "语法构建检查 compileall",
        [sys.executable, "-m", "compileall", "-q", "app", "scripts", "tests"],
    )
    run_subprocess(
        "模块导入检查",
        [sys.executable, "-c", "import app.diophantine, app.server; print('imports ok')"],
    )
    for rel in (
        "Dockerfile",
        "docker-compose.yml",
        "app/static/index.html",
        "app/static/app.js",
        "app/static/style.css",
    ):
        check(f"构建所需文件存在：{rel}",
              os.path.exists(os.path.join(ROOT, rel)))


def step3_http_smoke() -> None:
    print("\n== 步骤 3：健康路径与复核接口 HTTP 冒烟 ==", flush=True)
    status, text = http("GET", f"{APP_BASE_URL}/health")
    healthy = False
    try:
        healthy = json.loads(text).get("status") == "ok"
    except json.JSONDecodeError:
        pass
    check("健康路径 /health 返回 200 且 status=ok", status == 200 and healthy,
          f"status={status} body={text[:200]}")

    status, text = http("GET", f"{APP_BASE_URL}/")
    check("页面 / 返回 200 且包含复核界面", status == 200 and "复核" in text,
          f"status={status}")

    status, body = post_review(
        {
            "variables": ["K1", "K2", "K3"],
            "matrix": [
                ["9007199254740993", "1", "0"],
                ["1", "3", "1"],
                ["0", "2", "4"],
            ],
            "target": ["18014398509481989", "12", "10"],
        }
    )
    check("复核接口（可解大整数用例）返回 HTTP 200", status == 200, f"status={status}")
    check("判定为可解", bool(body) and body.get("solvable") is True,
          f"body={body!r}"[:400])
    check("精确整数校正量为 [2, 3, 1]",
          bool(body) and body.get("solution") == ["2", "3", "1"])
    constraints = (body or {}).get("constraints") or []
    check("每条约束的左侧和均精确等于目标值",
          bool(constraints) and all(c["satisfied"] for c in constraints))
    if constraints:
        first_products = [t["product"] for t in constraints[0]["terms"]]
        check("首条约束各项乘积精确（含超大整数）",
              first_products == [str(9007199254740993 * 2), "3", "0"],
              f"products={first_products!r}")

    status, body = post_review(
        {"variables": ["x"], "matrix": [[1.5]], "target": [1]}
    )
    check("浮点系数被拒绝（HTTP 400，绝不四舍五入）",
          status == 400 and bool(body) and body.get("ok") is False,
          f"status={status} body={body!r}"[:300])


def main() -> int:
    print(f"验收目标：{APP_BASE_URL}", flush=True)
    if not wait_for_app():
        check("等待应用就绪", False, f"{APP_BASE_URL}/health 未在限定时间内就绪")
    else:
        print("应用已就绪。", flush=True)
        step1_obstruction_evidence()
        step1b_zero_row_evidence()
        step2_tests_and_build_checks()
        step3_http_smoke()
        step4_solvable_exact_audit()

    print("\n== 验收结论 ==", flush=True)
    if failures:
        print(f"失败 {len(failures)} 项：", flush=True)
        for name in failures:
            print(f"  - {name}", flush=True)
        return 1
    print("全部验收项通过。", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
