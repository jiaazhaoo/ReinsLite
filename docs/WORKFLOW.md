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

**fork 是来接着干原会话的活**（原会话已经不用了）时，不要开新候选，直接接手（2026-10-18）：

```bash
reins session inherit                    # 接手父会话的工作区、批次租约和批次所有权，两边都留记录
reins session inherit --from <id> --why "原会话已放弃，接着跑 Mansfield"   # 不是自己的祖先：对方须已结束或闲置 6 小时以上
```
父会话 10 分钟内还在活动时会被拒绝：两个活着的会话不能同时拥有同一个批次或工作区。

## 会话之间的交接：问题单（issue）

运行会话发现问题 → 另一个会话修 → 发版 → 回到运行会话续跑，这条链每天都在发生。交接的载体是**问题单**，挂在批次上：

```
reins issue open --batch B --cases 111055,103713 --symptom "定位落到别的镇" --stage extract   # 运行会话 / 审核平台（reins issue from-review B）
reins dev start extract roadnames --about "..." --issue 7        # 接手的会话：问题 case 自动成 pilot 集合，问题单变"修复中"
reins dev finish V                                               # 门禁绿 → 问题单变"已修复"，开单的会话收到通知：用 V 续跑 B 的哪个阶段
reins issue verify 7 --by user --note "..."                       # 运行会话在问题 case 上确认后关闭
```
看板：运行卡上显示待修 / 修复中 / 已修复待确认的问题单；开发卡上显示"修问题单 #7"。每个新会话开始时会被告知有哪些问题单待修。

**收件箱（2026-10-18）**：reins 知道谁拥有哪个批次、谁开的问题单，所以消息按角色投递，不用猜哪个窗口是哪个会话。
问题单被开在你的批次上、被认领、被修好，候选版本和你的候选改了同一批文件，哨兵暂停了你的批次，都会进收件箱；
钩子在收件人下一次收到用户消息时（以及会话开始时）送达，送达即已读。
```
reins tell --batch B --title "可以用 qa-20261014-1 重跑 trace" "约 553 个 plan，~$8"   # 发给批次 B 现在的所有者
reins tell --to <session> --title "..." "..."                                        # 发给某个会话
reins inbox [--all]                                                                  # 看自己的消息
```
不做的："任务交接"（一个会话把半截活递给另一个会话）。会话各管一件事；要递的是问题，不是活。

## 每个模块版本的固定流程

```
reins dev start → 改代码 → pilot 批次（问题 case）→ eval 批次（benchmark，非确定阶段跑 2 次）
→ reins dev finish（门禁绿 = missed_error 不增、golden 不退）→ 用户批准 → released
→ reins dev release（打 tag，只读 worktree）→ production 批次 → 用户验收
```
上一步没完成，下一步开不了：production 批次只接受 released 版本 + release 名；paid 阶段在 `spend_cap` 为 $0 时拒绝启动。

**同步到 GitHub（2026-10-20）**：项目 `reins.toml` 的 `[dev]` 写 `push = "origin"` 后，`reins dev finish` 推 main，`reins dev release` 推 main 和 release tag，
watchdog 每 6 小时补推一次（网断时的合并、手打的 tag）。从不强推：远端有本地没有的提交 = 推送失败，发 action 通知，修好后 `reins dev sync`。

**权重放到机器以外（2026-10-20）**：项目 `reins.toml` 写 `[weights_store]`（`kind = "hf"` 私有模型仓库，或 `kind = "dir"` 挂载盘），
`origin = "trained"` 的权重每个登记版本上传一份：同一用途的模型一个文件夹（名字在 `[weights_store.folders]` 起，要看得出模型干什么），
文件夹里每版一个 `vN/`（文件 + 带 sha256 的 manifest），文件夹 README 列出各版本、当前版本和指标；`index.json` 给机器读；
`reins train` 登记后立即上传，watchdog 每 6 小时补传，失败发 action 通知。新机器不需要登记库：`reins weights pull --to DIR`，sha 不符拒装。

## 分模块的 benchmark 和门禁（2026-10-19）

改哪一步就考哪一步：每个模块用自己的 benchmark 和自己的标准答案打分，下游只重跑输出变了的 case；端到端全量只在发版前跑。

```toml
# 项目的 reins.toml
[modules.plan_crop.gate]
cmd = "python3 benchmark/s2_crops/module_gate.py"   # 写 $REINS_GATE_OUT：metrics + cases_rerun / cases_reused
benchmark = "bench-sheffield-crops"                 # 传给命令：$REINS_GATE_BENCH
```
```
reins bench changed bench-sheffield-crops --stage plan_crop --dir OUT --ignore '*.log'   # 哪些 case 的裁图变了
reins bench add-cases bench-sheffield-crops --issue 7 --split dev --why '生产上框错的页'      # benchmark 长大（开着的版本）
```
benchmark 自己也进化：生产错例 → dev；没调过参的新 case → holdout；冻结后改不了，要加就 `reins bench bump`。

## 提示词、模型、工作流的版本

改提示词 = 改代码：在自己的候选分支里改 `PROMPT` 常量，`reins dev finish` 时 reins 读出新文本、登记 `prompt-<name>-vN`、钉在这个模块版本上。
改模型或参数：改 `reins.toml [models]`，同样走 finish。
重训了 YOLO / ConvNeXt：新权重文件自动成为 `weights-<name>-vN`（内容哈希），训练记录从 `results.csv` / `history.json` 采集；
换权重 = 改 `reins.toml [weights]` 指向的文件或内容，同样走 finish；开批次前 preflight 核对每个权重都是登记过的版本。
发版前 `reins workflow freeze local_qa`：把每个阶段的生产模块版本、提示词版本、模型版本冻成 `workflow-local_qa-vN`；
开批次用 `--workflow workflow-local_qa-vN`，批次的来源元组里就有整套。两套并行试验 = 两个工作流版本、两个批次。

## 用户出现的三个点

| 决策点 | 命令 | 用户看到 | 用户做 |
|---|---|---|---|
| 批钱 | `reins batch approve B --cap USD` | 预估 `spend_cap` 内的花费、和上一版对比 | 批或不批 |
| 批发布 | `reins dev finish V`（会话提议）→ 用户点头 | 门禁结果 + 结果变了的 case 页面 | 批或打回 |
| 验收交付 | `reins accept sample/grade/decide` | 固定 seed 抽样的评分表；F 数对错误预算 | 交付 / 带说明交付 / 返工 |

**长期批准**：一类批次每次都要的小额花费，用户可以一次批好：`reins config set standing.TYPE USD --why '...'`
（记成一条决策；0 = 关掉；不能超过 `max_batch_cap`）。之后这类批次只要有付费阶段，开批次时就自动得到这个上限，
照样过机器池和自己池子的每日检查；池子不够时上限留在 $0，记一条 `standing_refused` 事件，付费阶段照旧等人批。

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

## 哨兵：结果看起来不对（2026-10-18）

进程正常退出、每个案件都"有结果"，结果却是错的：付费阶段一次网关都没调到（客户端库不认 `<ENV>_BASE_URL`）、
判定模型因网关拒绝而"一个都没答"、输入文件是空的。每个阶段结束时，哨兵把这个阶段和它自己、和同一项目以前的批次比：

| 检查 | 条件 |
|---|---|
| `paid_no_calls` | 付费阶段完成了案件，却没有一次经过网关的调用（缓存命中也算调用） |
| `one_reason` | 九成以上的案件以同一个原因没完成，而以前的批次在这个阶段没有这样 |
| `rate_jump` | 没完成的比例远高于这个阶段平时（> 2 倍中位数，且至少高 25 个百分点） |

生产和返工批次上，任何一条成立：阶段记为失败（下一阶段不能开始）、批次暂停（运行器不再重试撞同一面墙）、
自动开问题单、通知并发收件箱给批次所有者。实验、试跑等批次只发通知。付费阶段运行 30 分钟还没有一次网关调用，看门狗也会提醒。
用 2026-10-09 至 10-18 的 97 个已完成阶段回测，会标出 18 个，包括 Charnwood WP3 第一批（整批 1515 件"没有分道"却记为完成）、
Mansfield 判定"没回答"的两批（网关漏句柄那次）、描线阶段零调用（绕开网关那次）。

如果发现的情况其实是这个流程的常态（返工批次的 prepare 全部跳过、一批没有扫描件的案件），由人确认一次：
```
reins sentinel check B STAGE                     # 看哨兵发现了什么
reins sentinel ack B STAGE --why "..."           # 确认是常态：阶段恢复为完成；同项目同原因以后不再拦（本批后续阶段、以后批次的这个阶段）
```
阈值在 `config.toml [sentinel]`：`min_cases`、`reason_share`、`rate_jump`、`rate_margin`、`history`、`paid_idle_min`。

超出时间预算（`reins batch open --time-budget H`）默认只提醒；`config.toml` 里 `overtime_action = "pause"` 改为直接暂停批次，
等人决定续跑、加预算还是停止。同一问题持续存在时，提醒间隔逐次翻倍，最长一天一次；reins 从没接管过的 adopt 批次不再报"停滞"。

## 资源准入和被杀检测（2026-10-13）

起因：两个批次（`mansfield-low-rework-20261012-3` / `-4`）并行，手写的 GPU 守卫只管 GPU，不管内存；每个托管判官进程约 11 GB，
机器的 resource-watchdog 在 07:27、09:18、09:21 杀了子进程，流水线拿着缺失的输入继续跑，约 2 小时没人发现（守卫和等待脚本随启动它的会话一起没了）。

**准入**：项目在 `reins.toml` 里声明每个阶段或每类步骤要什么，reins 在 `reins.db` 里记一本所有批次共用的占用账（`resource_claim`），
超出机器预算的申请排队等，不启动。

```toml
# 项目的 reins.toml（项目的词和数字只写在这里）
[resources.judge]
match = "qa_judge\\.py"       # 正则，匹配批次进程树里任一进程的命令行；每个匹配的进程一份占用（它的子进程不重复算）
mem_gb = 11
[resources.ocr]
match = "ocr_rotated"
gpu_gb = 8
exclusive = ["gpu"]             # 同一个标签同一时刻只放行一份占用：GPU 步骤互斥，代替手写的 guard 脚本
[resources.check]
stage = "check"                 # 或者整个阶段
mem_gb = 4
```
```toml
# 机器的 config.toml（$REINS_HOME/config.toml）
[resources]
mem_total_gb = 0       # 0 = 读 /proc/meminfo
mem_reserved_gb = 8    # 机器上别的东西占的（桌面、服务、会话）
gpu_total_gb = 0       # 0 = 问 nvidia-smi
max_fraction = 0.75    # 已放行的占用合计不超过 total × max_fraction − reserved
poll_s = 1.0
```
- 顺序：先到先得，不插队。一个申请只有在放得下、而且没有更早的（运行中批次的）申请还在等的时候才放行；暂停的批次不挡别人。
  单个申请比整个预算还大：只在这一维没人占的时候放行（独占跑）。
- 在哪里等：`reins run B STAGE` 的阶段规则 → 监督进程等到放行才启动命令；自带阶段的命令（`--self-staged`）→ `reins batch stage-start` 阻塞，
  命令本身也就停在那里；`match` 规则 → 监督进程每 `poll_s` 秒看一次进程树，新出现的匹配进程放不下就 SIGSTOP（连同子进程），放行时 SIGCONT。
- 占用在进程结束、阶段结束（`stage-end`）时释放；进程没了的占用由监督进程和 Watchdog 清掉，账上不留幽灵。
- 查看：`reins resources`（预算、已放行、在等的），看板的提醒区也有一行。`reins config check` 会检查 `[resources]` 写得对不对。

**被杀检测**：监督进程（独立于会话，会话退出它还在）负责：
- 它启动的命令被 SIGKILL / SIGTERM 结束（rc −9 / −15 / 137 / 143），又不是 `reins ctl stop` 要求的；
- 或者 `config.toml [kill_watch]` 跟踪的日志里有一行点名了它进程树里出现过的 pid：
  ```toml
  [kill_watch]
  cmd = "journalctl --user -f -n 0 -o cat"
  pattern = "killing pid=(?P<pid>\\d+)"
  ```
动作：批次记一条 `killed` 事件 → 批次暂停（整个进程组 SIGSTOP，下一步不会拿着缺失的输入开跑）→ 发通知（看板、`reins notify list`、`notify_cmd`）。
不自动重试。之后二选一：`reins ctl stop B`，再从被杀的那一步重开；或者命令自己会补做时 `reins ctl resume B --after-kill`
（不带 `--after-kill` 的 resume 会被拒绝）。

**`reins run` 是分离启动**：命令立刻返回，输出写明 `detached: live process #N …`；批次在最后一个阶段结束时就标 done，那时进程可能还在收尾。
脚本要等它真的结束：`reins wait B`（退出码 0 = done，1 = 没完成 / 暂停 / 失败，2 = 超时 `--timeout`）。

## 账本自己的维护（2026-10-19）

后台检查程序（watchdog）每一轮顺带做这些事，不用人记着：

- **备份**：每天一份 `reins.db`，用 SQLite 在线备份，做完校验；放在 config.toml `[backup] dir`（本机是另一块盘
  `/env/reins-backups`），保留 14 天。`reins backup --now` 立刻做一份。
- **状态对齐**：标着"运行中"的批次，进程已经不在、半小时没有进展（reins 从没启动过的批次是 24 小时）→ 改成暂停，
  原因写明（机器重启 / 进程丢了 / 报错退出 / 正常结束但没收尾）。`reins ctl resume` 在没有进程可继续时，批次保持暂停。
- **锁**：已结束批次、已合并或放弃的候选、已删除的工作目录上的锁自动释放。
- **磁盘**：85% 预警，90% 暂停。

改 `$REINS_HOME/config.toml`（花费上限、阈值、备份位置）用 `reins config set KEY VALUE --why '...'`：只改那一个值、
注释保留、旧文件留在 `config-history/`、记成一条决策；`reins config history` 列出所有改动。不再手改、手留 `.bak`。

训练也进账本：`reins train start NAME --about '...' --weights PATH -- 训练命令`。它是一个 `training` 类型的批次
（没有案件，只有一个 `train` 阶段），由 reins 启动，所以有进程记录、资源准入、运行时长和花费；正常结束时
PATH 处的权重自动登记成 `weights-<name>-vN`，带上训练记录。`reins train list` 列出所有训练。

## 共享知识：服务、事实、简报（2026-10-21）

一个会话学到的东西，要放在下一个会话会看的地方，而不是只留在它的对话记录里。（起因：8773 早已能同时挂三个
council，一个会话只读了服务器参数就说不行；另一个会话正在修吸附底图，第三个会话又提出要修。）

- **服务**：对外开放的服务（审核平台、标注页面）登记成 service：端口、跑的哪份代码（发布版 / 候选 / 主目录 /
  仓库外脚本）、数据目录、数据是哪个脚本搭的、systemd 单元、谁登记的。后台检查每分钟对一遍：重启、下线自动记下；
  有人开了端口没登记，每周提醒一次。`reins service list`、`reins service adopt PORT --name N --about '...'
  --data DIR --built-by SCRIPT`。改了一个在线服务（换名单、追加数据、改配置）：`reins service event NAME '改了什么'`。
- **事实**：`reins note add 模块或服务 '一句能照着做的话'`，例如"一个端口能挂多个 council：先建合并视图，前缀留空"。
  过时了用 `reins note retire ID --why`。
- **问题单可以挂在模块或服务上**：`reins issue open --service qa-review-study3 --symptom '...'`。修它的人照常
  `reins dev start ... --issue N`，别人在简报里就看到"已有人在修"。
- **简报**：`reins brief review` 一屏看全：线上版本和最近的改动、谁正在改（候选版本和未登记的编辑）、哪些服务跑
  这个模块、未解决的问题、事实、相关决策。会话第一次改某个模块的文件、或在对话里提到某个服务的名字或端口时，
  钩子自动把这份简报给它；会话开始时列出所有登记的服务和没登记的端口。
- **会话用途**：会话没有 `reins session bind` 时，用它收到的第一句话当用途，别的会话能看到它在做什么。
- **实时**：每个变化发生时就记成一条变更（事实增删、问题开/接/修/关、服务登记/被改动/重启/下线、模块开始开发/合并/放弃），
  `reins changes [主题] --since 2h` 可查。会话关注它看过简报、改过文件、动过服务、正在开发的主题（含该模块的服务）；
  别的会话的变化在它**下一次操作结束或下一次提问时**送到。会话执行的命令如果动了登记过的服务（重启它的单元、写它的
  数据目录、在那里跑脚本、改它的数据库），钩子当场记一笔，不用会话自觉。改过模块或服务却一条事实都没留的会话，
  在这一轮结束前被问一次。钩子配置用 `bin/reins-install-hooks` 安装或更新（会先备份 settings.json）。

## 预算：整机总额度和两个池（2026-10-21）

- **整机** `[pool]`：所有花费合计的上限（每小时 / 每天 / 每周 / 单批 / 单会话每天）。
- **生产池** `[pool.production]`：生产、返工批次；**开发池** `[pool.development]`：其余类型（实验、评测、试跑、回归检查、
  训练……）。每个池可以有自己的 `hourly_cap`、`daily_cap`、`weekly_cap`、`max_batch_cap`、`per_session_daily_cap`；
  没设的那一项只受整机限制。归哪个池按批次类型，`types = [...]` 可改。
- 每次付费调用和每次批花费审批，都同时检查整机和所属的池。审批算的是**真实承诺**：今天已花或已预留 + 活跃批次
  还没花的余额（停了一天以上的批次不占额度）；不是所有没关闭批次的上限总和。
- 改额度：`reins config set pool.development.daily_cap 30 --why '...'`，记成一条决策，同时显示近 30 天实际日用量。
  被额度拦下时，正确的解法是缩小或关闭占着余额的批次（报错里有命令）；调高额度要用户决定。
- 看板"成本"页：两个池各自的今日、本周用量和上限，以及整机合计。

## 目录挪动之后（2026-10-22）

账本里记的路径是历史（批次当时在哪里跑），整理目录时不改。reins 读文件时（生产页、报表、进度探针、上线前检查、
卡住检测、续跑的日志目录）先按本机的挪动记录把旧路径换成现在的位置：config.toml `[paths] moves = ["<old/new 表>"]`
（制表符分隔，表头含 `old`、`new`，按时间先后）。`reins paths resolve PATH` 查一个旧路径现在在哪；`reins paths check`
列出还在用、但按挪动记录也找不到的路径（未结束批次的工作目录、权重、在线服务的数据）。要看的磁盘：`disks = [...]`。

## 批次怎么结束（2026-10-22）

- 不逐案件上报结果的批次（实验、试跑、评测、训练……用 `reins run B 阶段 -- 脚本` 跑一个脚本）：程序正常退出（0）就把
  阶段标为完成（备注"未逐案件上报"），批次完成。逐案件上报了一部分的，照旧要求补齐。
- 生产、返工批次：一律不自动结束。程序正常退出但阶段没收尾，会发消息给负责的会话，看板列在"要你决定"。
- 磁盘满只暂停有活进程的批次；磁盘降回暂停线以下 2 个百分点，后台检查程序自动恢复它们。
- 非生产批次没有活进程、7 天没有动静（`stale_days`），后台检查程序自动关闭，记原因，数据不动。
- 会话这一轮结束前，如果名下有没收尾、也没在跑的批次，钩子问一次：完成、放弃，还是保留待续。
- 批次的监督进程发给负责会话的消息（哨兵暂停、阶段收不了尾）以前被当成"发给自己"丢掉了，现在能送到。

## 候选版本和问题单也会结束（2026-10-22）

- 候选版本 7 天没有任何动静（编辑、事件），而且开它的会话不在活跃状态 → 自动放弃，分支和工作目录保留
  （`reins dev adopt 工作目录` 可以重新接手）；它接的问题单退回"待修"。
- 问题单：挂在没做完就关闭的批次或已下线的服务上 → 关闭；已修复的，问题案件在修复后的运行里都跑成功了 → 验证通过；
  修复 14 天后仍没有可验证的运行 → 关闭并写明"没有重跑验证"。

## 同一件事的多次运行（2026-10-22）

人只负责开跑；运行怎么结束由 reins 决定并记录（用户 2026-10-22）。
- 开批次时自动认出它和同工作包旧批次的关系：重跑（案件相同）、拆分（一部分）、扩充（更多）、重叠，记在 `parent`
  和一条 lineage 事件里。`reins batch lineage B` 看一条运行链，`--backfill` 给旧批次补上。
- 生产和返工批次没有活进程时：结果齐了 → 完成；被后来的同类运行覆盖了它的案件 → 关闭，写明被谁取代（实验不取代生产）；
  停了 `stale_days` 天 → 有部分结果就算部分完成（结果照样计入），一个结果都没有就关闭。

## 改 ReinsLite 本身（2026-10-18）

ReinsLite 也由它自己管理（仓库里的 `reins.toml`：core、guard、board、docs 四个模块）。和任何受管项目一样：
`reins dev start core 后缀 --about '...'` 开候选工作区，改完 `reins dev finish 版本`，门禁是全部单元测试，合并后自动推送到 origin。
直接改主目录、手动 merge 或 push 都会被护栏拒绝，所以 gateway、watchdog、board 跑的永远是已提交、已过测试的代码
（10-13 曾有一批改动在主目录里放了五天没提交，服务一直跑着它）。

## 会话的硬规则

1. 花钱或跑大任务前，先用规范词复述一遍：批次 id、阶段、case 数、预计花费、上限。用户回"对"再开始。
2. 结论写进 `reins decide add`，不留在会话记忆里。被推翻的结论用 `--supersedes` 标掉。
3. 用词按 [glossary.toml](../reins/glossary.toml)。给用户的汇报里出现禁用词算违规。
4. 长任务一律 `reins run`，不用 `nohup` / `setsid` / `&`。
5. 不在别人的 worktree 或 release 目录里改任何东西（hook 会拦，但拦不住的也不许）。
6. 改路由规则必须附 `reins rules diff OLD NEW` 的输出。
7. 每个阈值记录样本量 n；n < 30 标出来。
8. 不往代码根目录下写数据（`REINS_HOME`、批次 `work_dir` 都在数据目录；本机位置见 `~/.config/reins/settings.toml`）。

## 守护进程（systemd --user）

```bash
<ReinsLite>/bin/reins-install-services      # 用当前检出目录填好单元模板，装到 ~/.config/systemd/user 并启动
loginctl enable-linger $USER
```
网关 `127.0.0.1:8790`（key 放在 `$REINS_HOME/secrets/<provider>.key`，chmod 600；服务商见 `reins/providers.py` 和 config.toml `[providers.*]`），看板 `127.0.0.1:8791`，Watchdog 每 60 秒一轮。

## Claude Code hook（会话登记 + 护栏）

一个脚本 `hooks/session_hook.py` 接所有事件，装在 `~/.claude/settings.json`（`H` 代表这条命令）：
```json
"hooks": {
  "SessionStart":     [{"hooks": [H]}],
  "UserPromptSubmit": [{"hooks": [H]}],
  "PreToolUse":       [{"matcher": "Bash|Edit|Write|MultiEdit|NotebookEdit", "hooks": [H]}],
  "PostToolUse":      [{"matcher": "Bash|Edit|Write|MultiEdit|NotebookEdit", "hooks": [H]}],
  "Stop":             [{"hooks": [H]}],
  "SessionEnd":       [{"hooks": [H]}]
}
H = {"type": "command", "command": "python3 <ReinsLite>/hooks/session_hook.py"}
```
每次 20–50 毫秒。登记每个会话做了什么（C15）；拦：detached 启动流水线、手工 merge/push main、写冻结目录、改别人的 worktree。
出错时放行并记日志（`fail open`），不会让会话卡住。

## 运行目录必须在规定位置（2026-10-22）

项目可以在自己的 `reins.toml` 里写 `[run] work_dirs = ["/data/councils/*/runs/{batch}", "/data/benchmarks/*/experiments/{batch}"]`。
写了以后，`reins batch open --work-dir` 只接受符合其中一个模式的目录：`{batch}` 会换成新批次名（所以开批次时直接写
`--work-dir /data/councils/sheffield/runs/{batch}`），`*` 只代表一层目录名。目录不存在时自动创建（父目录必须已存在）。
不符合的直接拒绝，批次不会登记。没写这个设置的项目照旧（只禁止放在代码目录里）。
