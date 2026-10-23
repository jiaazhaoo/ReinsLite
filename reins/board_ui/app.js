"use strict";
/* Reins board: read-only views of /api/state. Views: overview, pipeline, runs, dev, cost, toolbox. No session details. */

const I18N = {
  zh: {
    overview: "概览", pipeline: "流程图谱", runs: "运行", dev: "开发", cost: "成本", toolbox: "工具箱",
    workspace: "工作区", governance: "治理", prodlink: "生产进度（给管理层）", updated: "更新于", live: "实时", sub: "开发与运行的缰绳",
    ov_sub: "现在在跑什么、在改什么、花了多少", running_now: "运行中", developing: "开发中", spend_today: "今日花费",
    lowest_balance: "最低余额", paused: "暂停", need_action: "需要处理", last_release: "最近发布", of_daily: "日上限",
    flow: "流程", flow_sub: "流水线按顺序做哪几步，每步上有多少批次", view_map: "查看图谱", runs_now: "正在运行", dev_now: "正在开发",
    recent_releases: "最近发布", all: "全部", nothing_running: "没有在跑的批次", nothing_dev: "没有进行中的开发",
    pipe_sub: "上面是步骤，下面是分工；点步骤看它的代码版本和工具", frozen: "冻结于", click_stage: "点阶段看详情",
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
    cost_sub: "预算池、各服务商余额与花费", pool: "整机总额度", pool_sub: "两个池合计不能超过的上限（所有会话，经网关）", hour: "近 1 小时", today: "今日",
    week: "本周", open_caps: "活跃批次还可花", pools: "两个预算池", pools_sub: "生产（生产、返工批次）和开发（实验、评测、试跑、训练）各自的额度", pool_production: "生产", pool_development: "开发", no_own_cap: "未设本池上限", t_training: "训练", batch_cap: "单批上限", session_cap: "单会话每日", providers: "服务商",
    balance: "余额", calls: "今日调用", no_key: "未配置 key", no_balance_api: "无余额接口", query_failed: "查询失败",
    by_balance: "按余额变化计", per_call: "按调用数 × 牌价估算", ledger_only: "仅网关记账", by_batch: "本周按批次",
    tb_sub: "工作流用到的每一件东西，都有版本", search: "搜索工具…", shelf: "架子", tool: "工具", does: "做什么",
    detail: "细节", stages: "阶段", unreg_run: "未经 reins 启动的运行", res_title: "资源占用", res_waiting: "等待", res_granted: "已放行", unreg_note: "看不到进度、花费和案件账本",
    theme: "主题", ok: "正常", attention: "注意", action: "需要处理", none: "无",
    s_trained: "自训模型", s_pretrained: "开源模型", s_paid: "付费模型", s_prompt: "提示词", s_code: "代码工具", s_api: "外部接口", s_data: "数据",
    st_running: "运行中", st_paused: "暂停", st_open: "未开始", st_closed: "已关闭", st_done: "完成", st_failed: "失败", st_planned: "未开始", st_skipped: "跳过",
    t_intake: "接收", by_package: "按工作包", by_package_sub: "每个工作包最新一次各类运行：接收 → 试跑 → 生产 → 返工；数字是最后一步完成的案件",
    runs_n: "共 {n} 次", earlier: "更早的 {n} 次", no_packages: "最近两周没有运行",
    t_production: "生产", t_rework: "返工", t_experiment: "实验", t_pilot: "试跑", t_eval: "评测", t_smoke: "冒烟", t_drift: "漂移", t_benchmark_build: "建基准",
    d_ship: "交付", d_ship_with_note: "带说明交付", d_rework: "返工",
    g_active: "在动", g_decide: "要你决定", g_stuck: "停住了", g_stale: "长期没动",
    gs_decide: "暂停了或在等你：看原因，照命令处理", gs_stuck: "两小时到一天没有新结果", gs_stale: "一天以上没有任何动静，大多可以清理",
    k_moving: "正在跑", k_approve: "等你批花费", k_sentinel: "结果看起来不对", k_spend_cap: "花费到上限", k_disk: "磁盘满时暂停",
    k_killed: "进程被杀", k_exited: "报错退出", k_overtime: "超出时间预算", k_stopped: "被手动停止", k_note: "人工暂停",
    k_unreported: "结束了但没回报结果", k_idle: "没有新结果", k_paused: "已暂停",
    k_reboot: "重启后进程没了", k_lost: "进程不在了", k_ended: "进程结束了，批次没收尾",
    jobs: "机器上未登记的任务", jobs_sub: "在跑，但没经过 reins：看不到账本、进度和花费", plus_jobs: "另有 {n} 个未登记任务在跑",
    gpu: "显卡", running_for: "已跑 {n} 小时",
    copy: "复制命令", copied: "已复制", idle_min: "{n} 分钟没动静", idle_h: "{n} 小时没动静", idle_d: "{n} 天没动静",
    more_n: "另外 {n} 个", stale_n: "另有 {n} 个长期没动", cleanup: "清理",
    stale_hint: "这些批次一天以上没有任何动静。先列出来看（不加 --yes 不会改动），确认后加 --yes 关闭；输出文件都保留。",
    all_clear: "没有需要你处理的事", needs_you: "需要你处理", needs_you_sub: "按状态分组，每条附下一步命令",
    active_sub: "两小时内有新结果或进程在跑", of_live: "共 {n} 个未关闭的批次", view_division: "看分工",
    division: "分工", division_sub: "每一步上现在有哪些批次和开发", toolbox_per_stage: "点步骤看这一步的代码版本和工具",
    unreg_dev: "未登记的开发", unreg_dev_sub: "领先 main、但没经过 reins 登记的分支：登记（reins dev adopt 工作区 --about ...）或放弃",
    branch: "分支", worktree: "工作区", ahead: "领先 main", uncommitted: "未提交", last_commit: "最后提交", stale_days: "{d} 天未动",
    plus_unreg: "另有 {n} 个未登记分支",
    all_spend: "全部开销", all_spend_sub: "网关记账 + 导入的历史，和服务商账户对账", total_spent: "累计花费", ledger: "网关记账",
    imported: "导入的历史", unattributed: "未归属", unattr_note: "服务商账户上有、reins 不知道去向的钱", since_gateway: "经 reins 网关的调用",
    before_gateway: "网关之前的记录（日志和缓存）", by_day: "每天", undated: "另有 {v} 没有日期", reconcile: "和服务商账户对账",
    attributed: "已归属", account_usage: "账户总用量", by_purpose: "按用途和模型", by_batch_all: "按批次（全部历史）", purpose: "用途",
    model: "模型", share: "占比", no_batch: "没有批次（缓存里多出的部分）", no_account: "服务商不报总用量", import_files: "导入记录", source: "来源", rows_n: "{n} 行", commits: "{n} 个提交", files: "{n} 个文件",
  },
  en: {
    overview: "Overview", pipeline: "Pipeline", runs: "Runs", dev: "Development", cost: "Cost", toolbox: "Toolbox",
    workspace: "Workspace", governance: "Governance", prodlink: "Production (for managers)", updated: "Updated", live: "Live", sub: "reins for dev and runs",
    ov_sub: "What runs, what changes, what it costs", running_now: "Running", developing: "In development", spend_today: "Spent today",
    lowest_balance: "Lowest balance", paused: "paused", need_action: "need action", last_release: "Last release", of_daily: "daily cap",
    flow: "Pipeline", flow_sub: "the steps of the pipeline, in order, with what stands at each", view_map: "Open map", runs_now: "Running now", dev_now: "In development",
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
    cost_sub: "Budget pool, provider balances and spend", pool: "Machine total", pool_sub: "the limit both pools together stay under (all sessions, through the gateway)", hour: "Last hour", today: "Today",
    week: "This week", open_caps: "Live batches may still spend", pools: "Budget pools", pools_sub: "production (production and rework batches) and development (experiments, evaluations, pilots, training), each with its own limits", pool_production: "Production", pool_development: "Development", no_own_cap: "no pool cap set", t_training: "training", batch_cap: "Per batch", session_cap: "Per session per day", providers: "Providers",
    balance: "Balance", calls: "Calls today", no_key: "no key", no_balance_api: "no balance API", query_failed: "query failed",
    by_balance: "from balance changes", per_call: "calls × list price", ledger_only: "gateway ledger only", by_batch: "This week by batch",
    tb_sub: "Everything the workflow uses, each with a version", search: "Search tools…", shelf: "Shelf", tool: "Tool", does: "Does",
    detail: "Detail", stages: "Stages", unreg_run: "Run not started through reins", res_title: "Resources", res_waiting: "waiting", res_granted: "granted", unreg_note: "no progress, spend or case ledger",
    theme: "Theme", ok: "OK", attention: "Attention", action: "Needs action", none: "none",
    s_trained: "Trained", s_pretrained: "Pretrained", s_paid: "Paid models", s_prompt: "Prompts", s_code: "Code", s_api: "APIs", s_data: "Data",
    st_running: "running", st_paused: "paused", st_open: "open", st_closed: "closed", st_done: "done", st_failed: "failed", st_planned: "planned", st_skipped: "skipped",
    t_intake: "intake", by_package: "By work package", by_package_sub: "the newest run of each kind per work package: intake → pilot → production → rework; numbers are cases done in the last step",
    runs_n: "{n} runs", earlier: "{n} earlier", no_packages: "no runs in the last two weeks",
    t_production: "production", t_rework: "rework", t_experiment: "experiment", t_pilot: "pilot", t_eval: "eval", t_smoke: "smoke", t_drift: "drift", t_benchmark_build: "benchmark build",
    d_ship: "ship", d_ship_with_note: "ship with note", d_rework: "rework",
    g_active: "Moving", g_decide: "Needs you", g_stuck: "Stuck", g_stale: "Long idle",
    gs_decide: "paused or waiting for you: read the reason, run the command", gs_stuck: "no new result for 2 h to a day", gs_stale: "no sign of life for over a day; most can be closed",
    k_moving: "running", k_approve: "awaiting a spend cap", k_sentinel: "results look wrong", k_spend_cap: "spend cap reached", k_disk: "paused on a full disk",
    k_killed: "process killed", k_exited: "exited with an error", k_overtime: "over its time budget", k_stopped: "stopped by hand", k_note: "paused by a person",
    k_unreported: "ended without reporting results", k_idle: "no new results", k_paused: "paused",
    k_reboot: "process gone after a reboot", k_lost: "process gone", k_ended: "process ended, batch not wrapped up",
    jobs: "Unregistered jobs on this machine", jobs_sub: "running, but not through reins: no ledger, progress or spend", plus_jobs: "+{n} unregistered job(s) running",
    gpu: "GPU", running_for: "running {n} h",
    copy: "copy command", copied: "copied", idle_min: "idle {n} min", idle_h: "idle {n} h", idle_d: "idle {n} days",
    more_n: "{n} more", stale_n: "{n} more long idle", cleanup: "clean up",
    stale_hint: "These batches show no sign of life for over a day. The command lists them first (nothing changes without --yes); add --yes to close them. Outputs are kept.",
    all_clear: "Nothing needs you", needs_you: "Needs you", needs_you_sub: "grouped by state, each with its next command",
    active_sub: "a new result or a live process within 2 h", of_live: "of {n} open batches", view_division: "Who is where",
    division: "Division of work", division_sub: "which batches and changes stand at each step", toolbox_per_stage: "click a step for its code version and tools",
    unreg_dev: "Unregistered development", unreg_dev_sub: "Branches ahead of main that reins never registered: adopt them (reins dev adopt WORKTREE --about ...) or abandon them",
    branch: "Branch", worktree: "Worktree", ahead: "Ahead of main", uncommitted: "Uncommitted", last_commit: "Last commit", stale_days: "idle {d} days",
    plus_unreg: "+{n} unregistered branch(es)",
    all_spend: "All spend", all_spend_sub: "Gateway ledger + imported history, reconciled with provider accounts", total_spent: "Spent in total",
    ledger: "Gateway ledger", imported: "Imported history", unattributed: "Unattributed", unattr_note: "on provider accounts, origin unknown to reins",
    since_gateway: "calls through the reins gateway", before_gateway: "before the gateway (logs and caches)", by_day: "By day",
    undated: "plus {v} without a date", reconcile: "Reconciled with provider accounts", attributed: "Attributed", account_usage: "Account usage",
    by_purpose: "By purpose and model", by_batch_all: "By batch (all history)", purpose: "Purpose", model: "Model", share: "Share", no_batch: "no batch (cache beyond the logs)",
    no_account: "provider reports no total", import_files: "Imports", source: "Source", rows_n: "{n} rows", commits: "{n} commits", files: "{n} files",
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
  copy: '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/>',
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
const typeName = (ty) => { const v = t("t_" + ty); return v === "t_" + ty ? ty : v; };
const typeBadge = (ty) => badge(typeName(ty), "outline");
const ACCEPTED_TYPES = ["production", "rework"];          // only deliveries are accepted by a person
const scopeName = (sc) => String(sc || "").replace(/-(wp[\w-]+)$/i, " $1").replace(/^./, (c) => c.toUpperCase());
function outcomeBar(so, n) {                              // per stage: where the batch's cases went
  return `<div class="obar">${(so || []).map((x) => {
    const tot = Math.max(n || 0, x.done + x.skipped + x.failed, 1);
    const seg = (v, c) => (v ? `<i class="${c}" style="width:${(100 * v / tot).toFixed(1)}%"></i>` : "");
    const lbl = [x.done ? `${t("done")} ${x.done}` : "", x.skipped ? `${t("skipped")} ${x.skipped}` : "", x.failed ? `${t("failed")} ${x.failed}` : ""].filter(Boolean).join(" · ") || t("st_" + x.status);
    return `<div class="orow" title="${esc(x.stage + ": " + lbl)}"><span class="ostage">${esc(x.stage)}</span><span class="otrack">${seg(x.done, "g")}${seg(x.skipped, "s")}${seg(x.failed, "r")}</span><span class="olbl">${esc(lbl)}</span></div>`;
  }).join("")}</div>`;
}
// a batch's short name: its alias if it has one, else "<work package> · <kind>"; the purpose is shown under it
const runTitle = (r) => (r.named || !r.scope ? r.title : scopeName(r.scope) + " · " + typeName(r.type));
const MAIN_TYPES = ["intake", "pilot", "production", "rework"];   // the delivery chain; the rest is development
function packagesPanel() {
  const P = (S.packages || []).map((p) => ({ ...p, main: p.types.filter((x) => MAIN_TYPES.includes(x.type)), dev: p.types.filter((x) => !MAIN_TYPES.includes(x.type)) }))
    .sort((a, b) => (b.main.length > 0) - (a.main.length > 0));
  if (!P.length) return `<div class="card empty">${esc(t("no_packages"))}</div>`;
  const dot = (st) => (st === "running" ? "green" : st === "paused" || st === "open" ? "amber" : st === "failed" ? "red" : st === "done" ? "blue" : "");
  const step = (x) => `<span class="pkg-step" title="${esc(x.batch_id)}"><span class="dot ${dot(x.status)}"></span><b>${esc(typeName(x.type))}</b>
    <span class="faint">${esc(t("st_" + x.status))}${x.n_cases ? ` · ${x.done}/${x.n_cases}` : ""} · ${when(x.at)}${x.repeats > 1 ? ` · ×${x.repeats}` : ""}</span></span>`;
  return `<div class="card pkgs">${P.map((p) => `<div class="pkg-row"><a class="pkg-name" href="#/runs"><b>${esc(scopeName(p.scope))}</b><span class="faint">${esc(p.project || "")} · ${esc(t("runs_n", { n: p.runs }))}</span></a>
    <div><div class="pkg-steps">${p.main.map((x, i) => `${i ? '<span class="pkg-arrow">→</span>' : ""}${step(x)}`).join("") || `<span class="faint" style="font-size:12.5px">—</span>`}</div>
    ${p.dev.length ? `<div class="pkg-dev">${p.dev.map((x) => `<span title="${esc(x.batch_id)}"><span class="dot ${dot(x.status)}"></span>${esc(typeName(x.type))}${x.repeats > 1 ? ` ×${x.repeats}` : ""}</span>`).join("")}</div>` : ""}</div></div>`).join("")}</div>`;
}
const HEALTH = { green: ["ok", "green"], yellow: ["attention", "amber"], red: ["action", "red"] };
const sectionHead = (title, sub, more) => `<div class="section-head"><h2>${esc(title)}</h2>${sub ? `<span class="sub">${esc(sub)}</span>` : ""}${more || ""}</div>`;
const meterCls = (p) => (p >= 90 ? "red" : p >= 70 ? "amber" : "");

function shelfCounts(tools) {
  const c = {};
  tools.forEach((x) => (c[x.group] = (c[x.group] || 0) + 1));
  return SHELVES.filter((s) => c[s]).map((s) => `<span class="shelf-n s-${s}" title="${esc(t("s_" + s))}">${esc(t("s_" + s))} ${c[s]}</span>`).join("");
}

/* ---------- the flow: a step diagram (what the pipeline does, in order) ---------- */
const shortVer = (v) => String(v || "").replace(/^[a-z][a-z0-9_]*-/, "").replace(/-(\d{4})(\d{2})(\d{2})-(\d+)$/, " · $2-$3");
const GROUPS = ["active", "decide", "stuck", "stale"];
function stageLoad(st) {                         // batches standing at this stage, by group, and changes in progress
  const at = S.running.filter((r) => r.stage === st.name);
  const by = {}; GROUPS.forEach((g) => (by[g] = at.filter((r) => r.group === g)));
  return { by, dev: st.developing.length };
}
function stepNode(st, i, pi, big) {
  const L = stageLoad(st);
  const busy = L.by.active.length > 0;
  const ver = st.code.length ? st.code[0].version : "";
  const counters = [
    L.by.active.length ? `<span class="cnt green" title="${esc(t("g_active"))}"><span class="dot green"></span>${L.by.active.length}</span>` : "",
    L.by.decide.length ? `<span class="cnt red" title="${esc(t("g_decide"))}"><span class="dot red"></span>${L.by.decide.length}</span>` : "",
    L.by.stuck.length ? `<span class="cnt amber" title="${esc(t("g_stuck"))}"><span class="dot amber"></span>${L.by.stuck.length}</span>` : "",
    L.dev ? `<span class="cnt violet" title="${esc(t("in_dev"))}">✎ ${L.dev}</span>` : "",
  ].join("");
  return `<button class="snode ${busy ? "busy" : ""}" data-stage="${pi}:${i}">
    <div class="sdot">${big ? crab(st.mascot, "crab") : `<span>${i + 1}</span>`}</div>
    <div class="slabel"><b>${esc(st.title)}</b><span>${esc(st.steps ? t("step_n", { n: st.steps }) : st.name)}</span></div>
    ${big && ver ? `<div class="sver" title="${esc(ver + " — " + st.code[0].about)}">${esc(shortVer(ver))}</div>` : ""}
    <div class="scnt">${counters || `<span class="faint">—</span>`}</div>
  </button>`;
}
const stepFlow = (p, pi, big) => `<div class="steps-wrap"><div class="steps ${big ? "big" : ""}" style="--n:${p.stages.length}">${p.stages.map((st, i) => stepNode(st, i, pi, big)).join("")}</div>
  <div class="steps-legend"><span><span class="dot green"></span>${esc(t("g_active"))}</span><span><span class="dot red"></span>${esc(t("g_decide"))}</span><span><span class="dot amber"></span>${esc(t("g_stuck"))}</span><span>✎ ${esc(t("in_dev"))}</span><span class="faint">${esc(t("click_stage"))}</span></div></div>`;

/* ---------- the division of work: who stands at which step ---------- */
function divisionBoard(p) {
  const col = (st) => {
    const L = stageLoad(st);
    const row = (r) => `<a class="drow" href="#/runs" title="${esc(r.batch_id)}"><span class="dot ${GCOLOR[r.group]}"></span><span class="dt">${esc(r.title)}</span><span class="dm">${r.group === "active" ? r.pct + "%" : esc(kindText(r))}</span></a>`;
    const shown = [...L.by.active, ...L.by.decide, ...L.by.stuck];
    const devs = st.developing.map((d) => `<a class="drow" href="#/dev" title="${esc((d.version || "") + " — " + (d.about || ""))}"><span class="dot violet"></span><span class="dt">${esc(shortVer(d.version) || d.about || "")}</span></a>`).join("");
    return `<div class="dcol"><div class="dhead"><b>${esc(st.title)}</b><span class="faint">${esc(st.name)}</span></div>
      ${shown.slice(0, 6).map(row).join("")}${shown.length > 6 ? `<div class="dmore">${esc(t("more_n", { n: shown.length - 6 }))}</div>` : ""}
      ${L.by.stale.length ? `<div class="dmore">${esc(t("stale_n", { n: L.by.stale.length }))}</div>` : ""}
      ${devs ? `<div class="dsub">${esc(t("in_dev"))}</div>${devs}` : ""}
      ${!shown.length && !devs && !L.by.stale.length ? `<div class="dmore">${esc(t("none"))}</div>` : ""}</div>`;
  };
  return `<div class="division-wrap"><div class="division" style="--n:${p.stages.length}">${p.stages.map(col).join("")}</div></div>`;
}

/* tables: fixed columns that wrap; below 720 px of room each row becomes a card (no sideways scrolling anywhere) */
function tbl(cols, rows, empty = "—", small = false) {
  if (!rows.length) return `<div class="card empty">${esc(empty)}</div>`;
  return `<div class="card tbl ${small ? "tbl-s" : ""}"><table><colgroup>${cols.map((c) => `<col style="width:${c.w || "auto"}">`).join("")}</colgroup>
    <thead><tr>${cols.map((c) => `<th class="${c.cls || ""}">${esc(c.h)}</th>`).join("")}</tr></thead>
    <tbody>${rows.map((r) => `<tr>${r.map((cell, i) => cell && cell.span ? `<td class="span" colspan="${cell.span}"><div class="cell">${cell.html}</div></td>`
      : `<td class="${cols[i].cls || ""}" data-label="${esc(cols[i].h)}"><div class="cell">${cell}</div></td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
}

function stepper(r) {
  return `<div class="stepper">${r.stages.map((s) => {
    const p = s.status === "done" || s.status === "skipped" ? 100 : pct(s.done, r.n_cases);
    return `<div class="step ${s.status}"><div class="bar"><i style="width:${p}%"></i></div>
      <div class="name"><span>${esc(s.stage)}</span><span>${s.status === "running" ? p + "%" : s.status === "done" ? "✓" : ""}</span></div></div>`;
  }).join("")}</div>`;
}

function runNotes(r) {
  const out = [];
  if (r.status_reason && r.status === "paused") out.push(`<div class="note warn">⏸ ${esc(r.status_reason)}</div>`);
  r.needs.forEach((n) => out.push(`<div class="note bad">⚠ ${esc(n)}</div>`));
  r.issues.forEach((i) => out.push(`<div class="note ${i.status === "fixed" ? "ok" : "bad"}">#${i.id} ${esc(t(i.status === "fixed" ? "issue_fixed" : i.status === "in_progress" ? "issue_fixing" : "issue_todo"))} · ${i.n} ${esc(t("cases"))} · ${esc(i.symptom)}${i.version ? " → " + esc(i.version) : ""}</div>`));
  if (r.mixed) out.push(`<div class="note warn">${esc(t("mixed"))}</div>`);
  if (r.adopted) out.push(`<div class="note warn">${esc(t("adopted"))}</div>`);
  return out.length ? `<div class="notes">${out.join("")}</div>` : "";
}

function runCard(r) {
  const [hk, hc] = HEALTH[r.health] || HEALTH.green;
  const same = (r.purpose || "").trim() === runTitle(r).trim();
  const stat = (k, v) => `<span><span class="k">${esc(k)}</span> ${v}</span>`;
  return `<div class="card run compact">
    <div class="run-head"><div style="min-width:0"><h3 title="${esc(r.purpose)}">${esc(runTitle(r))}</h3><div class="id">${esc(r.batch_id)}</div></div>
      <div class="right">${r.status !== "running" ? statusBadge(r.status) : ""}${typeBadge(r.type)}${r.health !== "green" ? badge(t(hk), hc) : ""}</div></div>
    ${same ? "" : `<div class="purpose">${esc(r.purpose)}</div>`}
    ${stepper(r)}
    <div class="statline">
      ${stat(t("cases"), `<b>${r.n_cases}</b>${r.n_skipped ? ` <span class="faint">(${r.n_skipped} ${esc(t("skipped"))})</span>` : ""}`)}
      ${stat(t("progress"), `<b>${r.adopted ? "—" : esc(r.progress)}</b> <span class="faint">${esc(r.stage)} ${esc(r.stage_ord)}</span>`)}
      ${r.eta && r.eta !== "-" ? stat(t("eta"), `<b>${esc(r.eta)}</b>`) : ""}
      ${stat(t("spend"), `<b>${usd(r.spent)}</b> <span class="faint">/ ${usd(r.cap)}</span>`)}
      ${stat(t("time"), `<b>${r.time_used_h} h</b>${r.time_budget_h ? ` <span class="faint">/ ${r.time_budget_h} h</span>` : ""}`)}
    </div>
    ${runNotes(r)}
    ${r.probe.length ? `<details class="probe"><summary>${esc(t("probe"))}</summary><pre>${esc(r.probe.join("\n"))}</pre></details>` : ""}
  </div>`;
}

/* ---------- what needs a person: grouped by state, each with the command that does it ---------- */
const GCOLOR = { active: "green", decide: "red", stuck: "amber", stale: "" };
const kindText = (r) => t("k_" + r.kind);
function copyBtn(cmd) { return `<button class="copy" data-copy="${esc(cmd)}" title="${esc(t("copy"))}">${icon("copy")}</button>`; }
const idleText = (r) => (r.idle_h == null ? "" : r.idle_h < 1 ? t("idle_min", { n: Math.round(r.idle_h * 60) }) : r.idle_h < 48 ? t("idle_h", { n: Math.round(r.idle_h) }) : t("idle_d", { n: Math.round(r.idle_h / 24) }));
function todoRow(r) {
  return `<div class="todo">
    <span class="tk ${GCOLOR[r.group]}">${esc(kindText(r))}</span>
    <div class="tm"><div class="tt"><b title="${esc(r.batch_id)}">${esc(r.title)}</b><span class="faint">${[t("t_" + r.type), r.stage !== "-" ? r.stage : "", idleText(r)].filter(Boolean).map(esc).join(" · ")}</span></div>
      ${r.status_reason ? `<div class="tr" title="${esc(r.status_reason)}">${esc(r.status_reason)}</div>` : ""}
      <div class="tc"><code>${esc(r.cmd)}</code>${copyBtn(r.cmd)}</div></div></div>`;
}
function todoPanel(full) {
  const g = {}; GROUPS.forEach((k) => (g[k] = S.running.filter((r) => r.group === k)));
  const order = (a, b) => (b.type === "production") - (a.type === "production") || (b.type === "rework") - (a.type === "rework") || (a.idle_h || 0) - (b.idle_h || 0);
  const sec = (k, rows, open) => rows.length ? `<details class="tgroup" data-k="${k}" ${open ? "open" : ""}><summary><span class="dot ${GCOLOR[k]}"></span><b>${esc(t("g_" + k))}</b><span class="tn">${rows.length}</span><span class="faint">${esc(t("gs_" + k))}</span></summary>
      ${rows.sort(order).slice(0, full ? 999 : 6).map(todoRow).join("")}${!full && rows.length > 6 ? `<a class="tall" href="#/runs">${esc(t("more_n", { n: rows.length - 6 }))} →</a>` : ""}</details>` : "";
  const st = g.stale;
  const types = {}; st.forEach((r) => (types[r.type] = (types[r.type] || 0) + 1));
  const staleCmd = "reins batch abandon --stale 24 --why '...'";
  const stale = st.length ? `<details class="tgroup" data-k="stale"><summary><span class="dot"></span><b>${esc(t("g_stale"))}</b><span class="tn">${st.length}</span>
      <span class="faint">${Object.entries(types).map(([k, n]) => `${esc(t("t_" + k))} ${n}`).join(" · ")}</span></summary>
      <div class="todo"><span class="tk">${esc(t("cleanup"))}</span><div class="tm"><div class="tr">${esc(t("stale_hint"))}</div>
        <div class="tc"><code>${esc(staleCmd)}</code>${copyBtn(staleCmd)}</div></div></div>
      ${st.sort((a, b) => (b.idle_h || 0) - (a.idle_h || 0)).map((r) => `<div class="srow"><span class="mono">${esc(r.batch_id)}</span><span class="faint">${esc(idleText(r))}</span><span class="faint">${esc(r.status_reason || "")}</span></div>`).join("")}</details>` : "";
  const sys = (S.system || []).map((n) => `<div class="alert ${n.level}">${icon("alert")}<div style="min-width:0"><b>${esc(n.title)}</b><div class="body">${esc(n.body)}</div></div><span class="when">${when(n.at)}</span></div>`).join("");
  const unreg = (S.unregistered_runs || []).map((u) => `<div class="alert warn">${icon("alert")}<div><b>${esc(t("unreg_run"))} · ${esc(u.kind)}</b><div class="body">${esc(u.what)} — ${esc(t("unreg_note"))}</div></div><span class="when">${when(u.at)}</span></div>`).join("");
  const body = sec("decide", g.decide, true) + sec("stuck", g.stuck, full) + stale;
  return (sys || unreg ? `<div class="alerts">${sys}${unreg}</div>` : "") +
    (body ? `<div class="card todos">${body}</div>` : `<div class="card empty">${esc(t("all_clear"))}</div>`);
}
function jobsHtml() {
  const J = S.machine_jobs || [];
  if (!J.length) return "";
  return `<div class="card mini jobs">${J.map((j) => `<div class="mini-row"><span class="t mono" title="${esc(j.cmd)}">${esc(j.cmd)}</span>
    <span class="faint nowrap" style="font-size:12px">pid ${j.pid}</span>
    <span class="s">${esc(t("running_for", { n: j.elapsed_h }))} · CPU ${j.cpu}% · RAM ${j.rss_gb} GB${j.gpu_mb ? ` · ${esc(t("gpu"))} ${j.gpu_mb} MB` : ""}</span></div>`).join("")}</div>`;
}
function resourcesLine() {
  const R = S.resources;
  if (!R || !R.claims || !R.claims.length) return "";
  const w = R.claims.filter((c) => c.state === "waiting").length;
  return `<div class="faint" style="font-size:12.5px;margin:-6px 0 14px">${esc(t("res_title"))}: RAM ${R.held.mem} / ${R.budget.mem.toFixed(0)} GB${R.budget.gpu ? ` · GPU ${R.held.gpu} / ${R.budget.gpu.toFixed(0)} GB` : ""}${w ? ` · ${w} ${esc(t("res_waiting"))}` : ""}</div>`;
}

/* ---------- views ---------- */
const VIEWS = {
  overview() {
    const n = (g) => S.running.filter((r) => r.group === g).length;
    const P = S.pool, L = P.limits;
    const lastRel = S.dev_history.find((v) => v.status === "released");
    const kpis = `<div class="grid g4">
      <a class="card kpi" href="#/runs"><div class="label"><span class="dot ${n("active") ? "pulse" : ""}" style="${n("active") ? "background:var(--green)" : ""}"></span>${esc(t("g_active"))}</div>
        <div class="value">${n("active")}</div><div class="foot">${(S.machine_jobs || []).length ? `<span style="color:var(--amber)">${esc(t("plus_jobs", { n: S.machine_jobs.length }))}</span>` : esc(t("of_live", { n: S.running.length }))}</div></a>
      <a class="card kpi ${n("decide") ? "hot" : ""}" href="#/runs"><div class="label"><span class="dot red"></span>${esc(t("g_decide"))}</div>
        <div class="value">${n("decide")}</div><div class="foot">${esc(t("g_stuck"))} ${n("stuck")} · ${esc(t("g_stale"))} ${n("stale")}</div></a>
      <a class="card kpi" href="#/dev"><div class="label"><span class="dot violet"></span>${esc(t("developing"))}</div><div class="value">${S.in_progress.length}</div>
        <div class="foot">${(S.unregistered_dev || []).length ? `<span style="color:var(--amber)">${esc(t("plus_unreg", { n: S.unregistered_dev.length }))}</span>` : `${esc(t("last_release"))} ${lastRel ? when(lastRel.released_at) : "—"}`}</div></a>
      <a class="card kpi" href="#/cost"><div class="label"><span class="dot accent"></span>${esc(t("spend_today"))}</div><div class="value">${usd(P.today)}<small>/ ${usd(L.daily_cap)}</small></div>
        <div class="meter"><i class="${meterCls(pct(P.today, L.daily_cap))}" style="width:${pct(P.today, L.daily_cap)}%"></i></div></a></div>`;
    const flows = S.pipelines.map((p, pi) => `<div class="section">${sectionHead(t("flow") + (S.pipelines.length > 1 ? " · " + p.project : ""), t("flow_sub"), `<a class="more" href="#/pipeline">${esc(t("view_division"))} →</a>`)}${stepFlow(p, pi, false)}</div>`).join("");
    const act = S.running.filter((r) => r.group === "active");
    const runs = act.length ? `<div class="card mini">${act.map((r) => `<a class="mini-row" href="#/runs">
        <span class="t">${esc(runTitle(r))}</span><span class="faint nowrap" style="font-size:12px">${esc(r.stage)} ${esc(r.stage_ord)}</span>
        <span class="s">${esc(t("t_" + r.type))} · ${r.adopted ? esc(t("adopted")) : esc(r.progress)} · ${usd(r.spent)} / ${usd(r.cap)}${r.eta && r.eta !== "-" ? " · " + esc(t("eta")) + " " + esc(r.eta) : ""}</span>
        <div class="meter"><i style="width:${r.pct}%"></i></div></a>`).join("")}</div>` : `<div class="card empty">${esc(t("nothing_running"))}</div>`;
    const devs = S.in_progress.length ? `<div class="card mini">${S.in_progress.map((d) => `<a class="mini-row" href="#/dev"><span class="t">${esc(d.module)}</span>
        <span>${badge(d.gate_status === "green" ? t("gate") + " ✓" : d.registered ? t("candidate") : t("unregistered"), d.gate_status === "green" ? "green" : "amber")}</span>
        <span class="s">${esc(d.about || "")}</span></a>`).join("")}</div>` : `<div class="card empty">${esc(t("nothing_dev"))}</div>`;
    return `<div class="page-head"><div><h1>${esc(t("overview"))}</h1><p>${esc(t("ov_sub"))}</p></div></div>
      ${kpis}
      <div class="section">${sectionHead(t("needs_you"), t("needs_you_sub"), `<a class="more" href="#/runs">${esc(t("all"))} →</a>`)}${todoPanel(false)}</div>
      <div class="section">${sectionHead(t("by_package"), t("by_package_sub"), `<a class="more" href="#/runs">${esc(t("all"))} →</a>`)}${packagesPanel()}</div>
      ${flows}
      <div class="section cols"><div>${sectionHead(t("g_active"), t("active_sub"), `<a class="more" href="#/runs">${esc(t("all"))} →</a>`)}${runs}
          ${(S.machine_jobs || []).length ? `<div class="section" style="margin-top:22px">${sectionHead(t("jobs"), t("jobs_sub"))}${jobsHtml()}</div>` : ""}</div>
        <div>${sectionHead(t("dev_now"), "", `<a class="more" href="#/dev">${esc(t("all"))} →</a>`)}${devs}</div></div>`;
  },
  pipeline() {
    const legend = SHELVES.map((s) => `<span class="shelf-n s-${s}">${esc(t("s_" + s))}</span>`).join("");
    return `<div class="page-head"><div><h1>${esc(t("pipeline"))}</h1><p>${esc(t("pipe_sub"))}</p></div></div>
      ${S.pipelines.map((p, pi) => `<div class="section" style="margin-top:${pi ? 34 : 0}px">
        <div class="section-head"><h2>${esc(t("flow"))}${S.pipelines.length > 1 ? " · " + esc(p.project) : ""}</h2><span class="sub">${esc(p.about || "")}</span>
          <span class="more" style="color:var(--muted)"><span class="chip">${esc(p.workflow)}</span> · ${esc(t("frozen"))} ${when(p.frozen)}</span></div>
        ${stepFlow(p, pi, true)}
        <div class="section">${sectionHead(t("division"), t("division_sub"))}${divisionBoard(p)}</div>
        <div class="filters" style="margin-top:14px"><span class="faint" style="font-size:12.5px">${esc(t("toolbox_per_stage"))}</span><span style="margin-left:auto;display:flex;gap:5px;flex-wrap:wrap">${legend}</span></div>
      </div>`).join("") || `<div class="card empty">workflow freeze NAME</div>`}`;
  },
  runs() {
    const groups = [];
    const seen = {};
    S.run_history.forEach((h) => { const k = h.scope + "|" + h.type; if (!seen[k]) { seen[k] = { head: h, more: [] }; groups.push(seen[k]); } else seen[k].more.push(h); });
    const row = (h, sub) => [when(h.closed),
      `<b>${esc(sub ? h.batch_id : scopeName(h.scope) + " · " + typeName(h.type))}</b><div class="sub ellip" title="${esc(h.purpose)}">${esc(sub ? h.purpose : h.batch_id + " — " + h.purpose)}</div>`,
      typeBadge(h.type), String(h.n_cases), outcomeBar(h.stage_outcomes, h.n_cases),
      usd(h.spent), h.hours != null ? h.hours + " h" : "—",
      h.decision ? badge(t("d_" + h.decision), "green") : ACCEPTED_TYPES.includes(h.type) ? `<span class="faint">${esc(t("not_accepted"))}</span>` : `<span class="faint">—</span>`];
    const rows = [];
    groups.forEach((g) => {
      rows.push(row(g.head, false));
      if (g.more.length) rows.push([{ html: `<details class="earlier"><summary>${esc(t("earlier", { n: g.more.length }))}</summary>${g.more.map((h) => `<div class="earlier-row">${when(h.closed)} · <span class="mono">${esc(h.batch_id)}</span> · ${esc(t("st_" + h.status))} · ${usd(h.spent)}</div>`).join("")}</details>`, span: 8 }]);
    });
    const hist = tbl([{ h: t("ended"), w: "10%" }, { h: t("batch"), w: "26%" }, { h: t("type"), w: "7%" }, { h: t("cases"), cls: "num", w: "6%" },
        { h: t("result"), w: "31%" }, { h: t("spend"), cls: "num", w: "7%" }, { h: t("time"), cls: "num", w: "5%" }, { h: t("acceptance"), w: "8%" }], rows);
    const act = S.running.filter((r) => r.group === "active");
    return `<div class="page-head"><div><h1>${esc(t("runs"))}</h1><p>${esc(t("runs_sub"))}</p></div></div>
      <div class="section" style="margin-top:0">${sectionHead(t("g_active"), t("active_sub"))}${resourcesLine()}
        ${act.length ? `<div class="runs-grid">${act.map(runCard).join("")}</div>` : `<div class="card empty">${esc(t("nothing_running"))}</div>`}</div>
      ${(S.machine_jobs || []).length ? `<div class="section">${sectionHead(t("jobs"), t("jobs_sub"))}${jobsHtml()}</div>` : ""}
      <div class="section">${sectionHead(t("needs_you"), t("needs_you_sub"))}${todoPanel(true)}</div>
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
    const rel = tbl([{ h: t("when"), w: "11%" }, { h: t("module"), w: "11%" }, { h: t("version"), w: "24%" }, { h: t("what"), w: "34%" }, { h: t("gate"), w: "20%" }],
      S.dev_history.map((v) => [when(v.released_at), `<b>${esc(v.module)}</b>`, `<span class="chip">${esc(v.version)}</span>${v.status !== "released" ? " " + badge(v.status, "red") : ""}`,
        esc(v.about) + (v.from_batch ? `<div class="sub">${esc(t("from_batch"))} ${esc(v.from_batch)}</div>` : "") + (v.why_out ? `<div class="sub">${esc(v.why_out)}</div>` : ""),
        `<span class="muted" style="font-size:12.5px">${esc(v.gate)}</span>`]));
    return `<div class="page-head"><div><h1>${esc(t("dev"))}</h1><p>${esc(t("dev_sub"))}</p></div></div>
      ${sectionHead(t("dev_now"))}${cards}
      ${(S.unregistered_dev || []).length ? `<div class="section">${sectionHead(t("unreg_dev"), t("unreg_dev_sub"))}${tbl(
        [{ h: t("branch"), w: "24%" }, { h: t("worktree"), w: "22%" }, { h: t("ahead"), cls: "num", w: "10%" }, { h: t("uncommitted"), cls: "num", w: "9%" },
         { h: t("last_commit"), w: "35%" }],
        S.unregistered_dev.map((u) => [`<span class="chip">${esc(u.branch)}</span>`, u.worktree ? `<span class="mono">${esc(u.worktree)}</span>` : '<span class="faint">—</span>',
          esc(t("commits", { n: u.ahead })), u.dirty ? `<span style="color:var(--amber)">${esc(t("files", { n: u.dirty }))}</span>` : '<span class="faint">0</span>',
          `${when(u.last)} ${u.stale ? badge(t("stale_days", { d: Math.floor(u.age_days) }), "amber") : ""}<div class="sub">${esc(u.subject)}</div>`]))}</div>` : ""}
      <div class="section">${sectionHead(t("releases"), t("releases_sub"))}${rel}</div>`;
  },
  cost() {
    const P = S.pool, L = P.limits;
    const row = (label, v, cap) => `<div class="pool-row"><span class="muted">${esc(label)}</span><div class="meter"><i class="${meterCls(pct(v, cap))}" style="width:${pct(v, cap)}%"></i></div><span class="num"><b>${usd(v)}</b> <span class="faint">/ ${usd(cap)}</span></span></div>`;
    const pool = `<div class="card pad">${row(t("hour"), P.hour, L.hourly_cap)}${row(t("today"), P.today, L.daily_cap)}${row(t("week"), P.week, L.weekly_cap)}
      <div class="muted" style="font-size:12.5px;padding-top:12px;border-top:1px solid var(--border);display:flex;gap:18px;flex-wrap:wrap">
      <span>${esc(t("open_caps"))} <b style="color:var(--text)">${usd(P.open_caps)}</b></span><span>${esc(t("batch_cap"))} ≤ ${usd(L.max_batch_cap)}</span><span>${esc(t("session_cap"))} ≤ ${usd(L.per_session_daily_cap)}</span></div></div>`;
    const pcard = (q) => {                       // one budget pool: production, development (limits unset = only the machine's apply)
      const lim = q.limits || {}, r = (label, v, cap) => cap != null ? row(label, v, cap)
        : `<div class="pool-row"><span class="muted">${esc(label)}</span><div class="meter"></div><span class="num"><b>${usd(v)}</b> <span class="faint">${esc(t("no_own_cap"))}</span></span></div>`;
      return `<div class="card pad"><div class="section-head" style="margin:0 0 8px"><h2 style="font-size:14px">${esc(t("pool_" + q.pool) || q.pool)}</h2>
        <span class="sub">${esc((q.types || []).map((x) => t("t_" + x)).join(" · "))}</span></div>
        ${r(t("today"), q.today, lim.daily_cap)}${r(t("week"), q.week, lim.weekly_cap)}
        <div class="muted" style="font-size:12.5px;padding-top:10px;display:flex;gap:16px;flex-wrap:wrap">
        <span>${esc(t("open_caps"))} <b style="color:var(--text)">${usd(q.held)}</b></span>${lim.max_batch_cap != null ? `<span>${esc(t("batch_cap"))} ≤ ${usd(lim.max_batch_cap)}</span>` : ""}</div></div>`;
    };
    const twoPools = `<div class="grid g2">${(S.pools || []).filter((q) => q.pool !== "machine").map(pcard).join("")}</div>`;
    const prov = `<div class="grid g3">${S.cost.map((c) => `<div class="card pad provider"><div class="name">${esc(c.provider)}
        ${!c.key_present ? badge(t("no_key"), "red") : !c.has_endpoint ? badge(t("no_balance_api")) : c.error ? badge(t("query_failed"), "red") : badge(t("live"), "green", "green")}</div>
        <div class="bal ${c.balance != null && c.balance < 10 ? "low" : ""}">${c.has_endpoint ? usd(c.balance) : "—"}</div>
        <div class="faint" style="font-size:12px">${esc(c.spend_source === "balance" ? t("by_balance") : c.per_call ? t("per_call") : t("ledger_only"))}${c.balance_at ? " · " + when(c.balance_at) : ""}</div>
        <div class="rows"><div>${esc(t("today"))}<b>${usd(c.spent_today)}</b></div><div>${esc(t("week"))}<b>${usd(c.spent_week)}</b></div><div>${esc(t("calls"))}<b>${c.calls_today}</b></div></div></div>`).join("")}</div>`;
    const byb = {};
    S.cost.forEach((c) => c.by_batch.forEach((b) => { byb[b.batch_id] = byb[b.batch_id] || {}; byb[b.batch_id][c.provider] = b.usd; }));
    const provs = S.cost.map((c) => c.provider);
    const rows = Object.entries(byb).sort((a, b) => Object.values(b[1]).reduce((x, y) => x + y, 0) - Object.values(a[1]).reduce((x, y) => x + y, 0));
    const table = tbl([{ h: t("batch"), w: "40%" }].concat(provs.map((p) => ({ h: p, cls: "num" }))).concat([{ h: "Σ", cls: "num" }]),
      rows.map(([b, v]) => [`<span class="mono">${esc(b)}</span>`].concat(provs.map((p) => (v[p] != null ? usd(v[p]) : '<span class="faint">—</span>')))
        .concat([`<b>${usd(Object.values(v).reduce((x, y) => x + y, 0))}</b>`])));
    return `<div class="page-head"><div><h1>${esc(t("cost"))}</h1><p>${esc(t("cost_sub"))}</p></div></div>
      ${allSpend(S.spend_all)}
      <div class="section">${sectionHead(t("pools"), t("pools_sub"))}${twoPools}</div>
      <div class="section">${sectionHead(t("pool"), t("pool_sub"))}${pool}</div><div class="section">${sectionHead(t("providers"))}${prov}</div>
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
      ${tbl([{ h: t("shelf"), w: "10%" }, { h: t("tool"), w: "17%" }, { h: t("version"), w: "10%" }, { h: t("does"), w: "33%" }, { h: t("detail"), w: "16%" }, { h: t("stages"), w: "14%" }],
        shown.map((x) => [`<span class="shelf-n s-${x.group}">${esc(t("s_" + x.group))}</span>`, `<b>${esc(x.title)}</b><div class="sub mono">${esc(x.base)}</div>`,
          `<span class="chip">v${x.version}</span>${x.newer ? " " + badge(t("newer"), "amber") : ""}`, esc(x.about), `<span class="muted mono" style="font-size:12px">${esc(x.detail)}</span>`,
          `<span class="muted">${esc((x.used_by || []).join(" · "))}</span>`]))}`;
  },
};

/* ---------- all-time spend ---------- */
const PCOLOR = { openrouter: "var(--accent)", deepseek: "var(--blue)", google: "var(--teal)", openai: "var(--violet)" };
const pcolor = (p) => PCOLOR[p] || "var(--faint)";
function allSpend(A) {
  if (!A) return "";
  const days = {}, provs = [];
  A.by_day.filter((r) => r.day).forEach((r) => { (days[r.day] = days[r.day] || {})[r.provider] = (days[r.day][r.provider] || 0) + r.usd; if (!provs.includes(r.provider)) provs.push(r.provider); });
  const keys = Object.keys(days).sort();
  const max = Math.max(1e-9, ...keys.map((d) => Object.values(days[d]).reduce((a, b) => a + b, 0)));
  const bars = keys.length ? `<div class="bars">${keys.map((d) => { const tot = Object.values(days[d]).reduce((a, b) => a + b, 0);
      return `<div class="bar-col" title="${esc(d)} · ${usd(tot)}"><span class="bar-v">${tot >= 1 ? "$" + tot.toFixed(0) : ""}</span><div class="bar-stack" style="height:${(100 * tot / max).toFixed(1)}%">
        ${provs.filter((p) => days[d][p]).map((p) => `<i style="flex:${days[d][p]};background:${pcolor(p)}"></i>`).join("")}</div><span class="bar-d">${esc(d.slice(5))}</span></div>`; }).join("")}</div>` : "";
  const legend = `<div class="legend">${provs.map((p) => `<span><i style="background:${pcolor(p)}"></i>${esc(p)}</span>`).join("")}${A.undated ? `<span class="faint">${esc(t("undated", { v: usd(A.undated) }))}</span>` : ""}</div>`;
  const kpis = `<div class="grid g4">
    <div class="card kpi"><div class="label"><span class="dot accent"></span>${esc(t("total_spent"))}</div><div class="value">${usd(A.total)}</div>
      <div class="foot">${A.by_project.filter((p) => p.project).map((p) => `${esc(p.project)} ${usd(p.usd)}`).join(" · ")}</div></div>
    <div class="card kpi"><div class="label"><span class="dot green"></span>${esc(t("ledger"))}</div><div class="value">${usd(A.ledger)}</div><div class="foot">${esc(t("since_gateway"))}</div></div>
    <div class="card kpi"><div class="label"><span class="dot blue"></span>${esc(t("imported"))}</div><div class="value">${usd(A.imported)}</div><div class="foot">${esc(t("before_gateway"))}</div></div>
    <div class="card kpi"><div class="label"><span class="dot amber"></span>${esc(t("unattributed"))}</div><div class="value">${usd(A.unattributed)}</div><div class="foot">${esc(t("unattr_note"))}</div></div></div>`;
  const recon = tbl([{ h: t("providers"), w: "18%" }, { h: t("attributed"), cls: "num" }, { h: t("ledger"), cls: "num" }, { h: t("imported"), cls: "num" },
      { h: t("account_usage"), cls: "num" }, { h: t("unattributed"), cls: "num" }],
    A.by_provider.map((g) => [`<span class="pdot" style="background:${pcolor(g.provider)}"></span>${esc(g.provider)}`, `<b>${usd(g.usd)}</b>`, usd(g.ledger), usd(g.import),
      g.account_usage != null ? usd(g.account_usage) : `<span class="faint">${esc(t("no_account"))}</span>`,
      g.unattributed != null ? `<span style="color:${g.unattributed > 0.5 ? "var(--amber)" : "inherit"}">${usd(g.unattributed)}</span>` : "—"]));
  const top = A.total || 1;
  const purpose = tbl([{ h: t("purpose"), w: "42%" }, { h: t("model"), w: "30%" }, { h: t("spend"), cls: "num", w: "14%" }, { h: t("share"), w: "14%" }],
    A.by_purpose.slice(0, 14).map((g) => [esc(g.purpose), `<span class="mono" style="font-size:12px">${esc(g.model)}</span>`, usd(g.usd),
      `<div class="meter" style="margin:7px 0 0"><i style="width:${Math.max(2, 100 * g.usd / top)}%;background:${pcolor(g.provider)}"></i></div>`]), "—", true);
  const batches_ = tbl([{ h: t("batch"), w: "58%" }, { h: t("spend"), cls: "num", w: "20%" }, { h: t("share"), w: "22%" }],
    A.by_batch.slice(0, 14).map((g) => [g.batch ? `<span class="mono" style="font-size:12px">${esc(g.batch)}</span>` : `<span class="faint">${esc(t("no_batch"))}</span>`,
      usd(g.usd), `<div class="meter" style="margin:7px 0 0"><i style="width:${Math.max(2, 100 * g.usd / top)}%"></i></div>`]), "—", true);
  const files = A.imports.length ? `<div class="muted" style="font-size:12.5px;margin-top:10px">${esc(t("import_files"))}: ${A.imports.map((f) => `${when(f.at)} · ${esc(f.project)} · ${usd(f.usd)} · ${esc(t("rows_n", { n: f.rows }))} — ${esc(f.source)}`).join("<br>")}</div>` : "";
  return `${sectionHead(t("all_spend"), t("all_spend_sub"))}${kpis}
    <div class="card pad" style="margin-top:14px"><div class="section-head" style="margin-bottom:6px"><h2>${esc(t("by_day"))}</h2>${legend}</div>${bars}</div>
    <div class="section">${sectionHead(t("reconcile"))}${recon}</div>
    <div class="section cols"><div>${sectionHead(t("by_purpose"))}${purpose}</div><div>${sectionHead(t("by_batch_all"))}${batches_}${files}</div></div>`;
}

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
  const counts = { runs: S ? S.running.length : 0, dev: S ? S.in_progress.length + (S.unregistered_dev || []).length : 0, overview: S ? S.notifications.length + S.unregistered_runs.length : 0 };
  document.getElementById("nav").innerHTML = NAV.map(([g, items]) => `<div class="nav-label">${esc(t(g))}</div>` + items.map((k) =>
    `<a href="#/${k}" class="${k === v ? "active" : ""}">${icon(k)}<span class="label">${esc(t(k))}</span>${counts[k] ? `<span class="count ${k === "overview" ? "hot" : ""}">${counts[k]}</span>` : ""}</a>`).join("")).join("");
  document.getElementById("brand-sub").textContent = t("sub");
  const theme = document.documentElement.dataset.theme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  document.getElementById("theme").innerHTML = icon(theme === "dark" ? "sun" : "moon");
  document.querySelectorAll("#lang button").forEach((b) => b.classList.toggle("on", b.dataset.lang === LANG));
  document.getElementById("prodlink").innerHTML = `${icon("runs")}<span>${esc(t("prodlink"))}</span><span class="ext">↗</span>`;
  if (!S) { document.getElementById("page").innerHTML = '<div class="empty">…</div>'; return; }
  const p = S.pipelines[0];
  document.getElementById("crumbs").innerHTML = `${p ? `<span>${esc(p.project || "")}</span><span class="sep">/</span>` : ""}<b>${esc(t(v))}</b>`;
  document.getElementById("live").innerHTML = `<span class="dot pulse" style="background:var(--green)"></span>${esc(t("updated"))} ${esc(S.at.slice(11, 16))}`;
  const openK = {}; document.querySelectorAll("details[data-k]").forEach((d) => (openK[d.dataset.k] = d.open));
  const y = scrollY;
  document.getElementById("page").innerHTML = VIEWS[v]();
  scrollTo(0, y);
  document.querySelectorAll("[data-stage]").forEach((el) => (el.onclick = () => { const [a, b] = el.dataset.stage.split(":").map(Number); openStage(a, b); }));
  document.querySelectorAll("details[data-k]").forEach((d) => { if (d.dataset.k in openK) d.open = openK[d.dataset.k]; });
  document.querySelectorAll("[data-copy]").forEach((el) => (el.onclick = (e) => {
    e.preventDefault(); e.stopPropagation();
    const done = () => { el.classList.add("ok"); el.title = t("copied"); setTimeout(() => el.classList.remove("ok"), 1200); };
    try { navigator.clipboard.writeText(el.dataset.copy).then(done, done); } catch (err) { done(); }
  }));
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
document.getElementById("lang").onclick = (e) => {
  const b = e.target.closest("button[data-lang]"); if (!b) return;
  LANG = b.dataset.lang; store.set("reins.lang", LANG); document.documentElement.lang = LANG; render();
};
document.getElementById("scrim").onclick = () => setDrawer(false);
addEventListener("keydown", (e) => { if (e.key === "Escape") setDrawer(false); });
addEventListener("hashchange", () => { setDrawer(false); scrollTo(0, 0); render(); });
document.documentElement.lang = LANG;
render();
load();
setInterval(load, 30000);
