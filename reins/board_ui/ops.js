"use strict";
/* Ops panel (#/ops): the delivery batches (production, rework) as a schedule, what the automation did to them, and how
   far their review is. Data: /api/ops (reins/ops.py). Loaded before app.js; uses its helpers (t, esc, when, usd) at
   render time. */

const OPS_I18N = {
  zh: {
    ops: "调度台", ops_sub: "要交付的批次：排在哪、在等什么、自动化做了什么、人审到哪了",
    o_sched: "交付排班", o_sched_sub: "实线是已经跑过的，虚线是预计（按这个批次自己的速度，或以往同类阶段每个 case 的用时）",
    o_running: "在跑", o_waiting: "在等", o_finish: "预计全部完成", o_late: "会晚于截止日", o_none: "最近 3 天没有交付批次",
    o_batch: "批次", o_state: "状态", o_progress: "进度", o_eta: "预计完成", o_due: "截止", o_spend: "花费 / 上限",
    o_now: "现在", o_unknown: "暂无法估计", o_cap0: "未批", o_why: "原因",
    k_done: "完成", k_stalled: "停住", k_running: "进行中", k_forecast: "预计", k_planned: "排队", k_failed: "失败", k_skipped: "跳过",
    s_open: "已开未跑", s_running: "运行中", s_paused: "暂停", s_done: "完成", s_failed: "失败", s_closed: "已关闭",
    o_auto: "自动化做了什么", o_auto_sub: "最近 72 小时这些批次上发生的事；\"自动\" = 没有人参与（看门狗、哨兵、监督进程）",
    o_only_auto: "只看自动", o_by_auto: "自动", o_by_session: "会话", o_timers: "定时任务", o_timers_sub: "这台电脑上自己会跑的任务",
    o_next: "下次", o_last: "上次", o_notices: "提醒",
    o_review: "人工审核", o_review_sub: "每个批次需要人看的 case、分配和判定进度；客户反馈单独一列",
    o_need: "需人工", o_assigned: "已分配", o_judged: "已判", o_wrong: "判错", o_reviewers: "审核员", o_client: "客户反馈",
    o_open_review: "打开审核页", o_no_review: "没有在线的审核页",
    ev_state: "状态变化", ev_stage_start: "阶段开始", ev_stage_end: "阶段结束", ev_opened: "开批次", ev_probe_set: "设进度探针",
    ev_lineage: "记录来源", ev_spend_approved: "批准花费", ev_done: "完成", ev_time_budget: "设时间预算", ev_paused: "暂停",
    ev_owner_changed: "换负责会话", ev_preflight: "开跑前检查", ev_closed: "关闭", ev_abandoned: "放弃",
    ev_outcome_without_input: "有结果但没输入", ev_stage_failed: "阶段失败", ev_per_case_off: "不按 case 记录",
    ev_stage_skipped: "阶段跳过", ev_running: "开始运行", ev_stage_restart: "阶段重跑", ev_outcomes_repaired: "修复结果记录",
    ev_finished: "跑完",
    w_paused: "暂停：", w_cap: "等你批花费上限（有付费阶段）", w_preflight: "等开跑前检查", w_notstarted: "已开，还没启动",
    w_machine: "等机器资源：", w_nostage: "在跑，但还没有阶段开始",
  },
  en: {
    ops: "Ops", ops_sub: "Delivery batches: where they stand in the schedule, what they wait for, what the automation did, how far review is",
    o_sched: "Delivery schedule", o_sched_sub: "solid = ran, dashed = forecast (this batch's own pace, or earlier delivery batches' time per case for the stage)",
    o_running: "Running", o_waiting: "Waiting", o_finish: "All done by", o_late: "Will miss its deadline", o_none: "No delivery batches in the last 3 days",
    o_batch: "Batch", o_state: "State", o_progress: "Progress", o_eta: "Forecast", o_due: "Deadline", o_spend: "Spend / cap",
    o_now: "now", o_unknown: "no estimate yet", o_cap0: "not approved", o_why: "Why",
    k_done: "done", k_stalled: "stalled", k_running: "running", k_forecast: "forecast", k_planned: "queued", k_failed: "failed", k_skipped: "skipped",
    s_open: "opened", s_running: "running", s_paused: "paused", s_done: "done", s_failed: "failed", s_closed: "closed",
    o_auto: "What the automation did", o_auto_sub: "the last 72 hours on these batches; \"auto\" = no person involved (watchdog, sentinel, supervisor)",
    o_only_auto: "automation only", o_by_auto: "auto", o_by_session: "session", o_timers: "Timers", o_timers_sub: "jobs this machine runs by itself",
    o_next: "next", o_last: "last", o_notices: "Alerts",
    o_review: "Human review", o_review_sub: "per batch: cases that need a person, assignment and verdict progress; the client's feedback in its own column",
    o_need: "Need a person", o_assigned: "Assigned", o_judged: "Judged", o_wrong: "Wrong", o_reviewers: "Reviewers", o_client: "Client feedback",
    o_open_review: "Open review page", o_no_review: "no live review page",
    ev_state: "state change", ev_stage_start: "stage started", ev_stage_end: "stage ended", ev_opened: "opened", ev_probe_set: "progress probe set",
    ev_lineage: "lineage recorded", ev_spend_approved: "spend approved", ev_done: "done", ev_time_budget: "time budget set", ev_paused: "paused",
    ev_owner_changed: "owner changed", ev_preflight: "preflight", ev_closed: "closed", ev_abandoned: "abandoned",
    ev_outcome_without_input: "result without input", ev_stage_failed: "stage failed", ev_per_case_off: "not recorded per case",
    ev_stage_skipped: "stage skipped", ev_running: "started", ev_stage_restart: "stage restarted", ev_outcomes_repaired: "results repaired",
    ev_finished: "finished",
    w_paused: "paused: ", w_cap: "waiting for a spend cap (paid stages)", w_preflight: "waiting for preflight", w_notstarted: "opened, not started",
    w_machine: "waiting for the machine: ", w_nostage: "running, no stage started",
  },
};

const OPS = {
  data: null,
  onlyAuto: false,
  async load() {
    try { OPS.data = await (await fetch("api/ops")).json(); } catch (e) { OPS.data = OPS.data || null; }
  },
  evName(e) { const k = "ev_" + e; const v = t(k); return v === k ? e : v; },
  state(b) { return t("s_" + b.status); },
  why(w) {                                                  // ops.py writes these phrases in English
    if (!w) return "";
    const pre = [["paused: ", "w_paused"], ["waiting for the machine: ", "w_machine"]];
    for (const [p, k] of pre) if (w.startsWith(p)) return t(k) + w.slice(p.length);
    return { "waiting for a spend cap (paid stages)": t("w_cap"), "waiting for preflight": t("w_preflight"),
             "opened, not started": t("w_notstarted"), "running, no stage started": t("w_nostage") }[w] || w;
  },
  label(b) { const m = b.batch_id.match(/(\d{4})(\d{2})(\d{2})-(\d+)$/); return m ? `${m[2]}-${m[3]} #${m[4]}` : ""; },
  timeline(B, nowIso) {
    const now = new Date(nowIso).getTime();
    const segs = B.flatMap((b) => b.segments.map((s) => [new Date(s.from).getTime(), new Date(s.to).getTime()]));
    // the last 48 hours and what is ahead (up to two weeks); older work is cut at the left edge
    const lo = now - 48 * 3600e3;
    const hi = Math.min(Math.max(now + 24 * 3600e3, ...segs.map((x) => x[1])), now + 14 * 86400e3);
    const pos = (ms) => Math.max(0, Math.min(100, (100 * (ms - lo)) / (hi - lo)));
    const days = [];
    for (let d = new Date(lo); d.getTime() <= hi; d.setHours(24, 0, 0, 0)) {
      const p = pos(d.getTime()); if (p > 0 && p < 100) days.push(`<span class="ops-tick" style="left:${p}%"><i>${esc(d.toISOString().slice(5, 10))}</i></span>`);
    }
    const rows = B.map((b) => {
      const bars = b.segments.map((s) => {
        const a = pos(new Date(s.from).getTime()), z = pos(new Date(s.to).getTime());
        const tip = `${s.stage} · ${t("k_" + s.kind)} · ${s.from.slice(5, 16).replace("T", " ")} → ${s.to.slice(5, 16).replace("T", " ")}`;
        return `<span class="ops-bar k-${s.kind}" style="left:${a}%;width:${Math.max(z - a, 0.4)}%" title="${esc(tip)}"><em>${esc(s.stage)}</em></span>`;
      }).join("");
      return `<div class="ops-row"><div class="ops-lab" title="${esc(b.batch_id + " — " + b.purpose)}"><b>${esc(scopeName(b.scope))}</b><span class="faint">${esc(typeName(b.type))} ${esc(OPS.label(b))} · ${esc(OPS.state(b))}</span></div>
        <div class="ops-lane">${bars}${b.waiting ? `<span class="ops-wait" title="${esc(OPS.why(b.waiting))}">${esc(OPS.why(b.waiting))}</span>` : ""}</div></div>`;
    }).join("");
    return `<div class="card ops-tl"><div class="ops-axis">${days}<span class="ops-now" style="left:${pos(now)}%"><i>${esc(t("o_now"))}</i></span></div>${rows}
      <div class="ops-legend">${["done", "running", "forecast", "planned", "stalled", "failed"].map((k) => `<span><span class="ops-sw k-${k}"></span>${esc(t("k_" + k))}</span>`).join("")}</div></div>`;
  },
  render() {
    const D = OPS.data;
    if (!D) return '<div class="empty">…</div>';
    const B = D.batches;
    const live = B.filter((b) => ["open", "running", "paused"].includes(b.status));
    const run = live.filter((b) => b.status === "running" && !b.waiting).length;
    const wait = live.filter((b) => b.waiting).length;
    const last = live.map((b) => b.forecast_finish).filter(Boolean).sort().pop();
    const late = B.filter((b) => b.late).length;
    const kpi = `<div class="grid g4">
      <div class="card kpi"><div class="label"><span class="dot ${run ? "pulse" : ""}" style="${run ? "background:var(--green)" : ""}"></span>${esc(t("o_running"))}</div><div class="value">${run}</div></div>
      <div class="card kpi ${wait ? "hot" : ""}"><div class="label"><span class="dot red"></span>${esc(t("o_waiting"))}</div><div class="value">${wait}</div></div>
      <div class="card kpi"><div class="label"><span class="dot accent"></span>${esc(t("o_finish"))}</div><div class="value" style="font-size:20px">${last ? when(last) : "—"}</div></div>
      <div class="card kpi ${late ? "hot" : ""}"><div class="label"><span class="dot red"></span>${esc(t("o_late"))}</div><div class="value">${late}</div></div></div>`;
    const cur = (b) => { const s = b.stages.find((x) => x.status === "running") || [...b.stages].reverse().find((x) => x.status === "done") || b.stages[0];
      return s ? `${esc(s.stage)} ${s.done}/${s.of}` : "—"; };
    const table = `<div class="card"><table class="ops-t"><thead><tr><th>${esc(t("o_batch"))}</th><th>${esc(t("o_state"))}</th><th>${esc(t("o_progress"))}</th><th>${esc(t("o_eta"))}</th><th>${esc(t("o_due"))}</th><th>${esc(t("o_spend"))}</th></tr></thead><tbody>
      ${B.map((b) => `<tr class="${b.late ? "late" : ""}"><td title="${esc(b.purpose)}"><b>${esc(scopeName(b.scope))}</b> <span class="faint">${esc(typeName(b.type))} ${esc(OPS.label(b))}</span><div class="faint mono">${esc(b.batch_id)}</div></td>
        <td>${esc(OPS.state(b))}${b.waiting ? `<div class="ops-why">${esc(OPS.why(b.waiting))}</div>` : ""}</td><td>${cur(b)}</td>
        <td>${b.forecast_finish ? when(b.forecast_finish) : (["open", "running", "paused"].includes(b.status) ? `<span class="faint">${esc(t("o_unknown"))}</span>` : "—")}</td>
        <td>${b.deadline ? when(b.deadline) : "—"}</td><td>${usd(b.spend)} / ${b.spend_cap ? usd(b.spend_cap) : `<span class="faint">${esc(t("o_cap0"))}</span>`}</td></tr>`).join("")}</tbody></table></div>`;
    const evs = D.events.filter((e) => !OPS.onlyAuto || e.by === "automation").slice(0, 120);
    const feed = `<div class="card ops-feed"><label class="ops-toggle"><input type="checkbox" id="opsAuto" ${OPS.onlyAuto ? "checked" : ""}> ${esc(t("o_only_auto"))}</label>
      ${D.notices.slice(0, 8).map((n) => `<div class="ops-ev notice ${n.level}"><span class="when">${when(n.at)}</span><span class="tag">${esc(t("o_notices"))}</span><span class="what"><b>${esc(n.title)}</b> ${esc(n.body || "")}</span></div>`).join("")}
      ${evs.map((e) => `<div class="ops-ev"><span class="when">${when(e.at)}</span><span class="tag ${e.by === "automation" ? "auto" : ""}">${esc(e.by === "automation" ? t("o_by_auto") : t("o_by_session"))}</span>
        <span class="what"><b>${esc(OPS.evName(e.event))}</b> · ${esc(scopeName(B.find((b) => b.batch_id === e.batch)?.scope || e.batch))}${e.detail ? ` — <span class="faint">${esc(e.detail)}</span>` : ""}</span></div>`).join("")}</div>`;
    const timers = `<div class="card ops-timers">${D.timers.map((x) => `<div class="ops-tm"><b>${esc(x.about || x.unit)}</b><span class="faint mono">${esc(x.unit)}</span>
      <span>${esc(t("o_next"))} ${when(x.next)}</span><span class="faint">${esc(t("o_last"))} ${when(x.last)}</span></div>`).join("")}</div>`;
    const R = B.filter((b) => b.review);
    const review = `<div class="card"><table class="ops-t"><thead><tr><th>${esc(t("o_batch"))}</th><th>${esc(t("o_need"))}</th><th>${esc(t("o_assigned"))}</th><th>${esc(t("o_judged"))}</th><th>${esc(t("o_wrong"))}</th><th>${esc(t("o_reviewers"))}</th><th>${esc(t("o_client"))}</th><th></th></tr></thead><tbody>
      ${R.map((b) => { const r = b.review; return `<tr><td><b>${esc(scopeName(b.scope))}</b> <span class="faint">${esc(typeName(b.type))}</span><div class="faint mono">${esc(b.batch_id)}</div></td>
        <td>${r.need_person == null ? "—" : r.need_person}</td><td>${r.assigned}</td>
        <td>${r.judged}${r.need_person ? `<div class="ops-mini"><span style="width:${pct(r.judged, r.need_person)}%"></span></div>` : ""}</td>
        <td>${r.wrong_share == null ? "—" : `${r.wrong} (${Math.round(100 * r.wrong_share)}%)`}</td><td>${esc(r.reviewers.join(", ") || "—")}</td>
        <td>${r.client_judged ? `${r.client_wrong} / ${r.client_judged}` : "—"}</td>
        <td>${r.url ? `<a class="btn" href="${esc(r.url)}" target="_blank" rel="noopener">${esc(t("o_open_review"))} ↗</a>` : `<span class="faint">${esc(t("o_no_review"))}</span>`}</td></tr>`; }).join("")}</tbody></table></div>`;
    if (!B.length) return `${sectionHead(t("ops"), t("ops_sub"))}<div class="empty">${esc(t("o_none"))}</div>`;
    return `${kpi}
      <div class="section">${sectionHead(t("o_sched"), t("o_sched_sub"))}${OPS.timeline(B, D.now)}${table}</div>
      <div class="section cols"><div>${sectionHead(t("o_auto"), t("o_auto_sub"))}${feed}</div><div>${sectionHead(t("o_timers"), t("o_timers_sub"))}${timers}</div></div>
      <div class="section">${sectionHead(t("o_review"), t("o_review_sub"))}${review}</div>`;
  },
  bind() {
    const cb = document.getElementById("opsAuto");
    if (cb) cb.onchange = () => { OPS.onlyAuto = cb.checked; render(); };
  },
};
