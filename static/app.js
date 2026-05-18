const riskMeta = {
  LOW: {
    cls: "low",
    label: "Ready to send",
    confirmText: "Release transfer",
    confirmCls: "",
  },
  MEDIUM: {
    cls: "medium",
    label: "Enhanced review started",
    confirmText: "Wait for review",
    confirmCls: "caution",
  },
  HIGH: {
    cls: "high",
    label: "Transfer temporarily held",
    confirmText: "Wait for review",
    confirmCls: "danger",
  },
  CRITICAL: {
    cls: "critical",
    label: "Transfer blocked by protection",
    confirmText: "Wait for review",
    confirmCls: "danger",
  },
  UNKNOWN: {
    cls: "unknown",
    label: "Recipient could not be verified",
    confirmText: "Wait for review",
    confirmCls: "danger",
  },
  CLEAN: {
    cls: "low",
    label: "Ready to send",
    confirmText: "Release transfer",
    confirmCls: "",
  },
};

const presets = [
  { address: "3LQUu4v9z6KNch71j7kbj8GPeAGUo1FW6a", amount: "250", token: "BTC" },
  { address: "1GH9bkaD3QsZyFU1MRcvpmQLj4SiVpARit", amount: "0.5", token: "BTC" },
  { address: "TApEYDGz8eH9JywtaTWkTwczwPJH368aD3", amount: "15000", token: "USDT" },
  { address: "TDNxWTrZWXYBHpHkRH9qV6sbE1nx888888", amount: "5000", token: "TRX" },
];

const displayStages = [
  "Fetching address history",
  "Tracing outgoing funds",
  "Analyzing incoming sources",
  "Running risk assessment",
  "Generating report",
];

let pendingJobId = null;
let pendingInvestigating = false;
let pendingQuick = null;
const activeJobCards = new Set();

const $ = (id) => document.getElementById(id);

function setHidden(node, hidden) {
  node.hidden = hidden;
}

function setText(id, text) {
  $(id).textContent = text;
}

function detectChainLabel(address) {
  const token = $("token")?.value;
  if (address.startsWith("bc1") || address.startsWith("1") || address.startsWith("3")) return "BTC";
  if (address.startsWith("T")) return token === "USDT" ? "USDT-TRC20" : "TRX";
  if (address.startsWith("0x")) return "ETH";
  return "BTC / TRON";
}

function updateTokenMeta() {
  const token = $("token").value;
  setText("network-fee", token === "BTC" ? "~0.0001 BTC" : "~3 TRX network fee");
  setText("chain-chip", detectChainLabel($("address").value.trim()));
}

function resetResult() {
  setHidden($("result-panel"), true);
  setHidden($("activity"), true);
  setHidden($("error-banner"), true);
  setHidden($("loading"), true);
  setHidden($("review-btn"), false);
  pendingJobId = null;
  pendingInvestigating = false;
  pendingQuick = null;
}

function resetForm() {
  $("send-form").reset();
  document.querySelectorAll(".preset-btn").forEach((button) => button.classList.remove("active"));
  setText("chain-chip", "BTC / TRON");
  updateTokenMeta();
  resetResult();
}

function loadPreset(index) {
  const preset = presets[index];
  $("address").value = preset.address;
  $("amount").value = preset.amount;
  $("token").value = preset.token;
  setText("chain-chip", detectChainLabel(preset.address));
  updateTokenMeta();
  document.querySelectorAll(".preset-btn").forEach((button) => {
    button.classList.toggle("active", button.dataset.preset === String(index));
  });
  resetResult();
}

function renderList(list, items) {
  list.replaceChildren();
  for (const item of items || []) {
    const li = document.createElement("li");
    li.textContent = item;
    list.append(li);
  }
}

function showError(message) {
  const banner = $("error-banner");
  banner.textContent = message;
  setHidden(banner, false);
  setHidden($("review-btn"), false);
}

async function reviewTransfer(event) {
  event.preventDefault();

  const address = $("address").value.trim();
  const amountInput = $("amount").value || "1000";
  const amount = parseFloat(amountInput) || 1000;
  const token = $("token").value;

  if (!address) {
    showError("Enter a BTC, TRX, or USDT-TRC20 recipient address before continuing.");
    return;
  }

  setText("chain-chip", detectChainLabel(address));
  setHidden($("review-btn"), true);
  setHidden($("loading"), false);
  setHidden($("result-panel"), true);
  setHidden($("error-banner"), true);

  try {
    const response = await fetch("/api/review", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ address, amount_usd: amount, token }),
    });
    const data = await response.json();

    setHidden($("loading"), true);

    if (!response.ok || data.error) {
      showError(data.error || "Server error. Try again in a moment.");
      return;
    }

    pendingJobId = data.job_id;
    pendingInvestigating = data.investigating;
    pendingQuick = data.quick;
    showQuickResult(data.quick, data.investigating, data.policy);

    if (data.investigating) {
      addActivityCard(data.job_id, address, amountInput, token, true, data.quick);
    }
  } catch {
    setHidden($("loading"), true);
    showError("SwiftX protection is unavailable. Make sure the FastAPI server is running.");
  }
}

function showQuickResult(quick, investigating, policy) {
  const level = quick.risk_level || "UNKNOWN";
  const meta = riskMeta[level] || riskMeta.UNKNOWN;
  const header = $("result-header");
  const score = Number.isFinite(quick.score) ? quick.score : "--";

  const visualClass = policy?.decision === "warn" && !investigating ? "medium" : meta.cls;
  header.className = `result-header ${visualClass}`;
  setText("risk-level-text", policy?.headline || meta.label);
  setText("score-badge", `${score}/100`);

  const facts = [];
  if (quick.tx_count != null) facts.push(`${quick.tx_count} transactions`);
  if (quick.first_seen_days_ago != null) facts.push(`first seen ${Math.round(quick.first_seen_days_ago)} days ago`);
  if (quick.balance) facts.push(`balance ${quick.balance}`);
  if (quick.asset) facts.push(`asset ${quick.asset}`);
  setText(
    "user-message",
    policy?.message || buildQuickMessage(level, investigating, facts)
  );

  const detailItems = policy?.details?.length ? policy.details : quick.evidence || [];
  renderList($("evidence-list"), detailItems);
  setHidden($("evidence-title"), !detailItems.length);
  setHidden($("deep-notice"), !investigating);

  const confirmButton = $("confirm-btn");
  confirmButton.textContent = meta.confirmText;
  confirmButton.className = `confirm-btn ${meta.confirmCls}`.trim();
  confirmButton.disabled = investigating || policy?.allow_release === false;

  setHidden($("result-panel"), false);
}

function buildQuickMessage(level, investigating, facts) {
  const factText = facts.length ? ` Check details: ${facts.join(", ")}.` : "";

  if (investigating) {
    return `For your protection, SwiftX is holding this transfer for a deeper review before releasing funds.${factText} You can cancel now, or wait for the safety report before deciding.`;
  }

  if (level === "CLEAN" || level === "LOW") {
    return facts.length
      ? `SwiftX completed the check. We did not find a reason to slow down this transfer.${factText}`
      : "SwiftX completed the check. We did not find a reason to slow down this transfer.";
  }

  return facts.length
    ? `SwiftX needs a little more time before this transfer can be released.${factText}`
    : "SwiftX needs a little more time before this transfer can be released.";
}

function confirmTransfer() {
  const address = $("address").value.trim();
  const amount = $("amount").value || "0";
  const token = $("token").value;
  const jobId = pendingJobId;
  const investigating = pendingInvestigating;
  const quick = pendingQuick;

  resetForm();
  if (!investigating) return;

  if (!activeJobCards.has(jobId)) {
    addActivityCard(jobId, address, amount, token, investigating, quick);
  }
}

function addActivityCard(jobId, address, amount, token, investigating, quick) {
  if (!jobId) return;
  if (!investigating) return;
  if (activeJobCards.has(jobId)) return;
  activeJobCards.add(jobId);

  const card = document.createElement("article");
  card.className = "tx-card";
  card.id = `card-${jobId}`;

  const top = document.createElement("button");
  top.type = "button";
  top.className = "tx-top";
  top.addEventListener("click", () => toggleDetail(jobId));

  const left = document.createElement("div");
  const addressEl = document.createElement("span");
  addressEl.className = "tx-address";
  addressEl.textContent = address;
  const metaEl = document.createElement("span");
  metaEl.className = "tx-meta";
  metaEl.id = `meta-${jobId}`;
  metaEl.textContent = investigating ? "SwiftX protection queued..." : "Pre-release check complete";
  const badge = document.createElement("span");
  badge.className = "tx-badge";
  badge.id = `badge-${jobId}`;
  badge.textContent = investigating ? "AGENT REVIEW" : (quick?.risk_level || "REVIEWED");
  left.append(addressEl, metaEl, badge);

  const right = document.createElement("div");
  const amountEl = document.createElement("div");
  amountEl.className = "tx-amount";
  amountEl.textContent = `${amount} ${token}`;
  const timeEl = document.createElement("div");
  timeEl.className = "tx-time";
  timeEl.textContent = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  right.append(amountEl, timeEl);
  top.append(left, right);

  const stages = document.createElement("ol");
  stages.className = "stage-list";
  stages.id = `stages-${jobId}`;
  for (const [index, stage] of displayStages.entries()) {
    const item = document.createElement("li");
    item.id = `stage-${jobId}-${index}`;
    item.className = index === 0 && investigating ? "active" : "";
    item.textContent = stage;
    stages.append(item);
  }
  if (!investigating) stages.hidden = true;

  const detail = document.createElement("div");
  detail.className = "tx-detail";
  detail.id = `detail-${jobId}`;
  detail.hidden = true;

  card.append(top, stages, detail);
  $("activity-list").prepend(card);

  pollJob(jobId);
}

function pollJob(jobId) {
  let stageIndex = 0;
  let intervalId = null;

  const checkStatus = async () => {
    try {
      const response = await fetch(`/api/result/${jobId}`);
      const data = await response.json();

      const serverStage = data.stage || "";
      setText(`meta-${jobId}`, serverStage ? `${serverStage}...` : "Agent review running...");
      stageIndex = updateStage(jobId, serverStage, stageIndex);

      if (data.status === "complete" && data.result) {
        if (intervalId) window.clearInterval(intervalId);
        markAllStagesDone(jobId);
        finalizeCard(jobId, data.result);
      } else if (data.status === "error") {
        if (intervalId) window.clearInterval(intervalId);
        finalizeCard(jobId, {
          risk_level: "UNKNOWN",
          user_message: `Investigation failed: ${data.error || "unknown error"}`,
          evidence: [],
          suggestions: ["Retry the review before sending."],
        });
      }
    } catch {
      setText(`meta-${jobId}`, "Waiting for agent review status...");
    }
  };

  checkStatus();
  intervalId = window.setInterval(checkStatus, 4000);
}

function updateStage(jobId, serverStage, currentIndex) {
  const nextIndex = displayStages.findIndex((stage) => {
    const firstWord = stage.split(" ")[0].toLowerCase();
    return serverStage.toLowerCase().includes(firstWord);
  });

  if (nextIndex <= currentIndex) return currentIndex;

  for (let i = 0; i < nextIndex; i += 1) {
    const el = $(`stage-${jobId}-${i}`);
    if (el) el.className = "done";
  }
  const active = $(`stage-${jobId}-${nextIndex}`);
  if (active) active.className = "active";
  return nextIndex;
}

function markAllStagesDone(jobId) {
  for (let i = 0; i < displayStages.length; i += 1) {
    const el = $(`stage-${jobId}-${i}`);
    if (el) el.className = "done";
  }
}

function finalizeCard(jobId, result) {
  const level = result.risk_level || "UNKNOWN";
  const meta = riskMeta[level] || riskMeta.UNKNOWN;
  const badge = $(`badge-${jobId}`);
  const stages = $(`stages-${jobId}`);

  setText(`meta-${jobId}`, meta.label);
  badge.textContent = level;
  badge.className = `tx-badge ${level.toLowerCase()}`;
  if (stages) stages.hidden = true;
  setHidden($("activity"), false);
  enableFinalDecision(jobId, level);

  const detail = $(`detail-${jobId}`);
  detail.replaceChildren();

  if (result.headline) {
    const headline = document.createElement("h3");
    headline.className = "tx-detail-headline";
    headline.textContent = result.headline;
    detail.append(headline);
  }

  const message = document.createElement("p");
  message.className = "tx-customer-message";
  message.textContent = result.user_message || result.warning_text || "Review complete.";
  detail.append(message);

  appendDetailList(detail, "What we found", result.detailed_findings || result.evidence || []);
  appendDetailList(detail, "Scam warning signs", result.scam_red_flags || []);
  appendDetailList(detail, "What to do now", result.suggestions || []);

  if (result.reassurance) {
    const reassurance = document.createElement("p");
    reassurance.className = "tx-reassurance";
    reassurance.textContent = result.reassurance;
    detail.append(reassurance);
  }

  detail.hidden = false;
}

function enableFinalDecision(jobId, level) {
  if (jobId !== pendingJobId) return;

  const meta = riskMeta[level] || riskMeta.UNKNOWN;
  const confirmButton = $("confirm-btn");
  confirmButton.disabled = false;
  confirmButton.textContent = level === "HIGH" || level === "CRITICAL"
    ? "Override warning"
    : meta.confirmText.replace("Wait for review", "Release transfer");
  confirmButton.className = `confirm-btn ${meta.confirmCls}`.trim();

  setText(
    "risk-level-text",
    level === "HIGH" || level === "CRITICAL"
      ? "Agent recommends canceling"
      : meta.label
  );
}

function appendDetailList(detail, title, items) {
  if (!items.length) return;
  const heading = document.createElement("h3");
  heading.textContent = title;
  const list = document.createElement("ul");
  list.className = "tx-detail-list";
  renderList(list, items);
  detail.append(heading, list);
}

function toggleDetail(jobId) {
  const detail = $(`detail-${jobId}`);
  if (!detail || !detail.childElementCount) return;
  detail.hidden = !detail.hidden;
}

document.querySelectorAll(".preset-btn").forEach((button) => {
  button.addEventListener("click", () => loadPreset(Number(button.dataset.preset)));
});

$("send-form").addEventListener("submit", reviewTransfer);
$("cancel-btn").addEventListener("click", resetForm);
$("confirm-btn").addEventListener("click", confirmTransfer);
$("token").addEventListener("change", updateTokenMeta);
$("address").addEventListener("input", (event) => setText("chain-chip", detectChainLabel(event.target.value.trim())));
updateTokenMeta();
