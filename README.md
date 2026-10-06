# ReinsLite

自动检查与审核流水线的管理体系：契约、登记库、主控命令行、付费网关、Watchdog、看板。
会话只提议，主控决定并记录。第一个接入的项目是 `e2e-plan-extract`。

- 契约和事故依据：[docs/DESIGN.md](docs/DESIGN.md)
- 会话分工、fork 协议、用户的三个决策点：[docs/WORKFLOW.md](docs/WORKFLOW.md)
- 词表：[reins/glossary.toml](reins/glossary.toml)
- 项目接入样例：[examples/reins.toml](examples/reins.toml)、[examples/rules.toml](examples/rules.toml)

## 组成

```
reins/
  store.py      登记库 $REINS_HOME/reins.db（默认 /data/reins；拒绝放在 /env/code）
  names.py      命名格式：模块版本 <module>-<suffix>-<YYYYMMDD>-<n>，批次 <council>-<wp>-<type>-<YYYYMMDD>-<n>
  glossary.*    C0 词表 + lint
  batches.py    C1 case 账本 + C3 批次（阶段顺序、paid cap 门、守恒门、冻结）
  modules.py    C2 模块版本
  dev.py        C2 生命周期：start（worktree、来源批次、pilot case）/ finish（门禁→合并→released）/ release（tag、只读树）
  gate.py       C5 门禁结果：missed_error 不增、golden 不退 = green
  runner.py     C7 启动器：reins run / reins ctl（supervisor 独立 session、pgid 控制、自动重试、暂停通知）
  spend.py      C9 账本：预留→结算、估价、价格表
  gateway.py    C9 网关：唯一持 key 的进程；缓存；cap 到顶 → 402 + 暂停 + 通知
  watchdog.py   C8：停滞、超时 case、进程丢失、磁盘/内存/GPU、网关宕
  leases.py     C10 租约：按 session id
  rules.py      C7b 规则即数据 + lane diff
  decisions.py  C13 决定记录
  notify.py     通知：notify.jsonl + 可配置命令
  board.py      C14 看板：运行中 / 开发中
hooks/guard.py  Claude Code PreToolUse 护栏
systemd/        gateway / watchdog / board 的 user 单元
```

## 快速开始

```bash
bin/reins --help
# 项目：repo 根放 reins.toml（见 examples/）
bin/reins dev start georef roadnames --about "plan road names before geocode" --from-batch <batch> --cases 111055,103713
#   -> georef-roadnames-20261006-1, cd <repo>-wt-georef-roadnames-20261006-1
bin/reins batch open --council sheffield --wp wp3 --type pilot --purpose "..." --cases cases.csv \
    --stage ocr --stage judge=judge-veto-20261005-1:paid:limit=600 --work-dir /data/sheffield/pilot-x
bin/reins batch approve <batch> --cap 8          # 用户批钱后 paid 阶段才能启动
bin/reins run <batch> ocr -- tools/run_stage.sh ...   # 阶段命令在里面用 reins batch mark 记每个 case
bin/reins batch status <batch>; bin/reins ctl pause|resume|stop <batch>
bin/reins dev finish georef-roadnames-20261006-1 # 门禁绿才合并、才 released
bin/reins dev release --note "..."               # rel-<project>-<day>-<n>，只读 worktree，production 批次从这里跑
bin/reins board serve                            # http://127.0.0.1:8791
```

## 测试

```bash
python3 -m unittest discover -s tests          # 36 tests
```
