const SIGNAL_URL = "signals/signal.json";
const EVENTS_URL = "events.json";
const DAILY_URL = "daily.json";
const STORAGE = {
  history: "spx0dte:history",
  interval: "spx0dte:interval",
  notif: "spx0dte:notif",
  lastSignalKey: "spx0dte:lastSignalKey",
  seenEvents: "spx0dte:seenEvents",
  seenDaily: "spx0dte:seenDaily",
};

const els = {
  card: document.getElementById("signalCard"),
  value: document.getElementById("signalValue"),
  time: document.getElementById("signalTime"),
  confFill: document.getElementById("confidenceFill"),
  confText: document.getElementById("confidenceText"),
  tradeBox: document.getElementById("tradeBox"),
  signalDisclaimer: document.getElementById("signalDisclaimer"),
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
  stocksList: document.getElementById("stocksList"),
  stocksMeta: document.getElementById("stocksMeta"),
  stockFilter: document.getElementById("stockFilter"),
  quarterLabel: document.getElementById("quarterLabel"),
  dailyList: document.getElementById("dailyList"),
  dailyMeta: document.getElementById("dailyMeta"),
  dailyFilter: document.getElementById("dailyFilter"),
  macroCard: document.getElementById("macroCard"),
  macroGrid: document.getElementById("macroGrid"),
};

const state = {
  intervalMs: Number(localStorage.getItem(STORAGE.interval)) || 15000,
  notif: localStorage.getItem(STORAGE.notif) === "1",
  history: loadHistory(),
  timer: null,
  events: [],
  stocks: [],
  daily: [],
  flowFilter: "all",
  stockFilter: "all",
  dailyFilter: "all",
  expanded: new Set(),
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

  els.value.textContent = (signalData.direction || signalData.signal || "NONE").toUpperCase();
  els.time.textContent = formatTimestamp(signalData.timestamp);

  const conf = Math.max(0, Math.min(100, Number(signalData.confidence) || 0));
  els.confFill.style.width = conf + "%";
  els.confText.textContent = conf + "%";

  renderTrade(signalData.trade, signalData);
}

function money(v) {
  if (typeof v !== "number") return v || "—";
  return "$" + Math.round(v).toLocaleString();
}

function renderTrade(trade, sig) {
  if (!trade || trade.type === "none" || !trade.legs) {
    els.tradeBox.hidden = true;
    if (trade && trade.type === "none") {
      els.tradeBox.hidden = false;
      els.tradeBox.innerHTML = `<div class="trade-head"><span class="trade-name">No trade</span></div>
        <div class="trade-rationale">${trade.rationale || "Standing aside — no clear edge."}</div>`;
    }
  } else {
    els.tradeBox.hidden = false;
    const legs = trade.legs.map((l) => {
      const cls = l.action === "BUY" ? "buy" : "sell";
      return `<div class="leg">
        <span class="leg-act ${cls}">${l.action}</span>
        <span class="leg-desc">${l.strike} ${l.right}${trade.contracts > 1 ? ` ×${trade.contracts}` : ""}</span>
        <span class="leg-px">~$${l.est_price}</span>
      </div>`;
    }).join("");
    const reward = typeof trade.max_reward === "number" ? money(trade.max_reward) : (trade.max_reward || "open");
    els.tradeBox.innerHTML = `
      <div class="trade-head">
        <span class="trade-name">${trade.strategy}</span>
        <span class="trade-badge ${trade.pricing === "live" ? "live" : "est"}">${trade.pricing === "live" ? "LIVE " + (trade.instrument || "") : "ESTIMATE"}</span>
      </div>
      <div class="trade-expiry-row">${trade.expiry || ""}${trade.instrument ? " · " + trade.instrument : ""}</div>
      <div class="legs">${legs}</div>
      <div class="trade-stats">
        <div><div class="ts-label">Max risk</div><div class="ts-val sell">${money(trade.max_risk)}</div></div>
        <div><div class="ts-label">Max reward</div><div class="ts-val buy">${reward}</div></div>
        <div><div class="ts-label">Breakeven</div><div class="ts-val">${trade.breakeven ?? "—"}</div></div>
      </div>
      <div class="trade-rationale">${trade.rationale || ""}</div>`;
  }

  if (sig && sig.disclaimer) {
    els.signalDisclaimer.hidden = false;
    els.signalDisclaimer.textContent = sig.disclaimer;
  } else {
    els.signalDisclaimer.hidden = true;
  }
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

function eventId(e, quarter) {
  return `${quarter}|${e.institution}|${e.cusip}|${e.action}`;
}

function signedValue(v) {
  v = Number(v) || 0;
  const sign = v > 0 ? "+" : v < 0 ? "-" : "";
  return sign + formatValue(Math.abs(v));
}

function shortShares(n) {
  n = Number(n) || 0;
  const a = Math.abs(n);
  const sign = n > 0 ? "+" : n < 0 ? "-" : "";
  if (a >= 1e9) return sign + (a / 1e9).toFixed(1) + "B sh";
  if (a >= 1e6) return sign + (a / 1e6).toFixed(1) + "M sh";
  if (a >= 1e3) return sign + (a / 1e3).toFixed(0) + "K sh";
  return sign + a + " sh";
}

// ----- daily activity (Form 4 / 13D / 13G) -----

function shortDate(d) {
  const dt = new Date(d);
  if (isNaN(dt.getTime())) return d || "";
  return dt.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function passesDailyFilter(a) {
  switch (state.dailyFilter) {
    case "buy":   return a.type === "insider" && a.action === "BUY";
    case "sell":  return a.type === "insider" && a.action === "SELL";
    case "stake": return a.type === "stake";
    default:      return true;
  }
}

function renderDaily() {
  const list = state.daily.filter(passesDailyFilter);
  if (list.length === 0) {
    els.dailyList.innerHTML = `<li class="history-empty">No matching activity.</li>`;
    return;
  }
  els.dailyList.innerHTML = list.map((a) => {
    if (a.type === "stake") {
      return `<li class="flow-item">
        <div class="flow-main">
          <span class="flow-action stake">5%+</span>
          <span class="flow-symbol">${a.ticker}</span>
          <span class="flow-pct muted">${a.form}</span>
        </div>
        <div class="flow-sub">
          <span class="flow-inst">${a.company || ""}</span>
          <span class="flow-val">${shortDate(a.date)}</span>
        </div>
      </li>`;
    }
    const cls = a.action === "BUY" ? "buy" : "sell";
    const who = a.title ? `${a.owner} · ${a.title}` : a.owner;
    const om = a.open_market ? "" : ` <span class="muted">(non-open-mkt)</span>`;
    return `<li class="flow-item">
      <div class="flow-main">
        <span class="flow-action ${cls}">${a.action}</span>
        <span class="flow-symbol">${a.ticker}</span>
        <span class="flow-pct ${cls}">${signedValue(a.action === "BUY" ? a.value : -a.value)}</span>
      </div>
      <div class="flow-sub">
        <span class="flow-inst">${who || "Insider"}${om}</span>
        <span class="flow-val">${shortShares(a.action === "BUY" ? a.shares : -a.shares)} · ${shortDate(a.date)}</span>
      </div>
    </li>`;
  }).join("");
}

function dailyId(a) {
  return `${a.date}|${a.ticker}|${a.form}|${a.owner || ""}|${a.shares || 0}`;
}

function notifyNewDaily(items) {
  if (!state.notif || !("Notification" in window) || Notification.permission !== "granted") return;
  let seen = [];
  try { seen = JSON.parse(localStorage.getItem(STORAGE.seenDaily)) || []; } catch {}
  const seenSet = new Set(seen);
  const fresh = items.filter((a) => !seenSet.has(dailyId(a)));
  localStorage.setItem(STORAGE.seenDaily, JSON.stringify(items.map(dailyId).slice(0, 500)));
  if (seen.length === 0 || fresh.length === 0) return;
  const top = fresh[0];
  const verb = top.type === "stake" ? "filed a 5%+ stake in"
    : top.action === "BUY" ? "bought" : "sold";
  const who = top.owner || "An insider";
  notifyNewSignal({
    signal: `${who} ${verb} ${top.ticker}`,
    confidence: 0,
    timestamp: new Date().toISOString(),
  });
}

async function fetchDaily() {
  try {
    const res = await fetch(DAILY_URL + "?t=" + Date.now(), { cache: "no-store" });
    if (!res.ok) throw new Error("HTTP " + res.status);
    const data = await res.json();
    state.daily = Array.isArray(data.activity) ? data.activity : [];
    renderDaily();
    const gen = data.generated ? relativeTime(data.generated) : "";
    const m = data.meta || {};
    els.dailyMeta.textContent =
      `${m.insider || 0} insider · ${m.stake || 0} stakes · last ${data.lookback_days || 5}d · ${gen}`;
    notifyNewDaily(state.daily);
  } catch (err) {
    els.dailyList.innerHTML = `<li class="history-empty">Daily activity unavailable.</li>`;
  }
}

// ----- per-stock ownership summary -----

function passesStockFilter(s) {
  if (state.stockFilter === "buy") return s.net_value > 0;
  if (state.stockFilter === "sell") return s.net_value < 0;
  return true;
}

function moverRows(movers, cls) {
  if (!movers || movers.length === 0) return `<div class="mover empty">—</div>`;
  return movers.map((m) => `
    <div class="mover">
      <span class="mover-name">${m.name}</span>
      <span class="mover-tag ${cls}">${m.action}</span>
      <span class="mover-delta ${cls}">${shortShares(m.delta_shares)}</span>
    </div>`).join("");
}

function renderStocks() {
  const list = state.stocks.filter(passesStockFilter);
  if (list.length === 0) {
    els.stocksList.innerHTML = `<li class="history-empty">No matching stocks.</li>`;
    return;
  }
  els.stocksList.innerHTML = list.map((s) => {
    const net = Number(s.net_value) || 0;
    const dir = net > 0 ? "buy" : net < 0 ? "sell" : "";
    const sym = s.ticker || s.issuer || s.cusip;
    const total = (s.buyers || 0) + (s.sellers || 0) || 1;
    const buyPct = Math.round(((s.buyers || 0) / total) * 100);
    const open = state.expanded.has(s.cusip);
    return `<li class="stock-item ${open ? "open" : ""}" data-cusip="${s.cusip}">
      <div class="stock-head">
        <div class="stock-id">
          <span class="stock-ticker">${sym}</span>
          <span class="stock-issuer">${s.issuer || ""}</span>
        </div>
        <div class="stock-net ${dir}">${signedValue(net)}</div>
      </div>
      <div class="stock-bar"><div class="stock-bar-buy" style="width:${buyPct}%"></div></div>
      <div class="stock-stats">
        <span class="buy">▲ ${s.buyers || 0} buying${s.new ? ` · ${s.new} new` : ""}</span>
        <span class="sell">▼ ${s.sellers || 0} selling${s.exits ? ` · ${s.exits} exits` : ""}</span>
        <span class="muted">${s.holders || 0} holders</span>
      </div>
      <div class="stock-detail">
        <div class="mover-col">
          <div class="mover-title buy">Top buyers</div>
          ${moverRows(s.top_buyers, "buy")}
        </div>
        <div class="mover-col">
          <div class="mover-title sell">Top sellers</div>
          ${moverRows(s.top_sellers, "sell")}
        </div>
      </div>
    </li>`;
  }).join("");
}

// ----- notable individual moves -----

function passesFilter(e) {
  switch (state.flowFilter) {
    case "buy":  return BUY_ACTIONS.has(e.action);
    case "sell": return SELL_ACTIONS.has(e.action);
    default:     return true;
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
    const star = e.highlight ? `<span class="watch-star" title="Tracked institution">★</span>` : "";
    return `<li class="flow-item">
      <div class="flow-main">
        <span class="flow-action ${cls}">${e.action}</span>
        <span class="flow-symbol">${sym}${star}</span>
        <span class="flow-pct ${cls}">${signedValue(e.delta_value)}</span>
      </div>
      <div class="flow-sub">
        <span class="flow-inst">${e.institution}</span>
        <span class="flow-val">${shortShares(e.delta_shares)}</span>
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

function notifyNewFlows(events, quarter) {
  if (!state.notif || !("Notification" in window) || Notification.permission !== "granted") return;
  let seen = [];
  try { seen = JSON.parse(localStorage.getItem(STORAGE.seenEvents)) || []; } catch {}
  const seenSet = new Set(seen);
  const fresh = events.filter((e) => !seenSet.has(eventId(e, quarter)));
  const ids = events.map((e) => eventId(e, quarter));
  localStorage.setItem(STORAGE.seenEvents, JSON.stringify(ids.slice(0, 400)));

  // Only notify if we had a prior baseline (avoid alerting on first ever load).
  if (seen.length === 0 || fresh.length === 0) return;
  const top = fresh[0];
  const verb = BUY_ACTIONS.has(top.action) ? "loading up on" : "offloading";
  notifyNewSignal({
    signal: `${top.institution} ${verb} ${top.ticker || top.issuer}`,
    confidence: 0,
    timestamp: new Date().toISOString(),
  });
}

async function fetchFlows() {
  try {
    const res = await fetch(EVENTS_URL + "?t=" + Date.now(), { cache: "no-store" });
    if (!res.ok) throw new Error("HTTP " + res.status);
    const data = await res.json();
    state.events = Array.isArray(data.events) ? data.events : [];
    state.stocks = Array.isArray(data.stocks) ? data.stocks : [];

    renderStocks();
    renderFlows();
    renderMacro(data.fred);

    const q = data.quarters || {};
    els.quarterLabel.textContent = q.current ? `${q.previous || "?"} → ${q.current}` : "";
    const gen = data.generated ? relativeTime(data.generated) : "";
    els.stocksMeta.textContent = `${state.stocks.length} stocks with activity · updated ${gen}`;
    els.flowsMeta.textContent = `${state.events.length} notable moves`;

    notifyNewFlows(state.events, q.current || "");
  } catch (err) {
    els.stocksList.innerHTML = `<li class="history-empty">Summary unavailable.</li>`;
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

  els.stockFilter.addEventListener("click", (e) => {
    const btn = e.target.closest(".seg-btn");
    if (!btn) return;
    state.stockFilter = btn.dataset.filter;
    els.stockFilter.querySelectorAll(".seg-btn").forEach((b) => b.classList.toggle("active", b === btn));
    renderStocks();
  });

  els.dailyFilter.addEventListener("click", (e) => {
    const btn = e.target.closest(".seg-btn");
    if (!btn) return;
    state.dailyFilter = btn.dataset.filter;
    els.dailyFilter.querySelectorAll(".seg-btn").forEach((b) => b.classList.toggle("active", b === btn));
    renderDaily();
  });

  els.stocksList.addEventListener("click", (e) => {
    const item = e.target.closest(".stock-item");
    if (!item) return;
    const cusip = item.dataset.cusip;
    if (state.expanded.has(cusip)) state.expanded.delete(cusip);
    else state.expanded.add(cusip);
    item.classList.toggle("open");
  });

  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") { fetchSignal(); fetchDaily(); fetchFlows(); }
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
  fetchDaily();
  fetchFlows();
  startPolling();
  registerSW();
}

init();
