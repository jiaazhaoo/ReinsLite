# ReinsLite

一套通用的开发与运行管理框架，管的是"会话驱动的自动化流水线"：契约、登记库、主控命令行、付费网关、Watchdog、看板。
会话只提议，reins 决定并记录。框架本身不认识任何领域词；一个项目的领域内容只放在它自己的仓库里。

- 契约和依据：[docs/DESIGN.md](docs/DESIGN.md)
- 会话分工、fork、交接、用户的决策点：[docs/WORKFLOW.md](docs/WORKFLOW.md)
- 框架词表（只有通用词）：[reins/glossary.toml](reins/glossary.toml)
- 项目模板（一个虚构的表单抽取项目，演示全部接入点）：[examples/](examples/)

## 框架负责什么，项目负责什么

| 归框架（ReinsLite 仓库） | 归项目（项目仓库的 `reins.toml` 和它指向的文件） |
|---|---|
| 案件主键的概念 `case_id`、守恒账本 | 主键在本项目叫什么 `case_key`、长什么样 `case_id_pattern` |
| 批次命名 `<scope>-<type>-<YYYYMMDD>-<n>` | scope 由哪几段组成 `[batch] scope`（如 客户-工作包） |
| 模块版本、门禁流程、发布、候选工作区 | 门禁命令 `[dev] gate`、模块和文件 `[modules.*]` |
| 运行器、暂停恢复、只许 `reins run` 启动 | 哪些程序是流水线 `[run] programs`、阶段 `[run] stages` |
| 提示词 / 模型 / 权重 / 工具 / 工作流的版本机制 | 具体有哪些 `[prompts.*]` `[models.*]` `[weights.*]` `[tools.*]` `[workflows.*]` |
| 通用词表 C0 | 领域词表 `[glossary] file` |
| 交付件契约检查（主键、重复、截断、空列） | 禁止的文字 `[deliver] forbid_scripts`、表格结构 |
| 网关、计费、预算池、服务商预设 | — |

| 归本机（不进任何仓库） | 位置 |
|---|---|
| 登记库和代码根目录在哪 | `~/.config/reins/settings.toml`：`home`、`code_root`（环境变量 `REINS_HOME` / `REINS_CODE_ROOT` 优先） |
| 端口、预算池、服务商、只读目录、基准目录 | `$REINS_HOME/config.toml`：`[pool]`、`[providers.*]`、`frozen_paths`、`bench_root` |
| 服务商密钥 | `$REINS_HOME/secrets/<provider>.key`（chmod 600），只有网关读 |

## 接入一个新项目

1. 在项目仓库根目录放 `reins.toml`（从 [examples/reins.toml](examples/reins.toml) 抄起），需要的话再放 `glossary.toml`。
2. `reins config check`：reins.toml 里用到的每个名字都有定义。
3. `reins artifact scan`：登记当前的提示词、模型、权重、工具版本；`reins workflow freeze NAME` 冻结工作流。
4. 流水线每个阶段结束时 `reins batch mark BATCH STAGE --file outcomes.tsv`（首列是案件主键）。
5. 付费调用改用 `<ENV>_BASE_URL` / `<ENV>_API_KEY`（`reins run` 会给每个在用的服务商设好，指向网关）。
6. 以后：开发 `reins dev start/finish`，运行 `reins batch open` + `reins run`，看板上自动出现流程图谱。

已接入的项目：`e2e-plan-extract`（英国规划档案的多边形质检），它的完整配置就在那个仓库的 `reins.toml` 和 `glossary.toml`。

## 组成

```
reins/
  store.py      登记库 $REINS_HOME/reins.db；本机位置 ~/.config/reins/settings.toml；旧库自动迁移
  names.py      命名：模块版本 <module>-<suffix>-<YYYYMMDD>-<n>，批次 <scope>-<type>-<YYYYMMDD>-<n>；主键列识别
  projects.py   本机管理的项目（代码根目录下带 reins.toml 的仓库）；reins config check
  glossary.*    C0 词表 + lint（框架词表 + 项目词表）
  batches.py    C1 case 账本 + C3 批次（阶段顺序、paid cap 门、守恒门、冻结）
  modules.py    C2 模块版本
  dev.py        C2 生命周期：start / finish（门禁→合并→released→钉住产物→重新冻结工作流）/ release
  artifacts.py  C17 版本：prompt / model / weights / tool / workflow
  gate.py       C5 门禁结果：missed_error 不增、golden 不退 = green
  runner.py     C7 启动器：reins run / reins ctl
  providers.py  C9 服务商：预设 + config.toml，网关、计费、看板都从这里读
  spend.py      C9 账本：预留→结算、估价、余额、预算池
  gateway.py    C9 网关：唯一持 key 的进程；缓存；cap 到顶 → 402 + 暂停 + 通知
  watchdog.py   C8：停滞、超时 case、进程丢失、磁盘/内存/GPU、余额
  leases.py     C10 租约
  rules.py      C7b 规则即数据 + lane diff
  decisions.py  C13 决定记录
  issues.py     C16 问题单：运行发现问题 → 另一个会话修 → 原批次确认
  sessions.py   C15 会话索引：每次开发和运行从哪个会话来
  notify.py     通知
  board.py      C14 看板：流程图谱（每阶段一只像素小螃蟹）/ 成本 / 开发 / 运行
  bench.py      C4 benchmark；review.py C6 审核记录；accept.py C11 验收；deliver.py C12 交付件
  preflight.py  输入体检；envs.py 环境清单
hooks/          Claude Code 钩子：会话索引 + 护栏（只在带 reins.toml 的仓库里生效）
systemd/        gateway / watchdog / board 的 user 单元
```

## 快速开始

```bash
bin/reins --help
bin/reins config check                                   # 在项目仓库里
bin/reins dev start extract roadnames --about "..." --from-batch <batch> --cases F000101,F000102
bin/reins batch open --scope acme-q3 --type pilot --purpose "..." --cases cases.csv --workflow workflow-main-v1 --work-dir /data/acme/pilot
bin/reins batch approve <batch> --cap 8                  # 用户批钱后付费阶段才能启动
bin/reins run <batch> --self-staged -- tools/run_all.sh /data/acme/pilot
bin/reins dev finish extract-roadnames-20261008-1        # 门禁绿才合并
bin/reins board serve                                    # http://127.0.0.1:8791
```

## 测试

```bash
python3 -m unittest discover -s tests
```
