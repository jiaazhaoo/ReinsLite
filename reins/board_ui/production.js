"use strict";
// The production page: production and rework batches only, per work package. Data: /api/production (reins/production.py).

const I18N = {
  zh: {
    title: "生产进度与成本", sub: "{p} 个工作包 · {r} 个在跑 · 更新于 {at}", live: "实时", stale: "连接中断",
    k_cases: "案件总数", k_cases_f: "{n} 个工作包", k_results: "已出结果", k_results_f: "占全部案件", k_good: "自动通过",
    k_good_f: "占已出结果，不需要人看", k_review: "需人工复核", k_review_f: "AI 拿不准或有疑点", k_today: "近 24 小时新出结果",
    k_spent: "累计花费", k_spent_f: "AI 调用，按批次记账",
    daily: "每天出结果的案件", daily_sub: "近 14 天", mix: "结果分布", mix_sub: "全部工作包",
    packages: "工作包", f_all: "全部", f_active: "未完成", f_done: "已完成",
    s_running: "运行中", s_stopped: "已停下，需要重启", s_paused: "暂停", s_waiting: "未开始", s_done: "已完成", s_results_in: "结果已出，收尾中",
    p_disk_full: "磁盘空间不足", p_out_of_memory: "内存不足被停下", p_program_error: "程序出错停下", p_budget: "等待预算批准",
    p_waiting: "等前一部分跑完", p_other: "已暂停",
    of_results: "已出结果", cases: "{n} 个案件", got: "已出结果 {n}", today: "近 24 小时 +{n}", eta: "预计还要 {t}",
    started: "开始于 {d}", last: "最近进展 {d}", spent: "花费 {v}", issues: "{n} 个已知问题在修",
    stages: "流程进度", results: "结果", why: "没有结果的案件", other: "其他原因",
    parts: "包含 {n} 个批次", h_part: "批次", h_cases: "案件", h_state: "状态", h_done: "已出结果", h_when: "时间", h_spent: "花费",
    t_production: "生产", t_rework: "返工", delivered: "已交付 v{v} · {d}", accepted_ship: "抽检通过", accepted_ship_with_note: "抽检通过（附说明）",
    accepted_rework: "抽检不通过，返工", empty: "还没有生产批次", until: "至 {d}", none_yet: "还没有结果",
    a_all: "全部 AI 花费", a_all_f: "生产 {p} · 开发 {d}", a_prod: "生产花费", a_prod_f: "每千个案件 {v}",
    a_dev: "开发花费", a_dev_f: "占全部 AI 花费的 {n}%", a_7d: "近 7 天花费", a_7d_f: "生产 {p} · 开发 {d}",
    g_time: "生产耗时与结果", k_results_all: "共 {n} 个案件", s_prod: "生产", s_dev: "开发",
    dev: "开发", dev_sub: "新功能、修错、实验和评测；不含生产批次的花费",
    d_spent: "开发花费", d_7d: "近 7 天", d_rel: "近 14 天上线的改动", d_prog: "开发中的改动", d_med: "从开始到上线",
    d_med_f: "中位数 · 最长 {m}", d_kind: "花费按用途", d_module: "花费按模块", d_list: "改动", unassigned: "未归到模块",
    kd_experiment: "实验", kd_eval: "评测", kd_pilot: "试跑", kd_drift: "回归检查", kd_history: "接入 reins 前（实验、评测）", kd_other: "其他",
    cs_candidate: "开发中", cs_released: "已上线", cs_retired: "已下线", cs_abandoned: "已放弃", retry: "含被替代的旧尝试 {v}",
    h_change: "改动", h_status: "状态", h_took: "用时",
    g_cost: "成本", g_prog: "进度与结果",
    c_spent: "生产花费", c_spent_f: "全部 AI 花费 {all}，其余用于试跑、实验和评测", c_per: "每千个案件",
    c_per_f: "每个已出结果的案件 {v}", c_7d: "近 7 天花费", c_7d_f: "生产和返工", c_run: "运行时长合计",
    c_run_f: "机器实际在跑的时间", c_run_unk: "另有 {n} 个批次接入前未计时",
    dspend: "每天花费", last14: "近 14 天",
    cmp: "各工作包成本与耗时", cmp_sub: "运行 = 机器在跑；等待 = 暂停、等资源或等人决定",
    h_wp: "工作包", h_cost: "花费", h_per: "每千案件", h_run: "运行", h_wait: "等待", h_span: "历时", h_res: "已出结果",
    budget: "预算 {v}", per1000: "每千案件 {v}", run: "运行 {t}", wait: "等待 {t}", span: "历时 {t}",
    cost_stage: "花费按阶段", time_stage: "运行时长按阶段", other_stage: "其他", untimed: "接入前未计时",
    u_min: "{n} 分钟", u_h: "{n} 小时", u_d: "{n} 天",
    foot: "只统计生产和返工批次；试跑、实验和评测不在此页。花费含接入 reins 之前导入的历史记录。数据来自 reins 账本和各批次的结果文件，每 30 秒刷新。",
  },
  en: {
    title: "Production and cost", sub: "{p} work packages · {r} running · updated {at}", live: "Live", stale: "Disconnected",
    k_cases: "Cases", k_cases_f: "{n} work packages", k_results: "With a result", k_results_f: "of all cases", k_good: "Auto-passed",
    k_good_f: "of results, no person needed", k_review: "Needs a person", k_review_f: "AI unsure or found a problem", k_today: "Results in the last 24 h",
    k_spent: "Spent", k_spent_f: "AI calls, booked per batch",
    daily: "Cases finished per day", daily_sub: "last 14 days", mix: "Results", mix_sub: "all work packages",
    packages: "Work packages", f_all: "All", f_active: "In progress", f_done: "Complete",
    s_running: "Running", s_stopped: "Stopped, needs a restart", s_paused: "Paused", s_waiting: "Not started", s_done: "Complete", s_results_in: "Results in, wrapping up",
    p_disk_full: "Disk full", p_out_of_memory: "Stopped: out of memory", p_program_error: "Stopped by an error", p_budget: "Waiting for budget approval",
    p_waiting: "Waiting for an earlier part", p_other: "Paused",
    of_results: "with a result", cases: "{n} cases", got: "{n} with a result", today: "+{n} in 24 h", eta: "about {t} to go",
    started: "started {d}", last: "last progress {d}", spent: "spent {v}", issues: "{n} known issues being fixed",
    stages: "Pipeline progress", results: "Results", why: "Cases without a result", other: "Other",
    parts: "Batches ({n})", h_part: "Batch", h_cases: "Cases", h_state: "State", h_done: "With result", h_when: "When", h_spent: "Spent",
    t_production: "production", t_rework: "rework", delivered: "Delivered v{v} · {d}", accepted_ship: "Spot check passed", accepted_ship_with_note: "Spot check passed (with a note)",
    accepted_rework: "Spot check failed: rework", empty: "No production batches yet", until: "to {d}", none_yet: "No results yet",
    a_all: "All AI spend", a_all_f: "production {p} · development {d}", a_prod: "Production spend", a_prod_f: "{v} per 1,000 cases",
    a_dev: "Development spend", a_dev_f: "{n}% of all AI spend", a_7d: "Spend, last 7 days", a_7d_f: "production {p} · development {d}",
    g_time: "Production time and results", k_results_all: "of {n} cases", s_prod: "Production", s_dev: "Development",
    dev: "Development", dev_sub: "new features, fixes, experiments and evaluations; production batches not included",
    d_spent: "Development spend", d_7d: "Last 7 days", d_rel: "Changes released, 14 days", d_prog: "Changes in progress", d_med: "Start to release",
    d_med_f: "median · longest {m}", d_kind: "Spend by purpose", d_module: "Spend by module", d_list: "Changes", unassigned: "not tied to a module",
    kd_experiment: "Experiments", kd_eval: "Evaluations", kd_pilot: "Pilots", kd_drift: "Regression checks", kd_history: "Before reins (experiments, evaluations)", kd_other: "Other",
    cs_candidate: "In progress", cs_released: "Released", cs_retired: "Retired", cs_abandoned: "Abandoned", retry: "includes {v} on superseded tries",
    h_change: "Change", h_status: "Status", h_took: "Took",
    g_cost: "Cost", g_prog: "Progress and results",
    c_spent: "Production spend", c_spent_f: "all AI spend {all}; the rest went to trials, experiments and evaluations", c_per: "Per 1,000 cases",
    c_per_f: "{v} per case with a result", c_7d: "Spend, last 7 days", c_7d_f: "production and rework", c_run: "Running time",
    c_run_f: "time the machines were actually working", c_run_unk: "{n} batches from before reins have no times",
    dspend: "Spend per day", last14: "last 14 days",
    cmp: "Cost and time per work package", cmp_sub: "running = machines working; waiting = paused, waiting for resources or a decision",
    h_wp: "Work package", h_cost: "Spend", h_per: "Per 1,000", h_run: "Running", h_wait: "Waiting", h_span: "Elapsed", h_res: "With result",
    budget: "budget {v}", per1000: "{v} per 1,000 cases", run: "running {t}", wait: "waiting {t}", span: "elapsed {t}",
    cost_stage: "Spend by stage", time_stage: "Running time by stage", other_stage: "Other", untimed: "no times (before reins)",
    u_min: "{n} min", u_h: "{n} h", u_d: "{n} days",
    foot: "Production and rework batches only; trials, experiments and evaluations are not on this page. Spend includes history imported from before reins. Data: the reins ledger and each batch's result file, refreshed every 30 s.",
  },
};

const store = { get(k, d) { try { return localStorage.getItem(k) ?? d; } catch (e) { return d; } }, set(k, v) { try { localStorage.setItem(k, v); } catch (e) {} } };
let LANG = store.get("reins.lang", "zh");
let FILTER = store.get("reins.prod.filter", "all");
let D = null, OK = true;
const t = (k, vars) => { let s = I18N[LANG][k] ?? I18N.zh[k] ?? k; if (vars) for (const [a, b] of Object.entries(vars)) s = s.replace(`{${a}}`, b); return s; };
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const num = (n) => Number(n || 0).toLocaleString(LANG === "zh" ? "zh-CN" : "en-GB");
const pct = (a, b) => (b ? Math.round((100 * a) / b) : 0);
const usd = (v) => "$" + Number(v || 0).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const day = (ts) => (ts ? (LANG === "zh" ? `${+ts.slice(5, 7)}月${+ts.slice(8, 10)}日` : new Date(ts.slice(0, 10) + "T12:00").toLocaleDateString("en-GB", { day: "numeric", month: "short" })) : "—");
const dayTime = (ts) => (ts ? `${day(ts)} ${ts.slice(11, 16)}` : "—");
const dur = (h) => (h == null ? "—" : h < 1 ? t("u_min", { n: Math.round(h * 60) }) : h < 48 ? t("u_h", { n: (+h).toFixed(1).replace(/\.0$/, "") })
  : t("u_d", { n: (h / 24).toFixed(1).replace(/\.0$/, "") }));
const per1k = (v) => (v == null ? "—" : usd(v * 1000));
const STAGE_COLORS = ["var(--accent)", "var(--blue)", "var(--teal)", "var(--violet)", "var(--amber)", "var(--red)", "var(--faint)"];
const etaText = (s) => (s ? s.replace(" h", LANG === "zh" ? " 小时" : " h").replace(" min", LANG === "zh" ? " 分钟" : " min") : "");
const lab = (proj, key) => { const l = (D.labels[proj] || {})[key]; return (l && (l[LANG] || l.zh || l.en)) || key; };
const reason = (proj, w) => { if (w === "other") return t("other"); const r = (D.reasons[proj] || [])[+w.slice(1)]; return (r && (r[LANG] || r.zh || r.en)) || w; };
const scopeName = (s) => s.split("-").map((p) => p.replace(/wp(\d+)/g, " WP$1").trim().replace(/^\w/, (c) => c.toUpperCase())).join(" ").replace(/\s+/g, " ");

const STATE_CLS = { running: "green", stopped: "red", paused: "amber", waiting: "", done: "blue", results_in: "teal" };
const badge = (text, cls = "", dot = false) => `<span class="badge ${cls}">${dot ? `<span class="dot ${dot}"></span>` : ""}${esc(text)}</span>`;
const stateBadge = (s, pause) => badge(s === "paused" && pause ? `${t("s_paused")} · ${t("p_" + pause)}` : t("s_" + s), STATE_CLS[s] || "",
  s === "running" ? "green pulse" : false);
const COLORS = ["var(--amber)", "var(--red)", "var(--violet)", "var(--blue)", "var(--teal)", "var(--faint)"];

// the coloured pieces of a results bar: good values in green, the rest by detail
function pieces(proj, results) {
  const out = []; let ci = 0;
  for (const r of results) {
    if (r.good) { out.push({ label: lab(proj, r.value), n: r.n, color: "var(--green)" }); continue; }
    if (r.details.length) {
      const inDetail = r.details.reduce((a, d) => a + d.n, 0);
      for (const d of r.details) out.push({ label: lab(proj, d.value), n: d.n, color: COLORS[ci++ % COLORS.length] });
      if (r.n > inDetail) out.push({ label: lab(proj, r.value), n: r.n - inDetail, color: COLORS[ci++ % COLORS.length] });
    } else out.push({ label: lab(proj, r.value), n: r.n, color: COLORS[ci++ % COLORS.length] });
  }
  return out;
}
function stackHtml(ps, total) {
  if (!total) return `<div class="faint">${t("none_yet")}</div>`;
  return `<div class="stack">${ps.map((p) => `<i style="width:${(100 * p.n) / total}%;background:${p.color}" title="${esc(p.label)} ${p.n}"></i>`).join("")}</div>
    <div class="legend">${ps.map((p) => `<span><i class="sw" style="background:${p.color}"></i>${esc(p.label)} <b>${num(p.n)}</b> ${pct(p.n, total)}%</span>`).join("")}</div>`;
}

function kpis() {
  const T = D.totals, V = D.dev, review = T.with_result - T.good, got = T.with_result || T.finished;
  const all = T.spent + V.spent;
  const k = (l, v, f, extra = "", cls = "") => `<div class="pkpi ${cls}"><div class="l">${esc(l)}</div><div class="v">${v}</div>${extra}<div class="f">${esc(f)}</div></div>`;
  const split = `<div class="stack thin"><i style="width:${pct(T.spent, all)}%;background:var(--accent)"></i><i style="width:${100 - pct(T.spent, all)}%;background:var(--violet)"></i></div>`;
  return `<section><div class="ghead">${t("g_cost")}</div><div class="pkpis four">
    ${k(t("a_all"), usd(all), t("a_all_f", { p: usd(T.spent), d: usd(V.spent) }), split, "hero")}
    ${k(t("a_prod"), usd(T.spent), t("a_prod_f", { v: per1k(T.per_case) }))}
    ${k(t("a_dev"), usd(V.spent), t("a_dev_f", { n: pct(V.spent, all) }))}
    ${k(t("a_7d"), usd(T.spent_7d + V.spent_7d), t("a_7d_f", { p: usd(T.spent_7d), d: usd(V.spent_7d) }))}
  </div></section>
  <section><div class="ghead">${t("g_time")}</div><div class="pkpis four">
    ${k(t("c_run"), dur(T.run_h), T.unknown_parts ? t("c_run_unk", { n: T.unknown_parts }) : t("c_run_f"))}
    ${k(t("k_results"), `${num(got)}<small>${pct(got, T.n_cases)}%</small>`, t("k_results_all", { n: num(T.n_cases) }),
      `<div class="meter"><i class="green" style="width:${pct(got, T.n_cases)}%"></i></div>`)}
    ${k(t("k_good"), `${num(T.good)}<small>${pct(T.good, T.with_result)}%</small>`, t("k_good_f"))}
    ${k(t("k_review"), `${num(review)}<small>${pct(review, T.with_result)}%</small>`, t("k_review_f"))}
  </div></section>`;
}

function bars(title, sub, key, fmt, color) {
  const max = Math.max(1e-9, ...D.daily.map((d) => d[key] || 0));
  return `<div class="pcard dl"><h2>${title}<small>${sub}</small></h2><div class="daily">${D.daily.map((d) => { const v = d[key] || 0;
    return `<div class="d" title="${d.day}: ${fmt(v)}"><b>${v ? fmt(v) : ""}</b><i class="${v ? "" : "zero"}" style="height:${v ? Math.max(3, (100 * v) / max) : 2}%;${v && color ? `background:${color}` : ""}"></i><span>${+d.day.slice(5, 7)}/${+d.day.slice(8, 10)}</span></div>`; }).join("")}</div></div>`;
}
const daily = () => bars(t("daily"), t("daily_sub"), "n", num, "var(--green)");
const money = (v) => "$" + (v >= 10 ? Math.round(v) : v.toFixed(1));
function dailySpend() {
  const dev = Object.fromEntries(D.dev.daily.map((d) => [d.day, d.usd]));
  const max = Math.max(1e-9, ...D.daily.map((d) => d.usd + (dev[d.day] || 0)));
  return `<div class="pcard dl"><h2>${t("dspend")}<small>${t("last14")}</small></h2><div class="daily">${D.daily.map((d) => {
    const p = d.usd, v = dev[d.day] || 0, tot = p + v;
    return `<div class="d" title="${d.day}: ${t("s_prod")} ${usd(p)} · ${t("s_dev")} ${usd(v)}"><b>${tot ? money(tot) : ""}</b>
      <div class="col" style="height:${tot ? Math.max(3, (100 * tot) / max) : 2}%">${tot ? `<i class="dv" style="flex:${v}"></i><i class="pr" style="flex:${p}"></i>` : `<i class="zero"></i>`}</div>
      <span>${+d.day.slice(5, 7)}/${+d.day.slice(8, 10)}</span></div>`; }).join("")}</div>
    <div class="legend"><span><i class="sw" style="background:var(--accent)"></i>${t("s_prod")}</span><span><i class="sw" style="background:var(--violet)"></i>${t("s_dev")}</span></div></div>`;
}

function compare() {
  const rows = D.packages.map((p) => {
    const got = p.with_result || p.finished;
    return `<div class="crow-i"><div class="nm">${esc(scopeName(p.scope))} ${stateBadge(p.status, p.pause)}</div>
      <div class="num"><span class="k">${t("h_cases")}</span>${num(p.n_cases)}</div>
      <div class="num strong"><span class="k">${t("h_cost")}</span>${usd(p.spent)}</div>
      <div class="num"><span class="k">${t("h_per")}</span>${per1k(p.per_case)}</div>
      <div class="num"><span class="k">${t("h_run")}</span>${p.unknown_parts && !p.run_h ? `<span class="faint">${t("untimed")}</span>` : dur(p.run_h)}</div>
      <div class="num"><span class="k">${t("h_wait")}</span>${p.wait_h == null ? "—" : dur(p.wait_h)}</div>
      <div class="num"><span class="k">${t("h_span")}</span>${dur(p.calendar_h)}</div>
      <div class="num"><span class="k">${t("h_res")}</span>${pct(got, p.n_cases)}%</div></div>`;
  }).join("");
  const T = D.totals;
  return `<div class="pcard"><h2>${t("cmp")}<small>${t("cmp_sub")}</small></h2>
    <div class="crow-h"><div>${t("h_wp")}</div><div>${t("h_cases")}</div><div>${t("h_cost")}</div><div>${t("h_per")}</div><div>${t("h_run")}</div><div>${t("h_wait")}</div><div>${t("h_span")}</div><div>${t("h_res")}</div></div>
    ${rows}
    <div class="crow-i total"><div class="nm">${esc(t("f_all"))}</div><div class="num"><span class="k">${t("h_cases")}</span>${num(T.n_cases)}</div>
      <div class="num strong"><span class="k">${t("h_cost")}</span>${usd(T.spent)}</div>
      <div class="num"><span class="k">${t("h_per")}</span>${per1k(T.per_case)}</div><div class="num"><span class="k">${t("h_run")}</span>${dur(T.run_h)}</div><div></div><div></div>
      <div class="num"><span class="k">${t("h_res")}</span>${pct(T.with_result || T.finished, T.n_cases)}%</div></div></div>`;
}

function devCard() {
  const V = D.dev;
  const proj = D.packages[0] ? D.packages[0].project : "";
  const modName = (c) => (c.module === "unassigned" ? t("unassigned") : lab(c.project || proj, c.module));
  const split = (items, key, name) => {
    const total = items.reduce((a, x) => a + x.usd, 0);
    if (!total) return `<div class="faint">—</div>`;
    return `<div class="stack">${items.map((x, i) => `<i style="width:${(100 * x.usd) / total}%;background:${STAGE_COLORS[(i + 3) % STAGE_COLORS.length]}"></i>`).join("")}</div>
      <div class="legend">${items.map((x, i) => `<span><i class="sw" style="background:${STAGE_COLORS[(i + 3) % STAGE_COLORS.length]}"></i>${esc(name(x))} <b>${usd(x.usd)}</b> ${pct(x.usd, total)}%</span>`).join("")}</div>`;
  };
  const stat = (l, v, f) => `<div class="ct"><div class="l">${esc(l)}</div><div class="v">${v}</div>${f ? `<div class="f">${esc(f)}</div>` : ""}</div>`;
  const CS = { candidate: "amber", released: "green", retired: "", abandoned: "" };
  const rows = V.changes.slice(0, 15).map((c) => `<div class="drow-i"><div class="nm"><b>${esc(modName(c))}</b> <span class="muted">${esc(c.about)}</span></div>
      <div>${badge(t("cs_" + c.status), CS[c.status] || "")}</div>
      <div class="num"><span class="k">${t("h_took")}</span>${c.status === "candidate" ? "" : ""}${dur(c.hours)}</div>
      <div class="num"><span class="k">${t("h_cost")}</span>${c.spent ? usd(c.spent) : "—"}</div></div>`).join("");
  return `<div class="pcard"><h2>${t("dev")}<small>${t("dev_sub")}</small></h2>
    <div class="ctstats five">
      ${stat(t("d_spent"), usd(V.spent), "")}
      ${stat(t("d_7d"), usd(V.spent_7d), "")}
      ${stat(t("d_rel"), num(V.released_14d), "")}
      ${stat(t("d_prog"), num(V.in_progress), "")}
      ${stat(t("d_med"), dur(V.median_h), t("d_med_f", { m: dur(V.max_h) }))}
    </div>
    <div class="ctsplit" style="margin-top:16px"><div><div class="sub-h">${t("d_kind")}</div>${split(V.by_kind, "usd", (x) => t("kd_" + x.kind))}</div>
      <div><div class="sub-h">${t("d_module")}</div>${split(V.by_module, "usd", (x) => modName({ ...x, project: proj }))}</div></div>
    <details class="parts flat" data-k="dev-changes"><summary>${t("d_list")} (${V.changes.length})</summary>
      <div class="plist"><div class="drow-h"><div>${t("h_change")}</div><div>${t("h_status")}</div><div>${t("h_took")}</div><div>${t("h_cost")}</div></div>${rows}</div></details>
  </div>`;
}

function costTime(p) {
  const order = p.stages.map((s) => s.stage).concat(["other"]);
  const color = (st) => STAGE_COLORS[Math.max(0, order.indexOf(st)) % STAGE_COLORS.length];
  const name = (st) => (st === "other" ? t("other_stage") : lab(p.project, st));
  const split = (items, key, fmt) => {
    const total = items.reduce((a, x) => a + x[key], 0);
    if (!total) return `<div class="faint">—</div>`;
    return `<div class="stack">${items.map((x) => `<i style="width:${(100 * x[key]) / total}%;background:${color(x.stage)}" title="${esc(name(x.stage))} ${fmt(x[key])}"></i>`).join("")}</div>
      <div class="legend">${items.map((x) => `<span><i class="sw" style="background:${color(x.stage)}"></i>${esc(name(x.stage))} <b>${fmt(x[key])}</b> ${pct(x[key], total)}%</span>`).join("")}</div>`;
  };
  const stat = (l, v, f) => `<div class="ct"><div class="l">${esc(l)}</div><div class="v">${v}</div>${f ? `<div class="f">${esc(f)}</div>` : ""}</div>`;
  return `<div class="ctbox">
    <div class="ctstats">
      ${stat(t("h_cost"), usd(p.spent), [p.budget ? t("budget", { v: usd(p.budget) }) : "", p.spent_retry ? t("retry", { v: usd(p.spent_retry) }) : ""].filter(Boolean).join(" · "))}
      ${stat(t("h_per"), per1k(p.per_case), "")}
      ${stat(t("h_run"), p.unknown_parts && !p.run_h ? `<span class="faint small">${t("untimed")}</span>` : dur(p.run_h), p.unknown_parts && p.run_h ? t("c_run_unk", { n: p.unknown_parts }) : "")}
      ${stat(t("h_wait"), p.wait_h == null ? "—" : dur(p.wait_h), t("span", { t: dur(p.calendar_h) }))}
    </div>
    <div class="ctsplit"><div><div class="sub-h">${t("cost_stage")}</div>${split(p.cost_by_stage, "usd", usd)}</div>
      <div><div class="sub-h">${t("time_stage")}</div>${split(p.time_by_stage, "h", dur)}</div></div>
  </div>`;
}

function mix() {
  // results of all packages together, merged by label so projects with the same words add up
  const merged = new Map(); let total = 0;
  for (const p of D.packages) for (const x of pieces(p.project, p.results)) {
    const m = merged.get(x.label) || { ...x, n: 0 }; m.n += x.n; merged.set(x.label, m); total += x.n;
  }
  const ps = [...merged.values()].sort((a, b) => (a.color === "var(--green)" ? -1 : b.color === "var(--green)" ? 1 : b.n - a.n));
  return `<div class="pcard"><h2>${t("mix")}<small>${t("mix_sub")}</small></h2>${stackHtml(ps, total)}</div>`;
}

function stepsHtml(p) {
  const n = p.n_cases;
  return `<div><div class="sub-h">${t("stages")}</div><div class="psteps">${p.stages.map((s, i) => {
    const cls = s.done >= n ? "full" : s.done > 0 ? "part" : "";
    return `<div class="pstep ${cls}"><div class="n"><em>${s.done >= n ? "✓" : i + 1}</em>${esc(lab(p.project, s.stage))}</div>
      <div class="c">${num(s.done)} / ${num(n)}</div><div class="wp-bar ${cls === "part" ? "going" : ""}"><i style="width:${pct(s.done, n)}%"></i></div></div>`;
  }).join("")}</div></div>`;
}

function partWhen(r) {
  if (r.state === "running" && r.eta) return t("eta", { t: etaText(r.eta) });
  const span = r.ended ? `${day(r.started)} – ${day(r.ended)}` : day(r.started);
  return r.run_h != null ? `${span} · ${t("run", { t: dur(r.run_h) })}` : span;
}
function partsHtml(p) {
  const rows = p.parts.slice().reverse().map((r) => {
    const extra = [r.delivered ? t("delivered", { v: r.delivered.version, d: day(r.delivered.at) }) : "",
      r.accepted ? t("accepted_" + r.accepted.decision) : ""].filter(Boolean).map((x) => badge(x, "teal")).join(" ");
    return `<div class="prow-i"><div class="nm">${esc(r.name)} <span class="faint">· ${t("t_" + r.type)}</span> ${extra}</div>
      <div class="num"><span class="k">${t("h_cases")}</span>${num(r.n)}</div>
      <div>${stateBadge(r.state, r.pause)}</div>
      <div class="num"><span class="k">${t("h_done")}</span>${num(r.finished)} <span class="faint">${pct(r.finished, r.n)}%</span></div>
      <div><span class="k">${t("h_when")}</span>${partWhen(r)}</div>
      <div class="num"><span class="k">${t("h_spent")}</span>${usd(r.spent)}</div></div>`;
  }).join("");
  return `<details class="parts" data-k="${esc(p.project + "/" + p.scope)}"><summary>${t("parts", { n: p.parts.length })}</summary>
    <div class="plist"><div class="prow-h"><div>${t("h_part")}</div><div>${t("h_cases")}</div><div>${t("h_state")}</div><div>${t("h_done")}</div><div>${t("h_when")}</div><div>${t("h_spent")}</div></div>${rows}</div></details>`;
}

function packageHtml(p, showProject) {
  const got = p.with_result || p.finished, share = pct(got, p.n_cases);
  const ps = pieces(p.project, p.results), totalRes = ps.reduce((a, x) => a + x.n, 0);
  const facts = [t("cases", { n: num(p.n_cases) }), t("got", { n: num(got) }),
    p.today ? t("today", { n: num(p.today) }) : "", p.eta ? t("eta", { t: etaText(p.eta) }) : "",
    t("started", { d: day(p.started) }), p.last_activity ? t("last", { d: dayTime(p.last_activity) }) : "",
    p.open_issues ? t("issues", { n: p.open_issues }) : ""].filter(Boolean);
  return `<article class="wp">
    <div class="wp-head"><div><h3>${esc(scopeName(p.scope))}</h3>${showProject ? `<div class="sub">${esc(p.project)}</div>` : ""}
      <div class="badges">${stateBadge(p.status, p.pause)}</div></div>
      <div class="big"><b>${share}%</b><span>${t("of_results")}</span></div></div>
    <div class="wp-body">
      <div class="wp-bar ${p.status === "running" ? "going" : ""}"><i style="width:${share}%"></i></div>
      <div class="facts">${facts.map((f) => `<span>${esc(f)}</span>`).join("")}</div>
      ${costTime(p)}
      ${p.stages.length && !["done", "results_in"].includes(p.status) ? stepsHtml(p) : ""}
      <div><div class="sub-h">${t("results")}</div>${stackHtml(ps, totalRes)}</div>
      ${p.why.length ? `<div><div class="sub-h">${t("why")}</div><div class="why">${p.why.map((w) => `<span>${esc(reason(p.project, w.word))} <b>${num(w.n)}</b></span>`).join("")}</div></div>` : ""}
    </div>
    ${partsHtml(p)}
  </article>`;
}

function render() {
  document.documentElement.lang = LANG;
  document.title = t("title");
  document.getElementById("p-title").textContent = t("title");
  for (const b of document.querySelectorAll("#p-lang button")) b.classList.toggle("on", b.dataset.lang === LANG);
  const theme = document.documentElement.dataset.theme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  document.getElementById("p-theme").innerHTML = theme === "dark"
    ? `<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>`
    : `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>`;
  document.getElementById("p-live").innerHTML = `<span class="dot ${OK ? "green pulse" : ""}"></span>${t(OK ? "live" : "stale")}`;
  const page = document.getElementById("p-page");
  if (!D) return;
  const open = new Set([...page.querySelectorAll("details[open]")].map((d) => d.dataset.k));
  const multi = new Set(D.packages.map((p) => p.project)).size > 1;
  const shown = D.packages.filter((p) => FILTER === "all" || (FILTER === "done" ? p.status === "done" : p.status !== "done"));
  const fchip = (k) => `<button class="fchip ${FILTER === k ? "on" : ""}" data-f="${k}">${t("f_" + k)} <span class="faint">${
    D.packages.filter((p) => k === "all" || (k === "done" ? p.status === "done" : p.status !== "done")).length}</span></button>`;
  page.innerHTML = `
    <section class="phead"><h1>${t("title")}</h1><p>${t("sub", { p: D.totals.packages, r: D.totals.running, at: dayTime(D.at) })}</p></section>
    ${kpis()}
    <section class="prow even">${dailySpend()}${daily()}</section>
    ${compare()}
    ${devCard()}
    ${mix()}
    <section class="pfilters"><h2>${t("packages")}</h2>${["all", "active", "done"].map(fchip).join("")}</section>
    ${shown.length ? shown.map((p) => packageHtml(p, multi)).join("") : `<div class="pempty">${t("empty")}</div>`}
    <div class="pfoot">${t("foot")}</div>`;
  for (const d of page.querySelectorAll("details[data-k]")) if (open.has(d.dataset.k)) d.open = true;
}

async function load() {
  try {
    const r = await fetch("api/production", { cache: "no-store" });
    D = await r.json(); OK = true;
  } catch (e) { OK = false; }
  render();
}

document.getElementById("p-lang").onclick = (e) => {
  const b = e.target.closest("button[data-lang]"); if (!b) return;
  LANG = b.dataset.lang; store.set("reins.lang", LANG); render();
};
document.getElementById("p-page").onclick = (e) => {
  const b = e.target.closest("button[data-f]"); if (!b) return;
  FILTER = b.dataset.f; store.set("reins.prod.filter", FILTER); render();
};
const savedTheme = store.get("reins.theme", "");
if (savedTheme) document.documentElement.dataset.theme = savedTheme;
document.getElementById("p-theme").onclick = () => {
  const cur = document.documentElement.dataset.theme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  document.documentElement.dataset.theme = cur === "dark" ? "light" : "dark";
  store.set("reins.theme", document.documentElement.dataset.theme); render();
};
render();
load();
setInterval(load, 30000);
