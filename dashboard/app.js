const SIGNAL_URL = "signals/signal.json";
const EVENTS_URL = "events.json";
const STORAGE = {
  history: "spx0dte:history",
  interval: "spx0dte:interval",
  notif: "spx0dte:notif",
  lastSignalKey: "spx0dte:lastSignalKey",
  seenEvents: "spx0dte:seenEvents",
};

const els = {
  card: document.getElementById("signalCard"),
  value: document.getElementById("signalValue"),
  time: document.getElementById("signalTime"),
  confFill: document.getElementById("confidenceFill"),
  confText: document.getElementById("confidenceText"),
  history: document.getElementById("historyList"),
  clearHistory: document.getElementById("clearHistoryBtn"),
  refreshStatus: document.getElementById("refreshStatus"),
  notifStatus: document.getElementById("notifStatus"),
  lastFetch: document.getElementById("lastFetch"),
  settingsBtn: document.getElementById("settingsBtn"),
  settingsDialog: document.getElementById("settingsDialog"),
  intervalSelect: document.getElementById("intervalSelect"),
  notifToggle: document.getElementById("notifToggle"),
  testNotifBtn: document.getElementById("testNotifBtn"),
  flowsList: document.getElementById("flowsList"),
  flowsMeta: document.getElementById("flowsMeta"),
  flowFilter: document.getElementById("flowFilter"),
  macroCard: document.getElementById("macroCard"),
  macroGrid: document.getElementById("macroGrid"),
};

const state = {
  intervalMs: Number(localStorage.getItem(STORAGE.interval)) || 15000,
  notif: localStorage.getItem(STORAGE.notif) === "1",
  history: loadHistory(),
  timer: null,
  events: [],
  flowFilter: "all",
};

function loadHistory() {
  try { return JSON.parse(localStorage.getItem(STORAGE.history)) || []; }
  catch { return []; }
}
function saveHistory() {
  localStorage.setItem(STORAGE.history, JSON.stringify(state.history.slice(0, 50)));
}

function classify(signal) {
  const s = String(signal || "").trim().toUpperCase();
  if (s === "CALL" || s === "BUY" || s === "LONG") return "call";
  if (s === "PUT"  || s === "SELL" || s === "SHORT") return "put";
  if (s === "WAIT" || s === "HOLD") return "wait";
  return "none";
}

function formatTimestamp(ts) {
  if (!ts || ts === "not yet running") return "—";
  const d = new Date(ts);
  if (isNaN(d.getTime())) return ts;
  return d.toLocaleString(undefined, {
    month: "short", day: "numeric",
    hour: "numeric", minute: "2-digit", second: "2-digit",
  });
}

function relativeTime(ts) {
  const d = new Date(ts);
  if (isNaN(d.getTime())) return "";
  const diff = (Date.now() - d.getTime()) / 1000;
  if (diff < 60)   return `${Math.floor(diff)}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return d.toLocaleDateString();
}

function render(signalData) {
  const kind = classify(signalData.signal);
  els.card.classList.remove("signal-call", "signal-put", "signal-none", "signal-wait");
  els.card.classList.add(`signal-${kind}`);

  els.value.textContent = (signalData.signal || "NONE").toUpperCase();
  els.time.textContent = formatTimestamp(signalData.timestamp);

  const conf = Math.max(0, Math.min(100, Number(signalData.confidence) || 0));
  els.confFill.style.width = conf + "%";
  els.confText.textContent = conf + "%";
}

function renderHistory() {
  if (state.history.length === 0) {
    els.history.innerHTML = `<li class="history-empty">No signals recorded yet.</li>`;
    return;
  }
  els.history.innerHTML = state.history.slice(0, 20).map((h) => {
    const k = classify(h.signal);
    return `<li>
      <span class="h-signal ${k}">${(h.signal || "NONE").toUpperCase()}</span>
      <span class="h-conf">${Number(h.confidence) || 0}%</span>
      <span class="h-time">${relativeTime(h.timestamp)}</span>
    </li>`;
  }).join("");
}

function signalKey(d) {
  return `${String(d.signal || "").toUpperCase()}|${d.timestamp || ""}`;
}

function isActionable(d) {
  const k = classify(d.signal);
  return k === "call" || k === "put";
}

async function notifyNewSignal(d) {
  if (!state.notif) return;
  if (!("Notification" in window)) return;
  if (Notification.permission !== "granted") return;

  const title = `SPX 0DTE: ${String(d.signal).toUpperCase()}`;
  const body  = `Confidence ${Number(d.confidence) || 0}% • ${formatTimestamp(d.timestamp)}`;
  const opts  = { body, icon: "icons/icon-192.png", badge: "icons/icon-192.png", tag: "spx-signal" };

  try {
    if (navigator.serviceWorker && navigator.serviceWorker.ready) {
      const reg = await navigator.serviceWorker.ready;
      await reg.showNotification(title, opts);
    } else {
      new Notification(title, opts);
    }
  } catch (e) { /* notifications best-effort */ }
}

// ---------- Institutional flows ----------

const BUY_ACTIONS = new Set(["NEW", "ADD"]);
const SELL_ACTIONS = new Set(["EXIT", "TRIM"]);

function actionClass(a) {
  if (BUY_ACTIONS.has(a)) return "buy";
  if (SELL_ACTIONS.has(a)) return "sell";
  return "";
}

function formatValue(v) {
  v = Number(v) || 0;
  if (v >= 1e9) return "$" + (v / 1e9).toFixed(1) + "B";
  if (v >= 1e6) return "$" + (v / 1e6).toFixed(0) + "M";
  if (v >= 1e3) return "$" + (v / 1e3).toFixed(0) + "K";
  return "$" + v;
}

function formatPct(p) {
  p = Number(p) || 0;
  if (p >= 1) return "NEW";
  if (p <= -1) return "EXIT";
  const sign = p > 0 ? "+" : "";
  return sign + Math.round(p * 100) + "%";
}

function eventId(e) {
  return `${e.cik}|${e.cusip}|${e.quarter}|${e.action}`;
}

function passesFilter(e) {
  switch (state.flowFilter) {
    case "watch": return !!e.in_watchlist;
    case "buy":   return BUY_ACTIONS.has(e.action);
    case "sell":  return SELL_ACTIONS.has(e.action);
    default:      return true;
  }
}

function renderFlows() {
  const list = state.events.filter(passesFilter);
  if (list.length === 0) {
    els.flowsList.innerHTML = `<li class="history-empty">No matching filings.</li>`;
    return;
  }
  els.flowsList.innerHTML = list.map((e) => {
    const cls = actionClass(e.action);
    const sym = e.ticker || e.issuer || "—";
    const star = e.in_watchlist ? `<span class="watch-star" title="S&amp;P 100">★</span>` : "";
    return `<li class="flow-item">
      <div class="flow-main">
        <span class="flow-action ${cls}">${e.action}</span>
        <span class="flow-symbol">${sym}${star}</span>
        <span class="flow-pct ${cls}">${formatPct(e.pct_change)}</span>
      </div>
      <div class="flow-sub">
        <span class="flow-inst">${e.institution}</span>
        <span class="flow-val">${formatValue(e.value)} · ${e.quarter || ""}</span>
      </div>
    </li>`;
  }).join("");
}

function renderMacro(fred) {
  if (!fred || fred.length === 0) { els.macroCard.hidden = true; return; }
  els.macroCard.hidden = false;
  els.macroGrid.innerHTML = fred.map((f) => {
    const cur = parseFloat(f.value), prev = parseFloat(f.prev);
    let dirClass = "", arrow = "";
    if (!isNaN(cur) && !isNaN(prev)) {
      if (cur > prev) { dirClass = "buy"; arrow = "▲"; }
      else if (cur < prev) { dirClass = "sell"; arrow = "▼"; }
    }
    return `<div class="macro-item">
      <div class="macro-label">${f.label}</div>
      <div class="macro-value ${dirClass}">${f.value ?? "—"} <span class="macro-arrow">${arrow}</span></div>
    </div>`;
  }).join("");
}

function notifyNewFlows(events) {
  if (!state.notif || !("Notification" in window) || Notification.permission !== "granted") return;
  let seen = [];
  try { seen = JSON.parse(localStorage.getItem(STORAGE.seenEvents)) || []; } catch {}
  const seenSet = new Set(seen);
  const fresh = events.filter((e) => e.in_watchlist && !seenSet.has(eventId(e)));
  const ids = events.map(eventId);
  localStorage.setItem(STORAGE.seenEvents, JSON.stringify(ids.slice(0, 300)));

  // Only notify if we had a prior baseline (avoid alerting on first ever load).
  if (seen.length === 0 || fresh.length === 0) return;
  const top = fresh[0];
  const verb = BUY_ACTIONS.has(top.action) ? "loading up on" : "offloading";
  const extra = fresh.length > 1 ? ` (+${fresh.length - 1} more)` : "";
  notifyNewSignal({
    signal: `${top.institution} ${verb} ${top.ticker || top.issuer}`,
    confidence: Math.abs(Math.round((Number(top.pct_change) || 0) * 100)),
    timestamp: top.filed || new Date().toISOString(),
  });
}

async function fetchFlows() {
  try {
    const res = await fetch(EVENTS_URL + "?t=" + Date.now(), { cache: "no-store" });
    if (!res.ok) throw new Error("HTTP " + res.status);
    const data = await res.json();
    state.events = Array.isArray(data.events) ? data.events : [];
    renderFlows();
    renderMacro(data.fred);
    const gen = data.generated ? relativeTime(data.generated) : "";
    els.flowsMeta.textContent = `${state.events.length} filings · updated ${gen}`;
    notifyNewFlows(state.events);
  } catch (err) {
    els.flowsList.innerHTML = `<li class="history-empty">Flows unavailable.</li>`;
  }
}

async function fetchSignal() {
  try {
    const res = await fetch(SIGNAL_URL + "?t=" + Date.now(), { cache: "no-store" });
    if (!res.ok) throw new Error("HTTP " + res.status);
    const data = await res.json();
    handleSignal(data);
    els.lastFetch.textContent = new Date().toLocaleTimeString();
  } catch (err) {
    els.lastFetch.textContent = "error";
  }
}

function handleSignal(data) {
  render(data);

  const key = signalKey(data);
  const prevKey = localStorage.getItem(STORAGE.lastSignalKey);
  const changed = prevKey && prevKey !== key;

  if (changed) {
    state.history.unshift({
      signal: data.signal,
      confidence: data.confidence,
      timestamp: data.timestamp || new Date().toISOString(),
    });
    saveHistory();
    renderHistory();

    if (isActionable(data)) {
      notifyNewSignal(data);
    }
  }
  localStorage.setItem(STORAGE.lastSignalKey, key);
}

function startPolling() {
  if (state.timer) clearInterval(state.timer);
  state.timer = setInterval(fetchSignal, state.intervalMs);
  els.refreshStatus.textContent =
    state.intervalMs >= 60000 ? `${state.intervalMs / 60000}m` : `${state.intervalMs / 1000}s`;
}

function updateNotifStatus() {
  let label = "Off";
  if (state.notif) {
    if (!("Notification" in window)) label = "Unsupported";
    else if (Notification.permission === "granted") label = "On";
    else if (Notification.permission === "denied")  label = "Blocked";
    else label = "Pending";
  }
  els.notifStatus.textContent = label;
}

async function setNotifEnabled(enabled) {
  if (enabled && "Notification" in window) {
    if (Notification.permission === "default") {
      try { await Notification.requestPermission(); } catch {}
    }
    if (Notification.permission !== "granted") {
      enabled = false;
      els.notifToggle.checked = false;
    }
  }
  state.notif = enabled;
  localStorage.setItem(STORAGE.notif, enabled ? "1" : "0");
  updateNotifStatus();
}

function wireUI() {
  els.settingsBtn.addEventListener("click", () => {
    els.intervalSelect.value = String(state.intervalMs);
    els.notifToggle.checked = state.notif;
    els.settingsDialog.showModal();
  });

  els.intervalSelect.addEventListener("change", () => {
    state.intervalMs = Number(els.intervalSelect.value);
    localStorage.setItem(STORAGE.interval, String(state.intervalMs));
    startPolling();
  });

  els.notifToggle.addEventListener("change", () => {
    setNotifEnabled(els.notifToggle.checked);
  });

  els.testNotifBtn.addEventListener("click", async () => {
    if (!state.notif) {
      await setNotifEnabled(true);
      els.notifToggle.checked = state.notif;
    }
    notifyNewSignal({ signal: "TEST", confidence: 99, timestamp: new Date().toISOString() });
  });

  els.clearHistory.addEventListener("click", () => {
    state.history = [];
    saveHistory();
    renderHistory();
  });

  els.flowFilter.addEventListener("click", (e) => {
    const btn = e.target.closest(".seg-btn");
    if (!btn) return;
    state.flowFilter = btn.dataset.filter;
    els.flowFilter.querySelectorAll(".seg-btn").forEach((b) => b.classList.toggle("active", b === btn));
    renderFlows();
  });

  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") { fetchSignal(); fetchFlows(); }
  });
}

async function registerSW() {
  if (!("serviceWorker" in navigator)) return;
  try { await navigator.serviceWorker.register("sw.js"); } catch {}
}

function init() {
  wireUI();
  updateNotifStatus();
  renderHistory();
  fetchSignal();
  fetchFlows();
  startPolling();
  registerSW();
}

init();
