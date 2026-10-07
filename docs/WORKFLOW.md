# 工作方式：会话怎么分工，人在哪三个点出现

适用于所有接入 ReinsLite 的项目。会话开始时读这一页；它和 [DESIGN.md](DESIGN.md) 的契约一起生效。

## 原则

**接入了 reins 的项目（仓库里有 `reins.toml`），开发和运行都只能经过 reins，没有绕过的路；每一件事都有迹可循**（用户 2026-10-07）。
- 开发：reins 管版本、冲突、门禁。只能在自己的候选分支里改（`reins dev start`，或用 `reins dev adopt` 登记之前开的分支）；
  和别的候选改同一批文件会被拒绝，除非写明理由（`--overlap-ok`，会记录）；上游文件要过上游门禁或写明跳过理由；只能用 `reins dev finish` 合并。
- 运行：reins 管成本、时间、benchmark、case 账本。流水线程序（`reins.toml [run] programs`）只能由 `reins run` 启动，
  直接执行、包在脚本里执行都会被拦；批次可设时间预算（`--time-budget`），超时会通知。
- 还没接入的仓库：hook 照样记录谁做了什么（有迹可循），但还拦不住，付费 key 也还没收走。逐个接入后再收。
- 一个事实：`/env` 是 NTFS（fuseblk），`chmod` 不起作用，所以 release 树从来没有真正只读过。它们的保护来自 hook（拦写入、拦切分支），不是文件系统。

**会话只提议，主控决定并记录。** Claude 会话（不管哪个角色）不直接启动批次、不直接合并 main、不直接花钱、不直接写冻结目录。
这些动作都走 `reins` 命令：主控检查（准入、预算、租约、版本状态）、放行、记录。
会话手上没有付费 key（key 只在网关里），冻结目录对会话只读，生产批次从 release worktree 由主控启动。

## 三种会话角色

| 角色 | 数量 | 能做 | 不能做 |
|---|---|---|---|
| **运维会话** | 1 个，常驻 | `reins run / ctl`、看 `reins notify list`、向用户汇报 | 改代码 |
| **开发会话** | 同时最多 3 个，每个只负责一个模块的一个候选版本 | 在自己的 worktree 里改代码、跑 pilot / eval 批次、`reins dev finish` | 跑 production / rework 批次；碰别人的 worktree |
| **分析会话** | 按需 | 只读：查登记库、做抽检页、回答问题 | 写任何东西 |

身份认登记库（session id + 租约），不认记忆。一个会话以为"这个批次是我的"没有用；`reins` 查租约。

## 从生产会话 fork 出开发会话

fork 保留上下文，这是好事。但 fork 出来的会话**不是**生产会话：它有新的 session id，在登记库里什么都不拥有。

fork 后的第一个动作，必须是：

```bash
reins dev start <module> <suffix> --about "一句话" \
    --from-batch <生产批次 id> --cases <触发 fork 的问题 case，逗号分隔>
cd <命令打印出来的 worktree>
```

这条命令：登记候选版本；从 main 开新 worktree（离开生产目录）；记录来源批次和来源会话；
把那些问题 case 登记成这个版本的 **pilot 集合**（先在失败的 case 上验证）；看板"开发中"出现一张卡。

fork 对生产批次做的任何运维动作（stage-start、mark、ctl）都会被拒绝，并提示它属于哪个会话。
fork 不再起自己的监控；监控是 Watchdog 和运维会话的事。

修好以后：`reins dev finish <version>` → 门禁绿 → 版本 released → 通知原批次的拥有者 → **由用户决定**是否让生产批次用新版本续跑（会标 `mixed_version`）。

## 会话之间的交接：问题单（issue）

运行会话发现问题 → 另一个会话修 → 发版 → 回到运行会话续跑，这条链每天都在发生。交接的载体是**问题单**，挂在批次上：

```
reins issue open --batch B --cases 111055,103713 --symptom "路名定位落到别的镇" --stage georef   # 运行会话 / 审核平台（reins issue from-review B）
reins dev start georef roadnames --about "..." --issue 7        # 接手的会话：问题 case 自动成 pilot 集合，问题单变"修复中"
reins dev finish V                                               # 门禁绿 → 问题单变"已修复"，开单的会话收到通知：用 V 续跑 B 的哪个阶段
reins issue verify 7 --by user --note "..."                       # 运行会话在问题 case 上确认后关闭
```
看板：运行卡上显示待修 / 修复中 / 已修复待确认的问题单；开发卡上显示"修问题单 #7"。每个新会话开始时会被告知有哪些问题单待修。
不做的："任务交接"（一个会话把半截活递给另一个会话）。会话各管一件事；要递的是问题，不是活。

## 每个模块版本的固定流程

```
reins dev start → 改代码 → pilot 批次（问题 case）→ eval 批次（benchmark，非确定阶段跑 2 次）
→ reins dev finish（门禁绿 = missed_error 不增、golden 不退）→ 用户批准 → released
→ reins dev release（打 tag，只读 worktree）→ production 批次 → 用户验收
```
上一步没完成，下一步开不了：production 批次只接受 released 版本 + release 名；paid 阶段在 `spend_cap` 为 $0 时拒绝启动。

## 提示词、模型、工作流的版本

改提示词 = 改代码：在自己的候选分支里改 `PROMPT` 常量，`reins dev finish` 时 reins 读出新文本、登记 `prompt-<name>-vN`、钉在这个模块版本上。
改模型或参数：改 `reins.toml [models]`，同样走 finish。
发版前 `reins workflow freeze local_qa`：把每个阶段的生产模块版本、提示词版本、模型版本冻成 `workflow-local_qa-vN`；
开批次用 `--workflow workflow-local_qa-vN`，批次的来源元组里就有整套。两套并行试验 = 两个工作流版本、两个批次。

## 用户出现的三个点

| 决策点 | 命令 | 用户看到 | 用户做 |
|---|---|---|---|
| 批钱 | `reins batch approve B --cap USD` | 预估 `spend_cap` 内的花费、和上一版对比 | 批或不批 |
| 批发布 | `reins dev finish V`（会话提议）→ 用户点头 | 门禁结果 + 结果变了的 case 页面 | 批或打回 |
| 验收交付 | `reins accept sample/grade/decide` | 固定 seed 抽样的评分表；F 数对错误预算 | 交付 / 带说明交付 / 返工 |

其余时间用户只看看板（`reins board serve`）的"运行中 / 开发中"两栏。批次结束、暂停、等批准都会推送通知；用户不需要问"好了么"。

## 测量闭环

```
reins bench init/freeze ──► 门禁打分（C5）──► 批次 ──► lanes ──► reins accept（C11）
        ▲                                                 │
        │  reins bench label（真人确认后）◄── reins review candidates（C6）◄── 审核员
        └──────────────── reins bench bump（新版本）◄─────────────────────────┘
```
标签只能通过 `reins bench label` 进入，golden 必须是真人、看过图纸。在对话里改标签 = 违规。
每个阈值 `reins threshold set --n`；n < 30 自动标 LOW_N。改规则 `reins rules freeze --old-lanes --new-lanes`，没有 lane diff 不生效。
每周一次 `--type drift` 批次：不读缓存，对比上周结果，看托管模型有没有悄悄变。

## 会话的硬规则

1. 花钱或跑大任务前，先用规范词复述一遍：批次 id、阶段、case 数、预计花费、上限。用户回"对"再开始。
2. 结论写进 `reins decide add`，不留在会话记忆里。被推翻的结论用 `--supersedes` 标掉。
3. 用词按 [glossary.toml](../reins/glossary.toml)。给用户的汇报里出现禁用词算违规。
4. 长任务一律 `reins run`，不用 `nohup` / `setsid` / `&`。
5. 不在别人的 worktree 或 release 目录里改任何东西（hook 会拦，但拦不住的也不许）。
6. 改路由规则必须附 `reins rules diff OLD NEW` 的输出。
7. 每个阈值记录样本量 n；n < 30 标出来。
8. 不往 `/env/code` 下写数据（`REINS_HOME`、批次 `work_dir` 都在 `/data`）。

## 守护进程（systemd --user）

```bash
mkdir -p ~/.config/systemd/user && cp /env/code/ReinsLite/systemd/*.service ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now reins-gateway reins-watchdog reins-board
loginctl enable-linger $USER
```
网关 `127.0.0.1:8790`（key 放在 `/data/reins/secrets/openrouter.key`，chmod 600），看板 `127.0.0.1:8791`，Watchdog 每 60 秒一轮。

## Claude Code hook（会话登记 + 护栏）

一个脚本 `hooks/session_hook.py` 接所有事件，装在 `~/.claude/settings.json`（`H` 代表这条命令）：
```json
"hooks": {
  "SessionStart":     [{"hooks": [H]}],
  "UserPromptSubmit": [{"hooks": [H]}],
  "PreToolUse":       [{"matcher": "Bash|Edit|Write|MultiEdit|NotebookEdit", "hooks": [H]}],
  "PostToolUse":      [{"matcher": "Edit|Write|MultiEdit|NotebookEdit", "hooks": [H]}],
  "Stop":             [{"hooks": [H]}],
  "SessionEnd":       [{"hooks": [H]}]
}
H = {"type": "command", "command": "python3 /env/code/ReinsLite/hooks/session_hook.py"}
```
每次 20–50 毫秒。登记每个会话做了什么（C15）；拦：detached 启动流水线、手工 merge/push main、写冻结目录、改别人的 worktree。
出错时放行并记日志（`fail open`），不会让会话卡住。
