#!/usr/bin/env python3
"""One-shot acceptance routine for the shim-correction review stack.

Order of checks (mirrors the acceptance contract):
  1. Confirm the no-solution evidence for a non-divisible constraint via
     the review API (with a target beyond the IEEE-754 safe-integer range).
  2. Run the code test-suite and the build checks.
  3. HTTP smoke-test the health path and the review endpoint.
The process exit code reports the overall result: 0 = pass, 1 = fail.
"""

from __future__ import annotations

import json
import os
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
        step2_tests_and_build_checks()
        step3_http_smoke()

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
