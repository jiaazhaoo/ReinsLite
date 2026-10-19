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
    s_running: "运行中", s_stopped: "已停下，需要重启", s_paused: "暂停", s_waiting: "未开始", s_done: "已完成", s_results_in: "结果已出，收尾中", s_delivered: "已交付",
    p_process_gone: "进程已不在，需要重启", p_disk_full: "磁盘空间不足", p_out_of_memory: "内存不足被停下", p_program_error: "程序出错停下", p_budget: "等待预算批准",
    p_waiting: "等前一部分跑完", p_other: "已暂停",
    of_results: "已出结果", cases: "{n} 个案件", got: "已出结果 {n}", today: "近 24 小时 +{n}", eta: "预计还要 {t}",
    started: "开始于 {d}", last: "最近进展 {d}", spent: "花费 {v}", issues: "{n} 个已知问题在修",
    stages: "流程进度", results: "结果", why: "没有结果的案件", other: "其他原因",
    parts: "包含 {n} 个批次", h_part: "批次", h_cases: "案件", h_state: "状态", h_done: "已出结果", h_when: "时间", h_spent: "花费",
    t_production: "生产", t_rework: "返工", delivered: "已交付 v{v} · {d}", accepted_ship: "抽检通过", accepted_ship_with_note: "抽检通过（附说明）",
    accepted_rework: "抽检不通过，返工", empty: "还没有生产批次", until: "至 {d}", none_yet: "还没有结果",
    xlsx: "下载 Excel", pk_sub: "每行一个工作包；结果按交付表的状态", tab_overview: "总览", tab_batches: "批次对比", bt_sub: "每行一个交付批次（重跑和拆分已合并），可以直接对比",
    cg_delivered: "交付状态", b_delivered: "已交付", b_not_delivered: "未交付", tot_distinct: "合计按不重复案件", cg_ai: "AI 分流", cg_review: "人工审核", cg_cost: "成本耗时", m_n: "数量", m_pct: "比例", all_sites: "全部地区",
    all_types: "全部类型", csv: "导出 CSV", total: "合计", b_date: "日期", b_batch: "批次", b_cases: "案件",
    b_result: "已出结果", r_date: "复核日期", r_n: "已复核", r_ok: "对", r_bad: "错", r_unsure: "拿不准",
    r_double: "两人复核", r_agree: "一致 {a} / 不一致 {b}", r_good: "自动通过抽查", r_good_bad: "其中判错",
    b_note: "备注", results_in_other: "结果已被「{b}」覆盖", no_review: "未做人工复核", sort_hint: "点列名排序",
    rows_n: "{n} 个批次",
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
    s_running: "Running", s_stopped: "Stopped, needs a restart", s_paused: "Paused", s_waiting: "Not started", s_done: "Complete", s_results_in: "Results in, wrapping up", s_delivered: "Delivered",
    p_process_gone: "process gone, needs a restart", p_disk_full: "Disk full", p_out_of_memory: "Stopped: out of memory", p_program_error: "Stopped by an error", p_budget: "Waiting for budget approval",
    p_waiting: "Waiting for an earlier part", p_other: "Paused",
    of_results: "with a result", cases: "{n} cases", got: "{n} with a result", today: "+{n} in 24 h", eta: "about {t} to go",
    started: "started {d}", last: "last progress {d}", spent: "spent {v}", issues: "{n} known issues being fixed",
    stages: "Pipeline progress", results: "Results", why: "Cases without a result", other: "Other",
    parts: "Batches ({n})", h_part: "Batch", h_cases: "Cases", h_state: "State", h_done: "With result", h_when: "When", h_spent: "Spent",
    t_production: "production", t_rework: "rework", delivered: "Delivered v{v} · {d}", accepted_ship: "Spot check passed", accepted_ship_with_note: "Spot check passed (with a note)",
    accepted_rework: "Spot check failed: rework", empty: "No production batches yet", until: "to {d}", none_yet: "No results yet",
    xlsx: "Download Excel", pk_sub: "one row per work package; results as delivered", tab_overview: "Overview", tab_batches: "Batch comparison", bt_sub: "one row per delivered batch (re-runs and splits folded in), side by side",
    cg_delivered: "Delivered status", b_delivered: "Delivered", b_not_delivered: "Not delivered", tot_distinct: "totals count each case once", cg_ai: "AI routing", cg_review: "Human review", cg_cost: "Cost and time", m_n: "Counts", m_pct: "Shares", all_sites: "All sites",
    all_types: "All types", csv: "Export CSV", total: "Total", b_date: "Date", b_batch: "Batch", b_cases: "Cases",
    b_result: "With result", r_date: "Review date", r_n: "Reviewed", r_ok: "Correct", r_bad: "Error", r_unsure: "Unsure",
    r_double: "Two reviewers", r_agree: "{a} agree / {b} not", r_good: "Auto-pass checked", r_good_bad: "of which error",
    b_note: "Notes", results_in_other: "results overwritten by “{b}”", no_review: "no human review", sort_hint: "click a column to sort",
    rows_n: "{n} batches",
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
let LANG = store.get("reins.lang", document.documentElement.dataset.lang || "zh");   // a published copy sets data-lang
let FILTER = store.get("reins.prod.filter", "all");
let D = null, OK = true;
const BT = { group: ["ai", "cost", "delivered"].includes(store.get("reins.bt.group2", "")) ? store.get("reins.bt.group2", "") : "delivered", mode: store.get("reins.bt.mode", "n"), site: "", type: "", sort: store.get("reins.bt.sort2", "date"), dir: +store.get("reins.bt.dir2", "-1") };   // newest first
const tab = () => (location.hash.replace(/^#\/?/, "") === "batches" ? "batches" : "overview");
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
const labShort = (proj, key) => { const l = (D.labels[proj] || {})[key]; return (l && l[LANG + "_short"]) || lab(proj, key); };   // table headers
const reason = (proj, w) => { if (w === "other") return t("other"); const r = (D.reasons[proj] || [])[+w.slice(1)]; return (r && (r[LANG] || r.zh || r.en)) || w; };
const CJK = /[\u3000-\u9fff\uff00-\uffef]+/g, HAS_CJK = /[\u3000-\u9fff\uff00-\uffef]/;   // test with HAS_CJK: a /g regex keeps state
const noCjk = (s, fallback) => {                 // English: a name with Chinese in it loses those words (or falls back)
  if (LANG !== "en" || !s || !HAS_CJK.test(s)) return s;
  const t2 = s.replace(CJK, " ").replace(/\s*[·\-–]\s*$/, "").replace(/\s+/g, " ").trim();
  return t2 || fallback || s;
};
const scopeName = (s) => s.split("-").map((p) => p.replace(/wp(\d+)/g, " WP$1").trim().replace(/^\w/, (c) => c.toUpperCase())).join(" ").replace(/\s+/g, " ");

const STATE_CLS = { running: "green", stopped: "red", paused: "amber", waiting: "", done: "blue", results_in: "teal", delivered: "green" };
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
  const rows = V.changes.slice(0, 15).map((c) => `<div class="drow-i"><div class="nm"><b>${esc(modName(c))}</b> ${LANG === "en" && HAS_CJK.test(c.about) ? "" : `<span class="muted">${esc(c.about)}</span>`}</div>
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
    return `<div class="prow-i"><div class="nm">${esc(noCjk(r.name))} <span class="faint">· ${t("t_" + r.type)}</span> ${extra}</div>
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

// ---------- batch comparison ----------
const partName = (proj, k) => { const l = (D.labels[proj] || {})[k]; return (l && (l[LANG] || l.zh || l.en)) || k.replace(/^\w/, (c) => c.toUpperCase()); };
const partVal = (v) => scopeName(v || "");
function btColumns(g) {
  const pctOf = (n, d) => (n == null || !d ? null : (100 * n) / d);
  const cnt = (n, d) => (BT.mode === "pct" ? pctOf(n, d) : n);
  const sh = (n, d) => (n == null || !d ? null : n / d);
  const ai = [{ k: "with_result", h: t("b_result"), v: (r) => r.with_result, share: (r) => sh(r.with_result, r.n), fmt: "n" }]
    .concat(g.values.map((v) => ({ k: "v:" + v, h: lab(g.project, v), good: g.good.includes(v), v: (r) => (r.values ? cnt(r.values[v] || 0, r.with_result) : null),
      share: (r) => (r.values ? sh(r.values[v] || 0, r.with_result) : null), fmt: BT.mode })))
    .concat(g.details.map((d) => ({ k: "d:" + d, h: lab(g.project, d), sub: true, v: (r) => (r.details ? cnt(r.details[d] || 0, r.with_result) : null),
      share: (r) => (r.details ? sh(r.details[d] || 0, r.with_result) : null), fmt: BT.mode })));
  const rv = (f) => (r) => (r.review ? f(r.review) : null);
  const review = [
    { k: "r_date", h: t("r_date"), v: rv((x) => x.date), fmt: "date" },
    { k: "r_n", h: t("r_n"), v: rv((x) => x.n), fmt: "n" },
    { k: "r_ok", h: t("r_ok"), v: rv((x) => cnt(x.correct, x.n)), fmt: BT.mode, cls: "ok" },
    { k: "r_bad", h: t("r_bad"), v: rv((x) => cnt(x.wrong, x.n)), fmt: BT.mode, cls: "bad" },
    { k: "r_unsure", h: t("r_unsure"), v: rv((x) => cnt(x.unsure, x.n)), fmt: BT.mode },
    { k: "r_double", h: t("r_double"), v: rv((x) => x.double), fmt: "n", extra: (r) => (r.review && r.review.double ? t("r_agree", { a: r.review.agree, b: r.review.double - r.review.agree }) : "") },
    { k: "r_good", h: t("r_good"), v: rv((x) => x.good_reviewed), fmt: "n" },
    { k: "r_good_bad", h: t("r_good_bad"), v: rv((x) => (BT.mode === "pct" ? pctOf(x.good_wrong, x.good_reviewed) : x.good_wrong)), fmt: BT.mode, cls: "bad" },
  ];
  const cost = [
    { k: "spent", h: t("h_cost"), v: (r) => r.spent, fmt: "usd" },
    { k: "per_case", h: t("h_per"), v: (r) => (r.per_case == null ? null : r.per_case * 1000), fmt: "usd" },
    { k: "run_h", h: t("h_run"), v: (r) => r.run_h, fmt: "h" },
    { k: "note", h: t("b_note"), v: (r) => (r.pause ? t("p_" + r.pause) : t("s_" + (r.state || (r.status === "closed" ? "done" : r.status === "open" ? "waiting" : r.status)))), fmt: "text" },
  ];
  const partly = g.rows.some((r) => r.delivered && r.delivered_n < r.n);        // otherwise "delivered" just repeats the cases
  const delivered = [{ k: "dn", h: t("b_delivered"), v: (r) => (r.delivered ? r.delivered_n : null), share: (r) => (r.delivered ? sh(r.delivered_n, r.n) : null), fmt: "n" }]
    .concat(g.delivered_order.map((st) => ({ k: "st:" + st, h: lab(g.project, st), full: lab(g.project, st),
      good: g.delivered_good.includes(st), cls: g.delivered_bad.includes(st) ? "bad" : "",
      v: (r) => (r.delivered ? cnt(r.delivered[st] || 0, r.delivered_n) : null),
      share: (r) => (r.delivered ? sh(r.delivered[st] || 0, r.delivered_n) : null), fmt: BT.mode })))
    .concat([{ k: "nd", h: t("b_not_delivered"), v: (r) => (r.delivered ? cnt(r.n - r.delivered_n, r.n) : null),
      share: (r) => (r.delivered ? sh(r.n - r.delivered_n, r.n) : null), fmt: BT.mode }])
    .filter((c) => partly || (c.k !== "dn" && c.k !== "nd"));
  return { ai, review, cost, delivered }[BT.group] || ai;
}
function fmtCell(v, fmt) {
  if (v == null || v === "") return `<span class="faint">—</span>`;
  if (fmt === "pct") return `${v.toFixed(1)}%`;
  if (fmt === "usd") return usd(v);
  if (fmt === "h") return dur(v);
  if (fmt === "date") return day(v);
  if (fmt === "text") return esc(v);
  return num(v);
}
function btTotals(g, rows) {
  // sums of counts; shares from the sums (not an average of shares)
  const sum = (f) => rows.reduce((a, r) => a + (f(r) || 0), 0);
  const res = sum((r) => r.with_result);
  const values = {}, details = {};
  for (const r of rows) { for (const [k, n] of Object.entries(r.values || {})) values[k] = (values[k] || 0) + n; for (const [k, n] of Object.entries(r.details || {})) details[k] = (details[k] || 0) + n; }
  const revRows = rows.filter((r) => r.review);
  const review = revRows.length ? ["n", "correct", "wrong", "unsure", "double", "agree", "good_reviewed", "good_wrong"].reduce((o, k) => ((o[k] = revRows.reduce((a, r) => a + r.review[k], 0)), o), { date: "" }) : null;
  const spent = sum((r) => r.spent), n = sum((r) => r.n);
  return { name: t("total"), n, with_result: res || null, values: res ? values : null, details: res ? details : null, review,
    spent, per_case: n ? spent / n : null, run_h: sum((r) => r.run_h), status: "", scope: {}, total: true };
}
function btCsv(g, rows) {
  const head = [t("b_date"), ...g.scope_names.map((k) => partName(g.project, k)), t("b_batch"), "type", t("b_cases"), t("b_result"),
    ...g.values.map((v) => lab(g.project, v)), ...g.details.map((d) => lab(g.project, d)),
    t("r_date"), t("r_n"), t("r_ok"), t("r_bad"), t("r_unsure"), t("r_double"), "agree", t("r_good"), t("r_good_bad"), t("h_cost"), t("h_run"), "status"];
  const line = (r) => [r.date, ...g.scope_names.map((k) => r.scope[k] || ""), r.name, r.type, r.n, r.with_result ?? "",
    ...g.values.map((v) => (r.values ? r.values[v] || 0 : "")), ...g.details.map((d) => (r.details ? r.details[d] || 0 : "")),
    ...(r.review ? [r.review.date, r.review.n, r.review.correct, r.review.wrong, r.review.unsure, r.review.double, r.review.agree, r.review.good_reviewed, r.review.good_wrong] : Array(9).fill("")),
    r.spent, r.run_h ?? "", r.status];
  const q = (x) => { const v = String(x ?? ""); return /[",\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v; };
  return "﻿" + [head, ...rows.map(line)].map((l) => l.map(q).join(",")).join("\n");
}
let BT_VIEW = {};
function batchesView() {
  if (!D.batches || !D.batches.length) return `<div class="pempty">${t("empty")}</div>`;
  return D.batches.map((g, gi) => {
    const sites = [...new Set(g.rows.map((r) => r.scope[g.scope_names[0]]))];
    const types = [...new Set(g.rows.map((r) => r.type))];
    let rows = g.rows.filter((r) => (!BT.site || r.scope[g.scope_names[0]] === BT.site) && (!BT.type || r.type === BT.type));
    const cols = btColumns(g);
    const fixed = [{ k: "date", v: (r) => r.date }, ...g.scope_names.map((k) => ({ k: "s:" + k, v: (r) => r.scope[k] })), { k: "name", v: (r) => r.name }, { k: "n", v: (r) => r.n }];
    const key = [...fixed, ...cols].find((c) => c.k === BT.sort) || fixed[0];
    rows = rows.slice().sort((a, b) => { const x = key.v(a), y = key.v(b); if (x == null && y == null) return 0; if (x == null) return 1; if (y == null) return -1;
      return (typeof x === "number" ? x - y : String(x).localeCompare(String(y))) * BT.dir; });
    BT_VIEW[gi] = { g, rows };
    const split = (h) => { const m = String(h).match(/^(.*?)\s*(\(.*\)|-\s.*|（.*）|·.*)$/); return m && m[1] ? [m[1], m[2].replace(/^-\s*/, "")] : [h, ""]; };
    const th = (k, h, cls = "", full = "") => { const [a, b] = split(h);
      return `<th class="${cls} ${BT.sort === k ? "sorted" : ""}" data-sort="${esc(k)}" title="${esc(full || h)}">${esc(a)}${b ? `<small>${esc(b)}</small>` : ""}${BT.sort === k ? (BT.dir > 0 ? " ↑" : " ↓") : ""}</th>`; };
    const tot = btTotals(g, rows);
    const dt = (g.delivered_totals || {})[`${BT.site}|${BT.type}`];
    if (dt) Object.assign(tot, { n: BT.group === "delivered" ? dt.n : tot.n, delivered: dt.delivered, delivered_n: dt.delivered_n });
    const tr = (r) => {
      const over = r.results_in && BT.group === "ai";
      const cells = over ? `<td colspan="${cols.length}" class="note-cell">${esc(t("results_in_other", { b: noCjk(r.results_in) }))}</td>`
        : (BT.group === "review" && !r.review && !r.total) ? `<td colspan="${cols.length}" class="note-cell faint">${t("no_review")}</td>`
        : cols.map((c) => {
            const v = c.v(r), share = c.share ? c.share(r) : null, barred = typeof v === "number" && share != null;
            const w = barred ? Math.max(0, Math.min(100, 100 * share)) : 0;      // the bar is the share, whatever the number shows
            const cell = barred ? `<span class="bar"><i style="width:${w.toFixed(1)}%"></i><span>${fmtCell(v, c.fmt)}</span></span>` : fmtCell(v, c.fmt);
            return `<td class="${c.fmt === "text" ? "txt" : "num"} ${c.cls || ""} ${c.good ? "good" : ""}" data-l="${esc(c.h)}">${cell}${c.extra && c.extra(r) ? `<small>${esc(c.extra(r))}</small>` : ""}</td>`;
          }).join("");
      return `<tr class="${r.total ? "total" : ""}">
        <td class="dt" data-l="${t("b_date")}">${r.total ? "" : day(r.date)}</td>
        ${g.scope_names.map((k) => `<td data-l="${esc(partName(g.project, k))}">${esc(partVal(r.scope[k]))}</td>`).join("")}
        <td class="bn" data-l="${t("b_batch")}">${r.total ? `<b>${esc(r.name)}</b> <span class="faint">${t("rows_n", { n: rows.length })}</span>` : `${esc(noCjk(r.name, r.batch_id))} <span class="faint">· ${t("t_" + r.type)}</span>`}</td>
        <td class="num" data-l="${t("b_cases")}">${num(r.n)}</td>${cells}</tr>`;
    };
    const chip = (k, v, label, cur) => `<button class="fchip ${cur === v ? "on" : ""}" data-bt="${k}" data-v="${esc(v)}">${esc(label)}</button>`;
    return `<div class="pcard bt">
      <h2>${esc(g.project)}<small>${t("bt_sub")}</small></h2>
      <div class="bt-bar">
        <div class="seg">${(g.delivered_order.length ? ["delivered", "ai", "cost"] : ["ai", "cost"]).map((k) => `<button class="${BT.group === k ? "on" : ""}" data-bt="group" data-v="${k}">${t("cg_" + k)}</button>`).join("")}</div>
        ${BT.group !== "cost" ? `<div class="seg">${["n", "pct"].map((k) => `<button class="${BT.mode === k ? "on" : ""}" data-bt="mode" data-v="${k}">${t("m_" + k)}</button>`).join("")}</div>` : ""}
        <div class="grow"></div>
        <button class="fchip" data-csv="${gi}">${t("csv")}</button>
      </div>
      <div class="bt-bar">${chip("site", "", t("all_sites"), BT.site)}${sites.map((x) => chip("site", x, partVal(x), BT.site)).join("")}
        <span class="sep"></span>${chip("type", "", t("all_types"), BT.type)}${types.map((x) => chip("type", x, t("t_" + x), BT.type)).join("")}</div>
      <table class="btable"><thead><tr>
        ${th("date", t("b_date"))}${g.scope_names.map((k) => th("s:" + k, partName(g.project, k))).join("")}${th("name", t("b_batch"), "bn")}${th("n", t("b_cases"), "num")}
        ${cols.map((c) => th(c.k, c.h, (c.fmt === "text" ? "txt" : "num") + (c.sub ? " subh" : "") + (c.good ? " good" : "") + (c.cls === "bad" ? " badh" : ""), c.full)).join("")}
      </tr></thead><tbody>${rows.map(tr).join("")}${tr(tot)}</tbody></table>
      <div class="faint small-note">${t("sort_hint")}${BT.group === "delivered" ? ` · ${t("tot_distinct")}` : ""}</div>
    </div>`;
  }).join("");
}

// ---------- work packages: one compact row each ----------
const DCOL = { good: "var(--green)", bad: "var(--red)" };
function dgroup(project) { return (D.batches || []).find((g) => g.project === project) || { delivered_order: [], delivered_good: [], delivered_bad: [] }; }
function dcolor(g, k, i) {
  if (g.delivered_good.includes(k)) return DCOL.good;
  if (g.delivered_bad.includes(k)) return DCOL.bad;
  return ["var(--amber)", "var(--faint)", "var(--violet)", "var(--blue)"][i % 4];
}
function resultBar(p) {
  const g = dgroup(p.project), d = p.delivered || {};
  const keys = g.delivered_order.filter((k) => d[k]);
  const tot = keys.reduce((a, k) => a + d[k], 0);
  if (!tot) return `<span class="faint">${t("none_yet")}</span>`;
  return `<div class="stack">${keys.map((k, i) => `<i style="width:${(100 * d[k]) / tot}%;background:${dcolor(g, k, i)}" title="${esc(lab(p.project, k))}: ${num(d[k])} (${pct(d[k], tot)}%)"></i>`).join("")}</div>`;
}
function packagesTable() {
  if (!D.packages.length) return "";
  const g0 = dgroup(D.packages[0].project);
  const legend = g0.delivered_order.map((k, i) => `<span><i class="sw" style="background:${dcolor(g0, k, i)}"></i>${esc(lab(D.packages[0].project, k))}</span>`).join("");
  const share = (p, k) => { const d = p.delivered || {}; const tot = Object.values(d).reduce((a, b) => a + b, 0); return tot ? `${pct(d[k] || 0, tot)}%` : "—"; };
  const good = g0.delivered_good[0], bad = g0.delivered_bad[0];
  const rows = D.packages.map((p) => {
    const run = p.status === "running" && p.eta ? `<small>${esc(t("eta", { t: etaText(p.eta) }))}</small>` : "";
    return `<div class="wrow">
      <div class="nm"><b>${esc(scopeName(p.scope))}</b>${stateBadge(p.status, p.pause)}${run}</div>
      <div class="num"><span class="k">${t("h_cases")}</span>${num(p.n_cases)}</div>
      <div class="bar-cell">${resultBar(p)}</div>
      ${good ? `<div class="num ok"><span class="k">${esc(lab(p.project, good))}</span>${share(p, good)}</div>` : ""}
      ${bad ? `<div class="num bad"><span class="k">${esc(lab(p.project, bad))}</span>${share(p, bad)}</div>` : ""}
      <div class="num strong"><span class="k">${t("h_cost")}</span>${usd(p.spent)}</div>
      <div class="num"><span class="k">${t("h_per")}</span>${per1k(p.per_case)}</div>
      <div class="num"><span class="k">${t("h_run")}</span>${p.unknown_parts && !p.run_h ? `<span class="faint">—</span>` : dur(p.run_h)}</div>
      <div class="num"><span class="k">${t("h_span")}</span>${dur(p.calendar_h)}</div></div>`;
  }).join("");
  return `<div class="pcard"><h2>${t("packages")}<small>${t("pk_sub")}</small></h2>
    <div class="wrow head"><div>${t("h_wp")}</div><div class="num">${t("h_cases")}</div><div>${t("results")}</div>
      ${good ? `<div class="num">${esc(lab(D.packages[0].project, good))}</div>` : ""}${bad ? `<div class="num">${esc(lab(D.packages[0].project, bad))}</div>` : ""}
      <div class="num">${t("h_cost")}</div><div class="num">${t("h_per")}</div><div class="num">${t("h_run")}</div><div class="num">${t("h_span")}</div></div>
    ${rows}
    <div class="legend">${legend}</div></div>`;
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
  const tabs = `<nav class="ptabs">${["overview", "batches"].map((k) => `<a href="#/${k}" class="${tab() === k ? "on" : ""}">${t("tab_" + k)}</a>`).join("")}</nav>`;
  const xlsx = document.documentElement.dataset.xlsx;                    // a published copy offers the workbook
  const head = `<section class="phead"><h1>${t("title")}</h1><p>${t("sub", { p: D.totals.packages, r: D.totals.running, at: dayTime(D.at) })}${
    xlsx ? ` · <a class="dl" href="${esc(xlsx)}">${t("xlsx")}</a>` : ""}</p></section>${tabs}`;
  if (tab() === "batches") { page.innerHTML = head + batchesView() + `<div class="pfoot">${t("foot")}</div>`; return; }
  page.innerHTML = `
    ${head}
    ${kpis()}
    <section class="prow even">${dailySpend()}${daily()}</section>
    ${packagesTable()}
    ${devCard()}
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
  const csv = e.target.closest("[data-csv]");
  if (csv) {
    const { g, rows } = BT_VIEW[csv.dataset.csv];
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([btCsv(g, rows)], { type: "text/csv;charset=utf-8" }));
    a.download = `batches-${g.project}-${D.at.slice(0, 10)}.csv`; a.click(); return;
  }
  const th = e.target.closest("th[data-sort]");
  if (th) {
    const k = th.dataset.sort; BT.dir = BT.sort === k ? -BT.dir : (k === "date" || k.startsWith("s:") || k === "name" ? 1 : -1); BT.sort = k;
    store.set("reins.bt.sort2", BT.sort); store.set("reins.bt.dir2", BT.dir); render(); return;
  }
  const bt = e.target.closest("[data-bt]");
  if (bt) { BT[bt.dataset.bt] = bt.dataset.v; if (bt.dataset.bt !== "site" && bt.dataset.bt !== "type") store.set("reins.bt." + bt.dataset.bt + (bt.dataset.bt === "group" ? "2" : ""), bt.dataset.v); render(); return; }
  const b = e.target.closest("button[data-f]"); if (!b) return;
  FILTER = b.dataset.f; store.set("reins.prod.filter", FILTER); render();
};
addEventListener("hashchange", () => { scrollTo(0, 0); render(); });
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
