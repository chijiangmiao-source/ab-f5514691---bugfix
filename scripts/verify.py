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
    check("行变换来源逐项均为精确整数文本",
          all(
              isinstance(term.get("coefficient"), str)
              and isinstance(term.get("target"), str)
              and isinstance(term.get("product"), str)
              for term in obstruction.get("uRowTerms", [])
          ))
    u_terms = obstruction.get("uRowTerms") or []
    total = sum(int(term["product"]) for term in u_terms) if u_terms else None
    check("行变换各项乘积之和等于变换后目标",
          total is not None and str(total) == BIG_TARGET)
    for term in u_terms:
        check(
            f"行变换逐项乘积可复算：{term['coefficient']} × {term['target']} = {term['product']}",
            str(int(term["coefficient"]) * int(term["target"])) == term["product"],
        )


def verify_solvable_constraints(body, label: str, require_big_correction_two: bool) -> bool:
    """Recompute every per-constraint term/product/sum from exact integers."""
    ok = bool(body) and body.get("solvable") is True
    solution = (body or {}).get("solution")
    constraints = (body or {}).get("constraints") or []
    if not ok or not isinstance(solution, list) or not constraints:
        check(f"{label}：可解且含 solution/constraints 明细", False,
              f"body={body!r}"[:400])
        return False
    all_text = all(
        isinstance(value, str)
        for c in constraints
        for t in c.get("terms", [])
        for value in (t.get("coefficient"), t.get("correction"), t.get("product"))
    ) and all(isinstance(c.get("sum"), str) and isinstance(c.get("target"), str)
              for c in constraints)
    check(f"{label}：系数/校正量/乘积/左侧和/目标均为精确整数文本", all_text)

    all_ok = True
    saw_big_coeff_with_correction_2 = False
    for c in constraints:
        idx = c.get("index")
        recomputed_products = []
        for t in c.get("terms", []):
            coeff = int(t["coefficient"])
            correction = int(t["correction"])
            product = coeff * correction
            recomputed_products.append(product)
            if abs(coeff) > 2**53 and correction == 2:
                saw_big_coeff_with_correction_2 = True
            if str(product) != t["product"]:
                check(f"{label} 约束 {idx + 1}：逐项乘积 {t['coefficient']} × "
                      f"{t['correction']} 精确为 {t['product']}", False,
                      f"recomputed {product}")
                all_ok = False
        left_sum = sum(recomputed_products)
        target = int(c["target"])
        row_ok = (
            str(left_sum) == c["sum"]
            and left_sum == target
            and c.get("satisfied") is True
        )
        check(f"{label} 约束 {idx + 1}：逐项乘积之和 {left_sum} ＝ 左侧和 {c['sum']}"
              f" ＝ 目标 {c['target']}，稳定显示相等", row_ok)
        all_ok = all_ok and row_ok
    # The corrections displayed inside each term must match the top-level
    # solution vector, both as exact integer text.
    for c in constraints:
        for j, t in enumerate(c.get("terms", [])):
            if t.get("correction") != solution[j]:
                check(f"{label} 约束 {c.get('index') + 1} 项 {j + 1}：校正量 "
                      f"{t.get('correction')} 与解向量 {solution[j]} 一致", False)
                all_ok = False
    if require_big_correction_two:
        check(f"{label}：含超过 2^53 的系数且其对应校正量为 2",
              saw_big_coeff_with_correction_2)
        all_ok = all_ok and saw_big_coeff_with_correction_2
    return all_text and all_ok


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

    # --- 核心验收：含超大系数（>2^53）且其校正量为 2 的可解耦合系统 ------
    # 由精确整数解 x = [2, 1, 3] 反推耦合约束构造，整体解与每项目标精确成立：
    #   BIG*2 + 7*1 + (-3)*3 = 2*BIG - 2
    #   5*2  + 11*1 + 2*3   = 27
    #   -4*2 + 3*1  + 9*3   = 22
    big_coeff = 10**16 + 7  # 10000000000000007 > 2^53
    big_target = 2 * big_coeff - 2
    status, body = post_review(
        {
            "variables": ["K1", "K2", "K3"],
            "matrix": [
                [str(big_coeff), "7", "-3"],
                ["5", "11", "2"],
                ["-4", "3", "9"],
            ],
            "target": [str(big_target), "27", "22"],
        }
    )
    check("含超大系数的可解耦合复核返回 HTTP 200", status == 200, f"status={status}")
    check("判定为可解", bool(body) and body.get("solvable") is True,
          f"body={body!r}"[:400])
    check("精确整数校正量为 [2, 1, 3]（大系数对应校正量为 2）",
          bool(body) and body.get("solution") == ["2", "1", "3"],
          f"solution={(body or {}).get('solution')!r}")
    if status == 200 and (body or {}).get("solvable"):
        verify_solvable_constraints(body, "超大系数耦合用例", True)

    # --- 回归：README 原有大整数可解用例，逐约束同样精确可复算 -----------
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
    check("回归可解大整数用例返回 HTTP 200", status == 200, f"status={status}")
    check("精确整数校正量为 [2, 3, 1]",
          bool(body) and body.get("solution") == ["2", "3", "1"])
    if status == 200 and (body or {}).get("solvable"):
        verify_solvable_constraints(body, "回归大整数用例", True)

    # --- 回归：普通安全范围整数的可解约束 -------------------------------
    # 2p + q = 7, p + 3q = 6，解 [3, 1]
    status, body = post_review(
        {
            "variables": ["p", "q"],
            "matrix": [["2", "1"], ["1", "3"]],
            "target": ["7", "6"],
        }
    )
    check("普通整数可解用例返回 HTTP 200", status == 200, f"status={status}")
    check("普通整数用例校正量为 [3, 1]",
          bool(body) and body.get("solution") == ["3", "1"])
    if status == 200 and (body or {}).get("solvable"):
        verify_solvable_constraints(body, "普通整数用例", False)

    # --- 回归：欠定系统的自由校正方向 -----------------------------------
    # 单约束 2a + 3b = 1 可解；零空间一维，基向量代入必须精确得 0。
    status, body = post_review(
        {"variables": ["a", "b"], "matrix": [["2", "3"]], "target": ["1"]}
    )
    check("欠定可解用例返回 HTTP 200", status == 200, f"status={status}")
    basis = (body or {}).get("homogeneousBasis") or []
    solvable = bool(body) and body.get("solvable") is True
    check("欠定系统给出 1 个自由校正方向", solvable and len(basis) == 1,
          f"basis={basis!r}")
    if solvable and len(basis) == 1:
        v = [int(z) for z in basis[0]]
        null_value = 2 * v[0] + 3 * v[1]
        particular = [int(z) for z in body["solution"]]
        check("自由方向代入原约束精确为 0（叠加不改变左侧和）",
              null_value == 0, f"A·v = {null_value}")
        check("特解满足原约束 2a + 3b = 1",
              2 * particular[0] + 3 * particular[1] == 1)

    # --- 回归：零行目标非零的无解证据 -----------------------------------
    # [[1,1],[2,2]] 秩 1，目标 [1,3]：行变换后出现 0 = 1。
    status, body = post_review(
        {
            "variables": ["r", "s"],
            "matrix": [["1", "1"], ["2", "2"]],
            "target": ["1", "3"],
        }
    )
    obstruction = (body or {}).get("obstruction") or {}
    check("零行用例判定为无整数解",
          status == 200 and (body or {}).get("solvable") is False)
    check("障碍类型为 zero_row（零行目标非零）",
          obstruction.get("type") == "zero_row", f"obstruction={obstruction!r}"[:300])
    check("零行主元精确为 0 且变换后目标非零",
          obstruction.get("pivot") == "0"
          and obstruction.get("transformedTarget") not in (None, "0"))
    z_terms = obstruction.get("uRowTerms") or []
    z_total = sum(int(t["product"]) for t in z_terms) if z_terms else None
    check("零行行变换各项乘积之和等于非零变换后目标",
          z_total is not None and str(z_total) == obstruction.get("transformedTarget"))

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
