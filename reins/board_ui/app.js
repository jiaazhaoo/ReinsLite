"use strict";
/* Reins board: read-only views of /api/state. Views: overview, pipeline, runs, dev, cost, toolbox. No session details. */

const I18N = {
  zh: {
    overview: "概览", pipeline: "流程图谱", runs: "运行", dev: "开发", cost: "成本", toolbox: "工具箱",
    workspace: "工作区", governance: "治理", updated: "更新于", live: "实时", sub: "开发与运行的缰绳",
    ov_sub: "现在在跑什么、在改什么、花了多少", running_now: "运行中", developing: "开发中", spend_today: "今日花费",
    lowest_balance: "最低余额", paused: "暂停", need_action: "需要处理", last_release: "最近发布", of_daily: "日上限",
    flow: "流程", flow_sub: "每个阶段现在跑的代码版本", view_map: "查看图谱", runs_now: "正在运行", dev_now: "正在开发",
    recent_releases: "最近发布", all: "全部", nothing_running: "没有在跑的批次", nothing_dev: "没有进行中的开发",
    pipe_sub: "最新冻结的工作流，每个阶段的代码版本和它从工具箱里拿的东西", frozen: "冻结于", click_stage: "点阶段看详情",
    steps: "步骤", step_n: "第 {n} 步", code: "代码版本", no_module: "无代码模块（流水线自带）", batches_here: "在这一阶段",
    in_dev: "开发中", paid_stage: "付费阶段", paid_note: "先批花费上限", tools: "工具", stale: "生产已是 {v}，工作流未重新冻结",
    used_by: "也用于", newer: "有新版本", runs_sub: "正在跑的批次和跑完的批次", history: "运行记录", history_sub: "跑完的批次",
    cases: "案件", skipped: "不进流程", progress: "进度", eta: "预计剩余", spend: "花费", time: "用时", release: "版本",
    probe: "进度详情", mixed: "中途换过版本", adopted: "账本未接入（旧版本启动）", issue_todo: "待修", issue_fixing: "修复中",
    issue_fixed: "已修复待确认", ended: "结束", batch: "批次", type: "类型", result: "结果", acceptance: "验收",
    not_accepted: "未验收", stages_done: "{a}/{b} 阶段", done: "完成", failed: "失败", dev_sub: "正在改的和已进生产的版本",
    releases: "发布记录", releases_sub: "已进生产的版本和它们做了什么", when: "时间", module: "模块", version: "版本",
    what: "这个版本做了什么", gate: "门禁", candidate: "候选版本", unregistered: "还没登记版本", files_changed: "改了 {n} 个文件",
    from_batch: "起因", fixes_issue: "修问题单", last_edit: "最后改动", stalled: "停滞 {h} h", no_gate: "还没过门禁",
    cost_sub: "预算池、各服务商余额与花费", pool: "预算池", pool_sub: "所有会话合计，经网关", hour: "近 1 小时", today: "今日",
    week: "本周", open_caps: "未关闭批次的上限合计", batch_cap: "单批上限", session_cap: "单会话每日", providers: "服务商",
    balance: "余额", calls: "今日调用", no_key: "未配置 key", no_balance_api: "无余额接口", query_failed: "查询失败",
    by_balance: "按余额变化计", per_call: "按调用数 × 牌价估算", ledger_only: "仅网关记账", by_batch: "本周按批次",
    tb_sub: "工作流用到的每一件东西，都有版本", search: "搜索工具…", shelf: "架子", tool: "工具", does: "做什么",
    detail: "细节", stages: "阶段", unreg_run: "未经 reins 启动的运行", unreg_note: "看不到进度、花费和案件账本",
    theme: "主题", ok: "正常", attention: "注意", action: "需要处理", none: "无",
    s_trained: "自训模型", s_pretrained: "开源模型", s_paid: "付费模型", s_prompt: "提示词", s_code: "代码工具", s_api: "外部接口", s_data: "数据",
    st_running: "运行中", st_paused: "暂停", st_open: "未开始", st_closed: "已关闭", st_done: "完成", st_failed: "失败", st_planned: "未开始", st_skipped: "跳过",
    t_production: "生产", t_rework: "返工", t_experiment: "实验", t_pilot: "试跑", t_eval: "评测", t_smoke: "冒烟", t_drift: "漂移", t_benchmark_build: "建基准",
    d_ship: "交付", d_ship_with_note: "带说明交付", d_rework: "返工",
  },
  en: {
    overview: "Overview", pipeline: "Pipeline", runs: "Runs", dev: "Development", cost: "Cost", toolbox: "Toolbox",
    workspace: "Workspace", governance: "Governance", updated: "Updated", live: "Live", sub: "reins for dev and runs",
    ov_sub: "What runs, what changes, what it costs", running_now: "Running", developing: "In development", spend_today: "Spent today",
    lowest_balance: "Lowest balance", paused: "paused", need_action: "need action", last_release: "Last release", of_daily: "daily cap",
    flow: "Pipeline", flow_sub: "The code version each stage runs now", view_map: "Open map", runs_now: "Running now", dev_now: "In development",
    recent_releases: "Recent releases", all: "All", nothing_running: "No batch is running", nothing_dev: "Nothing in development",
    pipe_sub: "The newest frozen workflow: each stage's code and the versioned tools it uses", frozen: "Frozen", click_stage: "Click a stage for details",
    steps: "Steps", step_n: "step {n}", code: "Code", no_module: "No module (part of the pipeline)", batches_here: "In this stage",
    in_dev: "In development", paid_stage: "Paid stage", paid_note: "needs an approved cap", tools: "Tools", stale: "Production is {v}; workflow not refrozen",
    used_by: "Also used by", newer: "newer version", runs_sub: "Running and finished batches", history: "History", history_sub: "Finished batches",
    cases: "Cases", skipped: "skipped", progress: "Progress", eta: "ETA", spend: "Spend", time: "Time", release: "Release",
    probe: "Progress detail", mixed: "version changed mid-run", adopted: "ledger not wired (started before reins)", issue_todo: "open", issue_fixing: "fixing",
    issue_fixed: "fixed, to verify", ended: "Ended", batch: "Batch", type: "Type", result: "Result", acceptance: "Acceptance",
    not_accepted: "not accepted", stages_done: "{a}/{b} stages", done: "done", failed: "failed", dev_sub: "Changes in progress and versions in production",
    releases: "Releases", releases_sub: "Versions in production and what each did", when: "When", module: "Module", version: "Version",
    what: "What it does", gate: "Gate", candidate: "Candidate", unregistered: "not registered yet", files_changed: "{n} files changed",
    from_batch: "From", fixes_issue: "Fixes issue", last_edit: "Last edit", stalled: "stalled {h} h", no_gate: "not gated yet",
    cost_sub: "Budget pool, provider balances and spend", pool: "Budget pool", pool_sub: "all sessions, through the gateway", hour: "Last hour", today: "Today",
    week: "This week", open_caps: "Caps of open batches", batch_cap: "Per batch", session_cap: "Per session per day", providers: "Providers",
    balance: "Balance", calls: "Calls today", no_key: "no key", no_balance_api: "no balance API", query_failed: "query failed",
    by_balance: "from balance changes", per_call: "calls × list price", ledger_only: "gateway ledger only", by_batch: "This week by batch",
    tb_sub: "Everything the workflow uses, each with a version", search: "Search tools…", shelf: "Shelf", tool: "Tool", does: "Does",
    detail: "Detail", stages: "Stages", unreg_run: "Run not started through reins", unreg_note: "no progress, spend or case ledger",
    theme: "Theme", ok: "OK", attention: "Attention", action: "Needs action", none: "none",
    s_trained: "Trained", s_pretrained: "Pretrained", s_paid: "Paid models", s_prompt: "Prompts", s_code: "Code", s_api: "APIs", s_data: "Data",
    st_running: "running", st_paused: "paused", st_open: "open", st_closed: "closed", st_done: "done", st_failed: "failed", st_planned: "planned", st_skipped: "skipped",
    t_production: "production", t_rework: "rework", t_experiment: "experiment", t_pilot: "pilot", t_eval: "eval", t_smoke: "smoke", t_drift: "drift", t_benchmark_build: "benchmark build",
    d_ship: "ship", d_ship_with_note: "ship with note", d_rework: "rework",
  },
};
const store = { get(k, d) { try { return localStorage.getItem(k) ?? d; } catch (e) { return d; } }, set(k, v) { try { localStorage.setItem(k, v); } catch (e) {} } };
let LANG = store.get("reins.lang", "zh");
let S = null;
let TB = { shelf: "all", q: "" };
const t = (k, vars) => { let s = (I18N[LANG] && I18N[LANG][k]) ?? I18N.zh[k] ?? k; if (vars) for (const [a, b] of Object.entries(vars)) s = s.replace(`{${a}}`, b); return s; };
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const usd = (v) => (v == null ? "—" : "$" + Number(v).toFixed(2));
const when = (ts) => (ts ? esc(ts.slice(5, 16).replace("T", " ")) : "—");
const pct = (a, b) => (b ? Math.max(0, Math.min(100, Math.round((100 * a) / b))) : 0);
const SHELVES = ["trained", "pretrained", "paid", "prompt", "code", "api", "data"];

const ICON = {
  overview: '<path d="M3 10.5 12 3l9 7.5"/><path d="M5 9.5V20h14V9.5"/>',
  pipeline: '<circle cx="5" cy="12" r="2.2"/><circle cx="12" cy="12" r="2.2"/><circle cx="19" cy="12" r="2.2"/><path d="M7.2 12h2.6M14.2 12h2.6"/>',
  runs: '<path d="M7 5v14l11-7z"/>',
  dev: '<path d="m8 8-4 4 4 4M16 8l4 4-4 4M13.5 5l-3 14"/>',
  cost: '<circle cx="12" cy="12" r="8.5"/><path d="M14.8 9.2c-.5-.9-1.6-1.4-2.8-1.4-1.6 0-2.8.8-2.8 2.1 0 2.9 5.8 1.4 5.8 4.3 0 1.3-1.2 2.1-3 2.1-1.3 0-2.4-.5-3-1.5M12 6v1.8M12 16.2V18"/>',
  toolbox: '<rect x="3" y="7.5" width="18" height="12" rx="2"/><path d="M9 7.5V5.5a1.5 1.5 0 0 1 1.5-1.5h3A1.5 1.5 0 0 1 15 5.5v2M3 12.5h18"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2.5v2M12 19.5v2M4.6 4.6 6 6M18 18l1.4 1.4M2.5 12h2M19.5 12h2M4.6 19.4 6 18M18 6l1.4-1.4"/>',
  moon: '<path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z"/>',
  alert: '<path d="M12 3.5 2.8 19.5h18.4z"/><path d="M12 10v4.5M12 17.2v.3"/>',
  x: '<path d="M6 6l12 12M18 6 6 18"/>',
  globe: '<circle cx="12" cy="12" r="8.5"/><path d="M3.5 12h17M12 3.5c2.5 2.6 2.5 14.4 0 17M12 3.5c-2.5 2.6-2.5 14.4 0 17"/>',
};
const icon = (n) => `<svg viewBox="0 0 24 24" aria-hidden="true">${ICON[n] || ""}</svg>`;

/* ---------- pixel crabs: one body, a hat and a tool per stage ---------- */
const BODY = "#D97757", EYE = "#1d1d1f";
const HATS = {
  cap: [[6, 2, 8, 2, "#3b6fd8"], [13, 3, 4, 1, "#3b6fd8"]], beret: [[7, 2, 7, 2, "#7a4fc4"], [10, 1, 1, 1, "#7a4fc4"]],
  explorer: [[7, 1, 6, 2, "#b59a5a"], [5, 3, 10, 1, "#9c8247"], [7, 2, 6, .5, "#6b5a2e"]],
  headband: [[6, 4, 8, 1, "#d23b3b"], [14, 3.5, 2, 1, "#d23b3b"], [15, 4.5, 1.5, 1, "#d23b3b"]],
  mortar: [[5, 2, 10, 1, "#26262a"], [8, 3, 4, 1, "#26262a"], [14.5, 2.5, .6, 2.5, "#e0b030"]], helmet: [[6, 2, 8, 2, "#f2c230"], [9, 1.5, 2, .6, "#f2c230"]],
};
const ITEMS = {
  clipboard: [[16, 5, 3.4, 5, "#f3efe4"], [17.2, 4.4, 1, 1, "#777"], [16.5, 6.5, 2.4, .5, "#999"], [16.5, 8, 2.4, .5, "#999"]],
  scissors: [[1, 3, .8, 4, "#9aa0a6"], [2.6, 3, .8, 4, "#9aa0a6"], [.4, 7, 1.6, 1.6, "#d23b3b"], [2.2, 7, 1.6, 1.6, "#d23b3b"]],
  map: [[16, 4.5, 4, 4.5, "#86c58a"], [16.6, 6, 2.8, .6, "#3b6fd8"], [18.2, 5, .6, 3.5, "#3b6fd8"], [17, 4.7, .9, .9, "#d23b3b"]],
  pencil: [[16.2, 2.5, 1, 6, "#f2c230"], [16.2, 8.5, 1, 1, "#d23b3b"], [1, 13.2, 18, .6, "#d23b3b"]],
  flag: [[1.8, 3, .6, 7, "#777"], [2.4, 3, 3, 2, "#2e9e5b"]], wrench: [[16.3, 4, 1, 5, "#9aa0a6"], [15.6, 3, 2.4, 1.3, "#9aa0a6"]],
};
const GLASSES = [[7, 5.4, 3, 3.2, "#26262a"], [7.5, 5.9, 2, 2.2, "#cfe8ff"], [10, 5.4, 3, 3.2, "#26262a"], [10.5, 5.9, 2, 2.2, "#cfe8ff"], [9.6, 6.4, .8, .5, "#26262a"]];
const MASCOT = { box: ["cap", "clipboard"], scissors: ["beret", "scissors"], compass: ["explorer", "map"], pencil: ["headband", "pencil"],
  judge: ["mortar", "flag", true], wrench: ["helmet", "wrench"] };
function crab(kind, cls = "crab") {
  const [h, it, glasses] = MASCOT[kind] || MASCOT.wrench;
  const r = (a) => a.map(([x, y, w, hh, c]) => `<rect x="${x}" y="${y}" width="${w}" height="${hh}" fill="${c}"/>`).join("");
  const body = [[6, 4, 8, 6, BODY], [4, 6, 2, 2, BODY], [14, 6, 2, 2, BODY], [6, 10, 1, 2, BODY], [8, 10, 1, 2, BODY], [11, 10, 1, 2, BODY], [13, 10, 1, 2, BODY]];
  const eyes = [[8, 6, 1, 2, EYE], [11, 6, 1, 2, EYE]];
  return `<svg class="${cls}" viewBox="0 0 20 15" aria-hidden="true">${r(body)}${glasses ? r(GLASSES) : ""}${r(eyes)}${r(HATS[h] || [])}${r(ITEMS[it] || [])}</svg>`;
}

/* ---------- small renderers ---------- */
const badge = (text, cls = "", dot = false) => `<span class="badge ${cls}">${dot ? `<span class="dot ${dot}"></span>` : ""}${esc(text)}</span>`;
const STATUS_CLS = { running: "green", paused: "amber", failed: "red", done: "blue", closed: "", open: "", planned: "", skipped: "" };
const statusBadge = (s) => badge(t("st_" + s), STATUS_CLS[s] || "", s === "running" ? "green" : s === "paused" ? "amber" : s === "failed" ? "red" : false);
const typeBadge = (ty) => badge(t("t_" + ty), "outline");
const HEALTH = { green: ["ok", "green"], yellow: ["attention", "amber"], red: ["action", "red"] };
const sectionHead = (title, sub, more) => `<div class="section-head"><h2>${esc(title)}</h2>${sub ? `<span class="sub">${esc(sub)}</span>` : ""}${more || ""}</div>`;
const meterCls = (p) => (p >= 90 ? "red" : p >= 70 ? "amber" : "");

function shelfCounts(tools) {
  const c = {};
  tools.forEach((x) => (c[x.group] = (c[x.group] || 0) + 1));
  return SHELVES.filter((s) => c[s]).map((s) => `<span class="shelf-n s-${s}" title="${esc(t("s_" + s))}">${esc(t("s_" + s))} ${c[s]}</span>`).join("");
}

function stageCard(st, i, pi) {
  const status = [];
  st.batches.forEach((b) => { const c = b.status === "running" ? "green" : b.status === "paused" ? "amber" : "";
    status.push(badge(`${b.title} · ${b.status === "running" ? b.pct + "%" : t("st_" + b.status)}`, c, c || false)); });
  st.developing.forEach(() => status.push(badge(t("in_dev"), "amber", "amber")));
  if (st.stale) status.push(badge("!", "red"));
  if (st.paid) status.push(badge(t("paid_stage"), "outline"));
  const ver = st.code.length ? st.code[0].version : null;
  return `<button class="stage ${st.batches.some((b) => b.status === "running") ? "busy" : ""}" data-stage="${pi}:${i}">
    <div class="top">${crab(st.mascot)}<span class="idx">${String(i + 1).padStart(2, "0")}</span></div>
    <div><h3>${esc(st.title)}</h3><div class="key">${esc(st.name)}${st.steps ? " · " + esc(t("step_n", { n: st.steps })) : ""}</div></div>
    <div class="about">${esc(st.module_about || "")}</div>
    ${ver ? `<span class="chip" title="${esc(st.code[0].about)}">${esc(ver)}${st.code.length > 1 ? ` +${st.code.length - 1}` : ""}</span>` : `<span class="faint" style="font-size:12px">${esc(t("no_module"))}</span>`}
    <div class="status">${status.join("")}</div>
    <div class="shelves">${shelfCounts(st.tools)}</div>
  </button>`;
}
const flowHtml = (p, pi, compact) => `<div class="flow ${compact ? "compact" : ""}">${p.stages.map((st, i) => (i ? '<div class="link"></div>' : "") + stageCard(st, i, pi)).join("")}</div>`;

function stepper(r) {
  return `<div class="stepper">${r.stages.map((s) => {
    const p = s.status === "done" || s.status === "skipped" ? 100 : pct(s.done, r.n_cases);
    return `<div class="step ${s.status}"><div class="bar"><i style="width:${p}%"></i></div>
      <div class="name"><span>${esc(s.stage)}</span><span>${s.status === "running" ? p + "%" : s.status === "done" ? "✓" : ""}</span></div></div>`;
  }).join("")}</div>`;
}

function runNotes(r) {
  const out = [];
  if (r.status_reason) out.push(`<div class="note warn">⏸ ${esc(r.status_reason)}</div>`);
  r.needs.forEach((n) => out.push(`<div class="note bad">⚠ ${esc(n)}</div>`));
  r.issues.forEach((i) => out.push(`<div class="note ${i.status === "fixed" ? "ok" : "bad"}">#${i.id} ${esc(t(i.status === "fixed" ? "issue_fixed" : i.status === "in_progress" ? "issue_fixing" : "issue_todo"))} · ${i.n} ${esc(t("cases"))} · ${esc(i.symptom)}${i.version ? " → " + esc(i.version) : ""}</div>`));
  if (r.mixed) out.push(`<div class="note warn">${esc(t("mixed"))}</div>`);
  if (r.adopted) out.push(`<div class="note warn">${esc(t("adopted"))}</div>`);
  return out.length ? `<div class="notes">${out.join("")}</div>` : "";
}

function runCard(r) {
  const [hk, hc] = HEALTH[r.health] || HEALTH.green;
  return `<div class="card run">
    <div class="run-head"><div style="min-width:0"><h3>${esc(r.title)}</h3><div class="id">${esc(r.batch_id)}</div></div>
      <div class="right">${statusBadge(r.status)}${typeBadge(r.type)}${badge(t(hk), hc)}</div></div>
    <div class="purpose">${esc(r.purpose)}</div>
    ${stepper(r)}
    <div class="stats">
      <div class="stat"><div class="k">${esc(t("cases"))}</div><div class="v">${r.n_cases}${r.n_skipped ? ` <small>· ${r.n_skipped} ${esc(t("skipped"))}</small>` : ""}</div></div>
      <div class="stat"><div class="k">${esc(t("progress"))} · ${esc(r.stage)}</div><div class="v">${r.adopted ? "—" : esc(r.progress)} <small>${esc(r.stage_ord)}</small></div></div>
      <div class="stat"><div class="k">${esc(t("eta"))}</div><div class="v">${esc(r.eta)}</div></div>
      <div class="stat"><div class="k">${esc(t("spend"))}</div><div class="v">${usd(r.spent)} <small>/ ${usd(r.cap)}</small></div></div>
      <div class="stat"><div class="k">${esc(t("time"))}</div><div class="v">${r.time_used_h} h${r.time_budget_h ? ` <small>/ ${r.time_budget_h} h</small>` : ""}</div></div>
    </div>
    ${runNotes(r)}
    ${r.probe.length ? `<details class="probe"><summary>${esc(t("probe"))}</summary><pre>${esc(r.probe.join("\n"))}</pre></details>` : ""}
  </div>`;
}

function alertsHtml() {
  const items = S.notifications.map((n) => `<div class="alert ${n.level}">${icon("alert")}<div style="min-width:0"><b>${esc(n.title)}</b><div class="body">${esc(n.body)}</div></div><span class="when">${when(n.at)}</span></div>`);
  S.unregistered_runs.forEach((u) => items.push(`<div class="alert warn">${icon("alert")}<div><b>${esc(t("unreg_run"))} · ${esc(u.kind)}</b><div class="body">${esc(u.what)} — ${esc(t("unreg_note"))}</div></div><span class="when">${when(u.at)}</span></div>`));
  return items.length ? `<div class="alerts">${items.join("")}</div>` : "";
}

/* ---------- views ---------- */
const VIEWS = {
  overview() {
    const running = S.running.filter((r) => r.status === "running").length;
    const paused = S.running.filter((r) => r.status === "paused").length;
    const act = S.running.filter((r) => r.health === "red").length;
    const P = S.pool, L = P.limits;
    const bals = S.cost.filter((c) => c.has_endpoint && c.balance != null).sort((a, b) => a.balance - b.balance);
    const low = bals[0];
    const lastRel = S.dev_history.find((v) => v.status === "released");
    const kpis = `<div class="grid g4">
      <div class="card kpi"><div class="label"><span class="dot ${running ? "pulse" : ""}" style="${running ? "background:var(--green)" : ""}"></span>${esc(t("running_now"))}</div>
        <div class="value">${running}<small>/ ${S.running.length}</small></div><div class="foot">${paused} ${esc(t("paused"))} · ${act} ${esc(t("need_action"))}</div></div>
      <div class="card kpi"><div class="label"><span class="dot amber"></span>${esc(t("developing"))}</div><div class="value">${S.in_progress.length}</div>
        <div class="foot">${esc(t("last_release"))} ${lastRel ? when(lastRel.released_at) : "—"}</div></div>
      <div class="card kpi"><div class="label"><span class="dot accent"></span>${esc(t("spend_today"))}</div><div class="value">${usd(P.today)}<small>/ ${usd(L.daily_cap)}</small></div>
        <div class="meter"><i class="${meterCls(pct(P.today, L.daily_cap))}" style="width:${pct(P.today, L.daily_cap)}%"></i></div></div>
      <div class="card kpi"><div class="label"><span class="dot blue"></span>${esc(t("lowest_balance"))}</div><div class="value">${low ? usd(low.balance) : "—"}</div>
        <div class="foot">${low ? esc(low.provider) : ""}</div></div></div>`;
    const flows = S.pipelines.map((p, pi) => `<div class="section">${sectionHead(t("flow") + (S.pipelines.length > 1 ? " · " + p.project : ""), t("flow_sub"), `<a class="more" href="#/pipeline">${esc(t("view_map"))} →</a>`)}${flowHtml(p, pi, true)}</div>`).join("");
    const runs = S.running.length ? `<div class="card mini">${S.running.map((r) => `<a class="mini-row" href="#/runs">
        <span class="t">${esc(r.title)}</span><span>${statusBadge(r.status)}</span>
        <span class="s">${esc(t("t_" + r.type))} · ${esc(r.stage)} ${esc(r.stage_ord)} · ${r.adopted ? esc(t("adopted")) : esc(r.progress)} · ${usd(r.spent)} / ${usd(r.cap)}</span>
        <div class="meter"><i style="width:${r.pct}%"></i></div></a>`).join("")}</div>` : `<div class="card empty">${esc(t("nothing_running"))}</div>`;
    const devs = S.in_progress.length ? `<div class="card mini">${S.in_progress.map((d) => `<a class="mini-row" href="#/dev"><span class="t">${esc(d.module)}</span>
        <span>${badge(d.gate_status === "green" ? t("gate") + " ✓" : d.registered ? t("candidate") : t("unregistered"), d.gate_status === "green" ? "green" : "amber")}</span>
        <span class="s">${esc(d.about || "")}</span></a>`).join("")}</div>` : `<div class="card empty">${esc(t("nothing_dev"))}</div>`;
    const rel = `<div class="card mini">${S.dev_history.slice(0, 5).map((v) => `<a class="mini-row" href="#/dev"><span class="t">${esc(v.module)} <span class="faint mono">${esc(v.version)}</span></span>
        <span class="faint nowrap" style="font-size:12px">${when(v.released_at)}</span><span class="s">${esc(v.about)}</span></a>`).join("")}</div>`;
    return `<div class="page-head"><div><h1>${esc(t("overview"))}</h1><p>${esc(t("ov_sub"))}</p></div></div>
      ${alertsHtml()}${kpis}${flows}
      <div class="section cols"><div>${sectionHead(t("runs_now"), "", `<a class="more" href="#/runs">${esc(t("all"))} →</a>`)}${runs}</div>
        <div>${sectionHead(t("dev_now"), "", `<a class="more" href="#/dev">${esc(t("all"))} →</a>`)}${devs}
          <div class="section" style="margin-top:22px">${sectionHead(t("recent_releases"))}${rel}</div></div></div>`;
  },
  pipeline() {
    const legend = SHELVES.map((s) => `<span class="shelf-n s-${s}">${esc(t("s_" + s))}</span>`).join("");
    return `<div class="page-head"><div><h1>${esc(t("pipeline"))}</h1><p>${esc(t("pipe_sub"))}</p></div></div>
      ${S.pipelines.map((p, pi) => `<div class="section" style="margin-top:${pi ? 34 : 0}px">
        <div class="section-head"><h2>${esc(p.project || "")}</h2><span class="sub">${esc(p.about || "")}</span>
          <span class="more" style="color:var(--muted)"><span class="chip">${esc(p.workflow)}</span> · ${esc(t("frozen"))} ${when(p.frozen)}</span></div>
        ${flowHtml(p, pi, false)}
        <div class="filters" style="margin-top:12px"><span class="faint" style="font-size:12.5px">${esc(t("click_stage"))}</span><span style="margin-left:auto;display:flex;gap:5px;flex-wrap:wrap">${legend}</span></div>
      </div>`).join("") || `<div class="card empty">workflow freeze NAME</div>`}`;
  },
  runs() {
    const hist = S.run_history.length ? `<div class="card table-wrap"><table><thead><tr><th>${esc(t("ended"))}</th><th>${esc(t("batch"))}</th><th>${esc(t("type"))}</th>
        <th class="num">${esc(t("cases"))}</th><th>${esc(t("result"))}</th><th class="num">${esc(t("spend"))}</th><th class="num">${esc(t("time"))}</th><th>${esc(t("acceptance"))}</th></tr></thead><tbody>
      ${S.run_history.map((h) => `<tr><td class="nowrap">${when(h.closed)}</td><td><b>${esc(h.title)}</b><div class="sub mono">${esc(h.batch_id)}</div></td><td>${typeBadge(h.type)}</td>
        <td class="num">${h.n_cases}</td><td>${esc(t("stages_done", { a: h.stages_done, b: h.stages }))}${h.done != null ? `<div class="sub">${esc(t("done"))} ${h.done}${h.skipped ? ` · ${esc(t("skipped"))} ${h.skipped}` : ""}${h.failed ? ` · <span style="color:var(--red)">${esc(t("failed"))} ${h.failed}</span>` : ""}</div>` : ""}</td>
        <td class="num">${usd(h.spent)}</td><td class="num">${h.hours != null ? h.hours + " h" : "—"}</td><td>${h.decision ? badge(t("d_" + h.decision), "green") : `<span class="faint">${esc(t("not_accepted"))}</span>`}</td></tr>`).join("")}
      </tbody></table></div>` : `<div class="card empty">—</div>`;
    return `<div class="page-head"><div><h1>${esc(t("runs"))}</h1><p>${esc(t("runs_sub"))}</p></div></div>${alertsHtml()}
      ${S.running.length ? S.running.map(runCard).join("") : `<div class="card empty">${esc(t("nothing_running"))}</div>`}
      <div class="section">${sectionHead(t("history"), t("history_sub"))}${hist}</div>`;
  },
  dev() {
    const cards = S.in_progress.length ? `<div class="grid g2">${S.in_progress.map((p) => `<div class="card pad">
        <div class="run-head"><div><h3 style="margin:0;font-size:15px">${esc(p.module)}</h3><div class="id mono">${esc(p.version || t("unregistered"))}</div></div>
          <div class="right">${badge(p.gate_status === "green" ? t("gate") + " ✓" : p.gate_status === "red" ? t("gate") + " ✗" : t("no_gate"), p.gate_status === "green" ? "green" : p.gate_status === "red" ? "red" : "amber")}${p.stalled ? badge(t("stalled", { h: p.idle_h }), "red") : ""}</div></div>
        <div style="margin:10px 0 6px">${esc(p.about || "")}</div>${p.module_about ? `<div class="muted" style="font-size:12.5px">${esc(p.module_about)}</div>` : ""}
        ${p.conflicts.map((c) => `<div class="note bad" style="margin-top:8px">⚠ ${esc(c)}</div>`).join("")}
        <div class="muted" style="font-size:12.5px;margin-top:12px;display:flex;gap:14px;flex-wrap:wrap"><span>${esc(t("files_changed", { n: p.files }))}</span><span>${esc(p.gate)}</span>
          ${p.issue ? `<span>${esc(t("fixes_issue"))} #${p.issue}</span>` : ""}${p.from_batch ? `<span>${esc(t("from_batch"))} ${esc(p.from_batch)}</span>` : ""}<span>${esc(t("last_edit"))} ${when(p.last)}</span></div></div>`).join("")}</div>`
      : `<div class="card empty">${esc(t("nothing_dev"))}</div>`;
    const rel = `<div class="card table-wrap"><table><thead><tr><th>${esc(t("when"))}</th><th>${esc(t("module"))}</th><th>${esc(t("version"))}</th><th>${esc(t("what"))}</th><th>${esc(t("gate"))}</th></tr></thead><tbody>
      ${S.dev_history.map((v) => `<tr><td class="nowrap">${when(v.released_at)}</td><td><b>${esc(v.module)}</b></td><td><span class="chip">${esc(v.version)}</span>${v.status !== "released" ? " " + badge(v.status, "red") : ""}</td>
        <td>${esc(v.about)}${v.from_batch ? `<div class="sub">${esc(t("from_batch"))} ${esc(v.from_batch)}</div>` : ""}${v.why_out ? `<div class="sub">${esc(v.why_out)}</div>` : ""}</td><td class="muted" style="font-size:12.5px">${esc(v.gate)}</td></tr>`).join("")}
      </tbody></table></div>`;
    return `<div class="page-head"><div><h1>${esc(t("dev"))}</h1><p>${esc(t("dev_sub"))}</p></div></div>
      ${sectionHead(t("dev_now"))}${cards}<div class="section">${sectionHead(t("releases"), t("releases_sub"))}${rel}</div>`;
  },
  cost() {
    const P = S.pool, L = P.limits;
    const row = (label, v, cap) => `<div class="pool-row"><span class="muted">${esc(label)}</span><div class="meter"><i class="${meterCls(pct(v, cap))}" style="width:${pct(v, cap)}%"></i></div><span class="num"><b>${usd(v)}</b> <span class="faint">/ ${usd(cap)}</span></span></div>`;
    const pool = `<div class="card pad">${row(t("hour"), P.hour, L.hourly_cap)}${row(t("today"), P.today, L.daily_cap)}${row(t("week"), P.week, L.weekly_cap)}
      <div class="muted" style="font-size:12.5px;padding-top:12px;border-top:1px solid var(--border);display:flex;gap:18px;flex-wrap:wrap">
      <span>${esc(t("open_caps"))} <b style="color:var(--text)">${usd(P.open_caps)}</b></span><span>${esc(t("batch_cap"))} ≤ ${usd(L.max_batch_cap)}</span><span>${esc(t("session_cap"))} ≤ ${usd(L.per_session_daily_cap)}</span></div></div>`;
    const prov = `<div class="grid g3">${S.cost.map((c) => `<div class="card pad provider"><div class="name">${esc(c.provider)}
        ${!c.key_present ? badge(t("no_key"), "red") : !c.has_endpoint ? badge(t("no_balance_api")) : c.error ? badge(t("query_failed"), "red") : badge(t("live"), "green", "green")}</div>
        <div class="bal ${c.balance != null && c.balance < 10 ? "low" : ""}">${c.has_endpoint ? usd(c.balance) : "—"}</div>
        <div class="faint" style="font-size:12px">${esc(c.spend_source === "balance" ? t("by_balance") : c.per_call ? t("per_call") : t("ledger_only"))}${c.balance_at ? " · " + when(c.balance_at) : ""}</div>
        <div class="rows"><div>${esc(t("today"))}<b>${usd(c.spent_today)}</b></div><div>${esc(t("week"))}<b>${usd(c.spent_week)}</b></div><div>${esc(t("calls"))}<b>${c.calls_today}</b></div></div></div>`).join("")}</div>`;
    const byb = {};
    S.cost.forEach((c) => c.by_batch.forEach((b) => { byb[b.batch_id] = byb[b.batch_id] || {}; byb[b.batch_id][c.provider] = b.usd; }));
    const provs = S.cost.map((c) => c.provider);
    const rows = Object.entries(byb).sort((a, b) => Object.values(b[1]).reduce((x, y) => x + y, 0) - Object.values(a[1]).reduce((x, y) => x + y, 0));
    const table = rows.length ? `<div class="card table-wrap"><table><thead><tr><th>${esc(t("batch"))}</th>${provs.map((p) => `<th class="num">${esc(p)}</th>`).join("")}<th class="num">Σ</th></tr></thead><tbody>
      ${rows.map(([b, v]) => `<tr><td class="mono">${esc(b)}</td>${provs.map((p) => `<td class="num">${v[p] != null ? usd(v[p]) : '<span class="faint">—</span>'}</td>`).join("")}<td class="num"><b>${usd(Object.values(v).reduce((x, y) => x + y, 0))}</b></td></tr>`).join("")}
      </tbody></table></div>` : `<div class="card empty">—</div>`;
    return `<div class="page-head"><div><h1>${esc(t("cost"))}</h1><p>${esc(t("cost_sub"))}</p></div></div>
      ${sectionHead(t("pool"), t("pool_sub"))}${pool}<div class="section">${sectionHead(t("providers"))}${prov}</div>
      <div class="section">${sectionHead(t("by_batch"))}${table}</div>`;
  },
  toolbox() {
    const all = [];
    S.pipelines.forEach((p) => p.toolbox.forEach((x) => all.push({ ...x, project: p.project })));
    const counts = {};
    all.forEach((x) => (counts[x.group] = (counts[x.group] || 0) + 1));
    const q = TB.q.toLowerCase();
    const shown = all.filter((x) => (TB.shelf === "all" || x.group === TB.shelf) &&
      (!q || [x.title, x.base, x.about, x.detail, x.name].join(" ").toLowerCase().includes(q)));
    const chips = [`<button class="fchip ${TB.shelf === "all" ? "on" : ""}" data-shelf="all">${esc(t("all"))} ${all.length}</button>`]
      .concat(SHELVES.filter((s) => counts[s]).map((s) => `<button class="fchip ${TB.shelf === s ? "on" : ""}" data-shelf="${s}">${esc(t("s_" + s))} ${counts[s]}</button>`)).join("");
    return `<div class="page-head"><div><h1>${esc(t("toolbox"))}</h1><p>${esc(t("tb_sub"))}</p></div></div>
      <div class="filters">${chips}<input class="search" id="tbq" placeholder="${esc(t("search"))}" value="${esc(TB.q)}"></div>
      <div class="card table-wrap"><table><thead><tr><th>${esc(t("shelf"))}</th><th>${esc(t("tool"))}</th><th>${esc(t("version"))}</th><th>${esc(t("does"))}</th><th>${esc(t("detail"))}</th><th>${esc(t("stages"))}</th></tr></thead><tbody>
      ${shown.map((x) => `<tr><td><span class="shelf-n s-${x.group}">${esc(t("s_" + x.group))}</span></td><td><b>${esc(x.title)}</b><div class="sub mono">${esc(x.base)}</div></td>
        <td class="nowrap"><span class="chip">v${x.version}</span>${x.newer ? " " + badge(t("newer"), "amber") : ""}</td><td>${esc(x.about)}</td><td class="muted mono" style="font-size:12px">${esc(x.detail)}</td>
        <td class="muted">${esc((x.used_by || []).join(" · "))}</td></tr>`).join("") || `<tr><td colspan="6" class="empty">—</td></tr>`}
      </tbody></table></div>`;
  },
};

/* ---------- drawer: one stage in full ---------- */
function openStage(pi, si) {
  const p = S.pipelines[pi], st = p && p.stages[si];
  if (!st) return;
  const groups = {};
  st.tools.forEach((x) => (groups[x.group] = groups[x.group] || []).push(x));
  const code = st.code.length ? st.code.map((c) => `<div style="margin-bottom:8px"><span class="chip">${esc(c.version)}</span><div class="muted" style="font-size:12.5px;margin-top:3px">${esc(c.about)}</div>
      ${c.stale ? `<div class="note bad" style="font-size:12.5px">${esc(t("stale", { v: c.now }))}</div>` : ""}</div>`).join("") : `<span class="faint">${esc(t("no_module"))}</span>`;
  document.getElementById("drawer").innerHTML = `<header>${crab(st.mascot)}<div><h2>${esc(st.title)}</h2><div class="key mono faint">${esc(st.name)}${st.steps ? " · " + esc(t("step_n", { n: st.steps })) : ""}</div></div>
      <button class="iconbtn x" id="dclose" aria-label="close">${icon("x")}</button></header>
    <div class="content">
      ${st.module_about ? `<p style="margin:0 0 16px">${esc(st.module_about)}</p>` : ""}
      <dl class="kv"><dt>${esc(t("code"))}</dt><dd>${code}</dd>
        <dt>${esc(t("batches_here"))}</dt><dd>${st.batches.length ? st.batches.map((b) => `<div>${badge(t("st_" + b.status), STATUS_CLS[b.status], b.status === "running" ? "green" : false)} ${esc(b.title)} · ${b.pct}%</div>`).join("") : `<span class="faint">${esc(t("none"))}</span>`}</dd>
        <dt>${esc(t("in_dev"))}</dt><dd>${st.developing.length ? st.developing.map((d) => `<div><span class="chip">${esc(d.version || "")}</span> <span class="muted">${esc(d.about || "")}</span></div>`).join("") : `<span class="faint">${esc(t("none"))}</span>`}</dd>
        ${st.paid ? `<dt>${esc(t("paid_stage"))}</dt><dd>${esc(t("paid_note"))}</dd>` : ""}</dl>
      ${SHELVES.filter((s) => groups[s]).map((s) => `<div class="group-title"><span class="shelf-n s-${s}">${esc(t("s_" + s))}</span>${groups[s].length}</div>
        <div class="toollist">${groups[s].map((x) => `<div class="toolrow"><span class="t">${esc(x.title)}</span><span class="v">v${x.version}${x.newer ? " ↑" : ""}</span>
          <span class="d">${esc(x.about)}${x.detail ? ` · <span class="mono">${esc(x.detail)}</span>` : ""}${x.used_by && x.used_by.length > 1 ? ` · ${esc(t("used_by"))} ${esc(x.used_by.filter((u) => u !== st.title).join("、"))}` : ""}</span></div>`).join("")}</div>`).join("")}
    </div>`;
  setDrawer(true);
  document.getElementById("dclose").onclick = () => setDrawer(false);
}
function setDrawer(open) {
  document.getElementById("drawer").classList.toggle("open", open);
  document.getElementById("drawer").setAttribute("aria-hidden", String(!open));
  document.getElementById("scrim").classList.toggle("open", open);
}

/* ---------- shell ---------- */
const NAV = [["workspace", ["overview", "pipeline", "runs", "dev"]], ["governance", ["cost", "toolbox"]]];
const view = () => { const v = (location.hash.replace(/^#\/?/, "") || "overview").split("/")[0]; return VIEWS[v] ? v : "overview"; };

function render() {
  const v = view();
  const counts = { runs: S ? S.running.length : 0, dev: S ? S.in_progress.length : 0, overview: S ? S.notifications.length + S.unregistered_runs.length : 0 };
  document.getElementById("nav").innerHTML = NAV.map(([g, items]) => `<div class="nav-label">${esc(t(g))}</div>` + items.map((k) =>
    `<a href="#/${k}" class="${k === v ? "active" : ""}">${icon(k)}<span class="label">${esc(t(k))}</span>${counts[k] ? `<span class="count ${k === "overview" ? "hot" : ""}">${counts[k]}</span>` : ""}</a>`).join("")).join("");
  document.getElementById("brand-sub").textContent = t("sub");
  const theme = document.documentElement.dataset.theme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  document.getElementById("theme").innerHTML = icon(theme === "dark" ? "sun" : "moon");
  document.getElementById("lang").innerHTML = icon("globe") + (LANG === "zh" ? "EN" : "中文");
  if (!S) { document.getElementById("page").innerHTML = '<div class="empty">…</div>'; return; }
  const p = S.pipelines[0];
  document.getElementById("crumbs").innerHTML = `${p ? `<span>${esc(p.project || "")}</span><span class="sep">/</span>` : ""}<b>${esc(t(v))}</b>`;
  document.getElementById("live").innerHTML = `<span class="dot pulse" style="background:var(--green)"></span>${esc(t("updated"))} ${esc(S.at.slice(11, 16))}`;
  const y = scrollY;
  document.getElementById("page").innerHTML = VIEWS[v]();
  scrollTo(0, y);
  document.querySelectorAll("[data-stage]").forEach((el) => (el.onclick = () => { const [a, b] = el.dataset.stage.split(":").map(Number); openStage(a, b); }));
  document.querySelectorAll("[data-shelf]").forEach((el) => (el.onclick = () => { TB.shelf = el.dataset.shelf; render(); }));
  const q = document.getElementById("tbq");
  if (q) q.oninput = () => { TB.q = q.value; const pos = q.selectionStart; render(); const q2 = document.getElementById("tbq"); q2.focus(); q2.setSelectionRange(pos, pos); };
}

async function load() {
  try {
    S = await (await fetch("api/state")).json();
    render();
  } catch (e) {
    document.getElementById("live").innerHTML = `<span class="dot red"></span>offline`;
  }
}

document.getElementById("brand-crab").innerHTML = crab("judge", "");
document.getElementById("brand-crab").firstChild.setAttribute("style", "width:30px;height:22px");
const savedTheme = store.get("reins.theme", "");
if (savedTheme) document.documentElement.dataset.theme = savedTheme;
document.getElementById("theme").onclick = () => {
  const cur = document.documentElement.dataset.theme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  document.documentElement.dataset.theme = cur === "dark" ? "light" : "dark";
  store.set("reins.theme", document.documentElement.dataset.theme);
  render();
};
document.getElementById("lang").onclick = () => { LANG = LANG === "zh" ? "en" : "zh"; store.set("reins.lang", LANG); document.documentElement.lang = LANG; render(); };
document.getElementById("scrim").onclick = () => setDrawer(false);
addEventListener("keydown", (e) => { if (e.key === "Escape") setDrawer(false); });
addEventListener("hashchange", () => { setDrawer(false); scrollTo(0, 0); render(); });
document.documentElement.lang = LANG;
render();
load();
setInterval(load, 30000);
