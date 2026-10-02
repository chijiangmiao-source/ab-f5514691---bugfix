"use strict";

/* 束流整形磁铁垫片校正复核 —— 前端逻辑。
 *
 * 精确性约定：所有整数在浏览器中始终以十进制字符串保存与展示，
 * 绝不经过 Number 转换，因此超过 2^53 的系数与目标不会丢失精度。
 *
 * 竞态约定：generation 为单调递增的世代号。发起复核、编辑草稿、
 * 取消复核都会使其递增；只有世代号仍为当前值的响应才允许渲染，
 * 过期响应一律丢弃，绝不覆盖当前草稿的状态或结论。
 */

const $ = (id) => document.getElementById(id);

const draftFields = {
  variables: $("variables"),
  matrix: $("matrix"),
  target: $("target"),
};
const runButton = $("run");
const cancelButton = $("cancel");
const statusLine = $("status");
const resultPanel = $("result-panel");
const resultBox = $("result");

let generation = 0; // 草稿/请求世代号
let inflight = null; // 在途请求的 AbortController

const INT_PATTERN = /^[+-]?\d+$/;

const EXAMPLE_SOLVABLE = {
  variables: "K1, K2, K3",
  matrix: "9007199254740993 1 0\n1 3 1\n0 2 4",
  target: "18014398509481989, 12, 10",
};

const EXAMPLE_UNSOLVABLE = {
  variables: "D1, D2",
  matrix: "2 0\n0 4",
  target: "9007199254740993, 8",
};

// ---------------------------------------------------------------- 草稿解析

function splitTokens(text) {
  return text.split(/[\s,，]+/).filter((token) => token.length > 0);
}

function parseIntegerToken(token, where) {
  if (!INT_PATTERN.test(token)) {
    throw new Error(
      `${where}：“${token}” 不是十进制整数。仅接受整数字符串，绝不进行浮点换算。`
    );
  }
  let text = token.replace(/^\+/, "");
  const negative = text.startsWith("-");
  let digits = negative ? text.slice(1) : text;
  digits = digits.replace(/^0+(?=\d)/, "");
  return (negative ? "-" : "") + digits;
}

function readDraft() {
  const variables = splitTokens(draftFields.variables.value);
  if (variables.length === 0) {
    throw new Error("请填写变量标识。");
  }
  if (new Set(variables).size !== variables.length) {
    throw new Error("变量标识存在重复。");
  }
  const rows = draftFields.matrix.value
    .split(/\n+/)
    .map((line) => line.trim())
    .filter((line) => line.length > 0);
  if (rows.length === 0) {
    throw new Error("请填写约束矩阵。");
  }
  const matrix = rows.map((line, i) => {
    const coeffs = splitTokens(line).map((token) =>
      parseIntegerToken(token, `矩阵第 ${i + 1} 行`)
    );
    if (coeffs.length !== variables.length) {
      throw new Error(
        `矩阵第 ${i + 1} 行有 ${coeffs.length} 个系数，与变量数 ${variables.length} 不一致。`
      );
    }
    return coeffs;
  });
  const target = splitTokens(draftFields.target.value).map((token) =>
    parseIntegerToken(token, "目标向量")
  );
  if (target.length !== matrix.length) {
    throw new Error(
      `目标向量长度 ${target.length} 与约束条数 ${matrix.length} 不一致。`
    );
  }
  return { variables, matrix, target };
}

// ---------------------------------------------------------------- 状态与竞态

function setStatus(message, kind) {
  statusLine.textContent = message;
  statusLine.dataset.kind = kind || "";
}

function markResultsStale() {
  if (!resultPanel.hidden && resultPanel.dataset.state === "fresh") {
    resultPanel.dataset.state = "stale";
    resultPanel.classList.add("stale");
  }
}

function invalidateDraft() {
  generation += 1; // 使任何在途响应立即过期
  if (inflight) {
    inflight.abort();
    inflight = null;
  }
  cancelButton.disabled = true;
  markResultsStale();
  setStatus("草稿已修改：先前计算即使返回也将被丢弃，不会覆盖当前草稿。", "warn");
}

Object.values(draftFields).forEach((field) =>
  field.addEventListener("input", invalidateDraft)
);

async function runReview() {
  let payload;
  try {
    payload = readDraft();
  } catch (error) {
    setStatus(error.message, "error");
    return;
  }
  const myGeneration = ++generation;
  if (inflight) {
    inflight.abort();
  }
  const controller = new AbortController();
  inflight = controller;
  cancelButton.disabled = false;
  setStatus("复核计算中……", "busy");
  try {
    const response = await fetch("/api/review", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal: controller.signal,
    });
    const data = await response.json().catch(() => null);
    if (myGeneration !== generation) {
      return; // 草稿已变更或已取消：丢弃过期响应
    }
    if (!response.ok || !data || data.ok !== true) {
      const message = data && data.error ? data.error : `HTTP ${response.status}`;
      setStatus(`复核失败：${message}`, "error");
      return;
    }
    renderResult(data);
    resultPanel.hidden = false;
    resultPanel.dataset.state = "fresh";
    resultPanel.classList.remove("stale");
    setStatus("复核完成。", "ok");
  } catch (error) {
    if (error && error.name === "AbortError") {
      return; // 已被取消或被更新的请求取代
    }
    if (myGeneration !== generation) {
      return;
    }
    setStatus(`复核请求失败：${error.message || error}`, "error");
  } finally {
    if (myGeneration === generation) {
      inflight = null;
      cancelButton.disabled = true;
    }
  }
}

function cancelReview() {
  generation += 1;
  if (inflight) {
    inflight.abort();
    inflight = null;
  }
  cancelButton.disabled = true;
  setStatus("已取消：先前计算若返回将被丢弃，不会覆盖当前草稿的状态或结论。", "warn");
}

runButton.addEventListener("click", runReview);
cancelButton.addEventListener("click", cancelReview);

function loadExample(example) {
  draftFields.variables.value = example.variables;
  draftFields.matrix.value = example.matrix;
  draftFields.target.value = example.target;
  invalidateDraft();
  setStatus("示例已载入，可发起复核。", "");
}

$("load-solvable").addEventListener("click", () => loadExample(EXAMPLE_SOLVABLE));
$("load-unsolvable").addEventListener("click", () => loadExample(EXAMPLE_UNSOLVABLE));

// ---------------------------------------------------------------- 结果渲染

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) {
    node.className = className;
  }
  if (text !== undefined) {
    node.textContent = text; // 精确整数文本，绝不经过 Number
  }
  return node;
}

function renderResult(data) {
  resultBox.replaceChildren();
  if (data.solvable) {
    renderSolution(data);
  } else {
    renderObstruction(data);
  }
  resultBox.appendChild(renderSmithSummary(data.smith));
}

function renderSolution(data) {
  resultBox.appendChild(
    el("p", "verdict ok", "结论：可解 —— 以下为精确整数校正量（最小垫片单位的整数倍）。")
  );
  const list = el("ul", "solution-list");
  data.variables.forEach((name, i) => {
    list.appendChild(el("li", "num", `${name} = ${data.solution[i]}`));
  });
  resultBox.appendChild(list);

  resultBox.appendChild(
    el("h3", null, "约束复核（点选任一约束，查看各项乘积、左侧和及其目标值）")
  );
  const listBox = el("div", "constraint-list");
  const detail = el("div", "constraint-detail");
  const constraints = buildConstraintDetails(data);
  constraints.forEach((constraint, idx) => {
    const button = el(
      "button",
      "constraint-item num",
      `约束 ${idx + 1}：左侧和 ${constraint.sum} ＝ 目标 ${constraint.target} ${
        constraint.satisfied ? "✓" : "✗"
      }`
    );
    button.type = "button";
    button.addEventListener("click", () => {
      listBox
        .querySelectorAll(".constraint-item")
        .forEach((node) => node.classList.remove("active"));
      button.classList.add("active");
      renderConstraintDetail(detail, constraint);
    });
    listBox.appendChild(button);
  });
  resultBox.appendChild(listBox);
  resultBox.appendChild(detail);
  const first = listBox.querySelector(".constraint-item");
  if (first) {
    first.click();
  }

  if (data.homogeneousBasis && data.homogeneousBasis.length > 0) {
    const details = el("details", "homogeneous");
    details.appendChild(
      el(
        "summary",
        null,
        `自由校正方向（${data.homogeneousBasis.length} 个）：叠加其任意整数倍不改变任何左侧和`
      )
    );
    const table = el("table", "num");
    const head = el("tr");
    head.appendChild(el("th", null, "方向"));
    data.variables.forEach((name) => head.appendChild(el("th", null, name)));
    table.appendChild(head);
    data.homogeneousBasis.forEach((vector, k) => {
      const row = el("tr");
      row.appendChild(el("td", null, `方向 ${k + 1}`));
      vector.forEach((value) => row.appendChild(el("td", null, value)));
      table.appendChild(row);
    });
    details.appendChild(table);
    resultBox.appendChild(details);
  }
}

function buildConstraintDetails(data) {
  // 服务端已给出每条约束的逐项明细，且所有值均为十进制整数字符串。
  // 这里在浏览器中用 BigInt 独立复算一遍：绝不经过 Number，因此即使
  // 系数、乘积或左侧和超过 2^53，相等结论依然精确、稳定。
  return data.constraints.map((input) => {
    let bigSum = 0n;
    const terms = input.terms.map((term) => {
      const coefficient = BigInt(term.coefficient);
      const correction = BigInt(term.correction);
      const product = coefficient * correction;
      bigSum += product;
      return {
        variable: term.variable,
        coefficient: term.coefficient,
        correction: term.correction,
        product: product.toString(),
        productMatches: product.toString() === term.product,
      };
    });
    const sum = bigSum.toString();
    const target = BigInt(input.target).toString();
    return {
      index: input.index,
      terms,
      sum,
      target: input.target,
      // 以本地精确复算为唯一判据：逐项乘积与服务端明细一致，
      // 且左侧和与目标值精确相等（绝不依赖 Number 比较或服务端标志）。
      satisfied:
        sum === target && terms.every((term) => term.productMatches),
    };
  });
}

function renderConstraintDetail(parent, constraint) {
  parent.replaceChildren();
  const table = el("table", "num");
  const head = el("tr");
  ["变量", "系数", "校正量", "乘积（系数 × 校正量）"].forEach((title) =>
    head.appendChild(el("th", null, title))
  );
  table.appendChild(head);
  constraint.terms.forEach((term) => {
    const row = el("tr");
    row.appendChild(el("td", null, term.variable));
    row.appendChild(el("td", null, term.coefficient));
    row.appendChild(el("td", null, term.correction));
    row.appendChild(el("td", null, term.product));
    table.appendChild(row);
  });
  parent.appendChild(table);
  parent.appendChild(el("p", "num", `左侧和 = ${constraint.sum}`));
  parent.appendChild(el("p", "num", `目标值 = ${constraint.target}`));
  parent.appendChild(
    el(
      "p",
      constraint.satisfied ? "verdict ok" : "verdict bad",
      constraint.satisfied ? "左侧和与目标值精确相等 ✓" : "左侧和与目标值不相等 ✗"
    )
  );
}

function renderObstruction(data) {
  const ob = data.obstruction;
  resultBox.appendChild(
    el("p", "verdict bad", "结论：无整数解 —— 存在由可逆整数行变换导出的规范除尽障碍。")
  );
  const explanation =
    ob.type === "non_divisible"
      ? `对约束系统施加可逆整数行变换（Smith 正规形 U·A·V = D）后，第 ${
          ob.row + 1
        } 行的主元为 ${ob.pivot}，而变换后目标为 ${
          ob.transformedTarget
        }，余数 ${ob.remainder} ≠ 0：主元不能整除变换后目标，因此不存在整数校正量。`
      : `对约束系统施加可逆整数行变换（Smith 正规形 U·A·V = D）后，第 ${
          ob.row + 1
        } 行为零行（主元 0），但变换后目标为 ${
          ob.transformedTarget
        } ≠ 0：等式 0 = ${ob.transformedTarget} 不可能成立。`;
  resultBox.appendChild(el("p", "explanation", explanation));

  const facts = el("table", "num facts");
  [
    ["障碍类型", ob.type === "non_divisible" ? "主元除尽失败" : "零行目标非零"],
    ["变换后行号", `第 ${ob.row + 1} 行`],
    ["主元", ob.pivot],
    ["变换后目标", ob.transformedTarget],
    ["余数（变换后目标 mod 主元）", ob.remainder],
  ].forEach(([key, value]) => {
    const row = el("tr");
    row.appendChild(el("th", null, key));
    row.appendChild(el("td", null, value));
    facts.appendChild(row);
  });
  resultBox.appendChild(facts);

  resultBox.appendChild(
    el(
      "h3",
      null,
      `行变换来源：变换后目标 = 第 ${ob.row + 1} 行变换行 U 与原始目标向量的乘积和`
    )
  );
  const table = el("table", "num");
  const head = el("tr");
  ["原约束行", "行变换系数 U", "原目标值", "乘积（U × 原目标）"].forEach((title) =>
    head.appendChild(el("th", null, title))
  );
  table.appendChild(head);
  ob.uRowTerms.forEach((term) => {
    const row = el("tr");
    row.appendChild(el("td", null, `约束 ${term.row + 1}`));
    row.appendChild(el("td", null, term.coefficient));
    row.appendChild(el("td", null, term.target));
    row.appendChild(el("td", null, term.product));
    table.appendChild(row);
  });
  resultBox.appendChild(table);
  resultBox.appendChild(
    el("p", "num", `各项乘积之和 = ${ob.transformedTarget}（即变换后目标）`)
  );
}

function renderSmithSummary(smith) {
  const details = el("details", "smith");
  details.appendChild(el("summary", null, "Smith 正规形摘要（可逆整数行变换导出）"));
  details.appendChild(el("p", "num", `秩 = ${smith.rank}`));
  details.appendChild(
    el("p", "num", `主元序列（d₁ | d₂ | …）= [${smith.diagonal.join(", ")}]`)
  );
  details.appendChild(
    el("p", "num", `变换后目标向量 U·b = [${smith.transformedTarget.join(", ")}]`)
  );
  return details;
}

// ---------------------------------------------------------------- 初始化

loadExample(EXAMPLE_SOLVABLE);
setStatus("已载入可解示例，可直接发起复核。", "");
