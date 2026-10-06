# 工作方式：会话怎么分工，人在哪三个点出现

适用于所有接入 ReinsLite 的项目。会话开始时读这一页；它和 [DESIGN.md](DESIGN.md) 的契约一起生效。

## 原则

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

## 每个模块版本的固定流程

```
reins dev start → 改代码 → pilot 批次（问题 case）→ eval 批次（benchmark，非确定阶段跑 2 次）
→ reins dev finish（门禁绿 = missed_error 不增、golden 不退）→ 用户批准 → released
→ reins dev release（打 tag，只读 worktree）→ production 批次 → 用户验收
```
上一步没完成，下一步开不了：production 批次只接受 released 版本 + release 名；paid 阶段在 cap 为 $0 时拒绝启动。

## 用户出现的三个点

| 决策点 | 命令 | 用户看到 | 用户做 |
|---|---|---|---|
| 批钱 | `reins batch approve B --cap USD` | 预估花费、和上一版对比 | 批或不批 |
| 批发布 | `reins dev finish V`（会话提议）→ 用户点头 | 门禁结果 + 结果变了的 case 页面 | 批或打回 |
| 验收交付 | C11（待实现） | 固定 seed 抽检页 | 交付或返工 |

其余时间用户只看看板（`reins board serve`）的"运行中 / 开发中"两栏。批次结束、暂停、等批准都会推送通知；用户不需要问"好了么"。

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

## Claude Code hook（护栏）

`~/.claude/settings.json`：
```json
"hooks": {"PreToolUse": [{"matcher": "Bash|Edit|Write|MultiEdit",
          "hooks": [{"type": "command", "command": "python3 /env/code/ReinsLite/hooks/guard.py"}]}]}
```
拦：detached 启动流水线、手工 merge/push main、写冻结目录、改别人的 worktree。出错时放行并记日志（fail open）。
