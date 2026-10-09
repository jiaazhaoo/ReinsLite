# ReinsLite 契约草案 v0（2026-10-06）

状态：草案，待用户审。依据：用户 10-06 定下的 7 条方向 + 2026-08-11 至 10-06 共 69 个会话的事故复盘。
每条契约写三样：**定义**、**规则**（可被机器检查的不变量）、**为什么**（真实事故，证明这条规则不是凭空加的）。

---

## 总览：五层，17 份契约

| 层 | 契约 | 一句话 | 用户方向 |
|---|---|---|---|
| 0 语言 | **C0 词表** | 一词一义，禁用词由 lint 拦截 | 第 5 条 |
| 1 身份 | **C1 Case** | case_id 是唯一主键（项目在 reins.toml 里用 case_key 给它起自己的列名），从输入到交付一条连续的账 | 第 1 条 |
| | **C2 模块版本** | 模块名-后缀-日期；每个产出都盖版本戳 | 第 2 条 |
| | **C3 批次** | 任何批次先登记再运行；类型、阶段、日志、花费全记录 | 第 3 条 |
| 2 证据 | **C4 Benchmark** | 冻结、分级标签、开发集/封存集、声明覆盖范围 | 第 4 条 |
| | **C5 门禁** | 改了哪个模块 → 必须过哪几层；漏放不许增加 | |
| | **C6 人工审核** | 只追加、署名、必填字段、审核员校准、回流 benchmark | 第 8 条 |
| 3 运行控制 | **C7 主控路由** | 规则即数据；每个 case 的去向可解释；规则变更自动出差异 | 第 7 条 |
| | **C8 Watchdog** | 不依赖会话的守护进程；看进度不看存活；分级动作 | 第 6 条 |
| | **C9 花费账本** | 所有付费调用过一个网关：缓存、预留、结算、上限 | 第 8 条 |
| | **C10 占用与租约** | worktree、批次目录、端口、GPU 都有唯一持有者 | 第 8 条 |
| 4 交付 | **C11 验收** | 抽样、错误预算、交付/返工决定，成文并登记 | 第 8 条 |
| | **C12 产物不可变** | 交付件和冻结件只增不改；重做 = 新版本 | 第 8 条 |
| 横向 | **C13 决定记录** | 结论有日期、有证据、可被推翻；被推翻的要标出来 | 第 8 条 |
| | **C14 主控看板** | 只看两样：运行中、开发中 | 10-06 追加 |
| 横向 | **C15 会话索引** | 会话是每个开发和运行的来源索引：这个动作是从哪个会话来的、记录在哪 | 10-07 追加 |
| | **C17 工件版本** | 提示词、模型、工作流各自有版本，按内容寻址，可多套并存；模块版本钉住它用的提示词和模型，工作流版本钉住整套 | 10-07 追加 |
| | **C16 问题单** | 运行发现问题 → 开发修 → 回到运行续跑，这条交接链的载体；挂在批次上，绑定修它的版本 | 10-07 追加 |

框架不认识任何领域词（红线、地址、Sheffield）。领域内容都在项目自己的 `reins.toml`、项目词表和插件里。
下文的事故例子都来自第一个接入项目（e2e-plan-extract，英国规划档案），它们是契约的依据，不是契约的一部分；那个项目的领域词（oachargeid、plan_image、georef、work_package……）在它自己的 `glossary.toml` 里。
哪些归框架、哪些归项目，见 README 的"接入一个新项目"。

---

## C0 词表（一词一义）

**定义**：`glossary.toml` 是唯一词源。每个词条：`term`（英文规范词）、`zh`（中文对照）、`definition`、`allowed_values`（如果是枚举）、`forbidden_synonyms`、`owner`（哪份契约定义它）。

**规则**
1. 代码里的字段名、表头、文档、状态值，只能用词表里的词。`reins lint` 扫描代码和产出表头，命中禁用词即报错。
2. 一个词只指一类对象。同一类对象只有一个词。
3. 新词必须先进词表（带定义）才能用。改定义 = 新词条 + 旧词条标记 retired，不许原地改义。
4. 对话里也用规范词。会话给用户的汇报里出现禁用词，算违规。

**首批要拆开的歧义词**（会话里实测到的，每个都造成过误解）：

| 现在的词 | 实际混用的意思 | 拆成 |
|---|---|---|
| `QA` | 客户质检 / 我们的 `QA` 层 / 本地第 13 步 / 整条 15 步流水线 / 审核平台 / 被检的多边形 / `QA` 团队 | `customer_check`、`check`（自动检查）、`review`（人工）、`pipeline`；不再单用 `QA` |
| `pass` / 通过 / 判对 | `HMLR` `Pass` / 本地 `correct` / 自动放行 / 抽检 `P` / 门禁通过 / 测试通过 | `customer_result` ∈ `{Pass, Fail, Advisory}`；`verdict` ∈ {correct, wrong, unsure}；`lane` ∈ {auto_accept, review, auto_reject, cannot_verify}；`gate_status` ∈ {green, red} |
| `confidence` / `high` | 至少 9 种：`geocode` 可靠、`trace` 置信、交付等级、模型自报、映射分数、`P5` "高置信"… | 每个必须带归属：`<stage>_confidence`，词表里写清是校准概率还是档位；裸的 `high` 禁用 |
| `batch` / 第一批 | `HMLR` `Batch-3`（=`WP3`） / 运行批次 / `sh.xlsx` `Batch1` / 盲测第一批 / `Qwen` `batching` | `batch`（C3 的对象）；`work_package`；客户标签进 `customer_batch_label` |
| `plan` | 图纸图片 / `site`·`location`·`floor` 类型 / `Plan` 生产路线 / 计数单位 | `plan_image`、`plan_type`、`capture_route` ∈ `{link_cleanse, plan, draw}`；裸的 `plan` 禁用 |
| `case` | `charge` / `application` / 文件夹 / `page` / `extent` | `case` = 一个 case_id（该项目叫 oachargeid）；`application`、`source_folder`、`page` 各是各（项目词） |
| `truth` / `golden` / 标准答案 / 基准 | `HMLR` 结果 / 用户裁定 / 助手自标 / 旧交付几何 / 冻结 `benchmark` | `customer_result`、`golden`（只来自审核页的人工裁定）、`reference_geometry`（不是真值）、`benchmark` |
| `ref` / 配准 | `georef` / 案卷号 | `georef`；案号用 `la_reference` |
| 门槛 / `cap` | 分数阈值 / 18 秒截断 / 单次花费上限 / 账户余额 / 每周 `key` 限额 | `threshold`、`time_limit`、`spend_cap`、`balance`、`key_limit` |
| `review` | 人工复核 / 本地 `lane` 名 / `review` 页面 | 只指人工；lane 名改 `review` 保留但只能意味着"给人看" |
| `version` / `v6` | `PP-OCRv6` / 规则 `v6` / `env` `paddleocr_v6` / `benchmark` `v6` | 版本号必须带前缀对象名：`bench-…-v6`、模块版本见 C2 |
| `P1–P5` | 审核队列，但 `P` 又是 `Pass` | 队列改名 `Q1–Q5` |
| `category` / `queue` / `Status` / `lane` | 四套重叠的分类 | 只留两套：`lane`（路由结果）+ `review_queue`（人工排序）；其余删除 |

---

## C1 Case（唯一身份，三个连贯）

**定义**：`case` = 一个 `case_id`（字符串，原样保存，不加前缀、不转数字；格式由项目的 `case_id_pattern` 规定，列名由 `case_key` 规定，reins 两个名字都认）。显示用的前缀（如 `SHF_`）只是显示层。
case 下面的对象用层级 ID：

```
case            case_id
└ source_file   <case_id>/file:<sha256 前 12 位>
  └ page        <case_id>/page:<file_sha>:<页码>
    └ item      <case_id>/img:<sha 前 12 位>       （项目自己的中间物，如图纸图片）
└ output        <case_id>/out:<stage>:<序号>       （多边形、字段……）
```
子对象 ID 按内容哈希，不按文件名。

**规则**
1. **数据连贯（守恒）**：批次的输入 case 集合，在每个阶段的输出里都必须出现，且只能是 `done` / `skipped(reason)` / `failed(reason)` 之一。少一个、多一个、重复一个，阶段都不算完成。
2. **过程连贯（归属）**：page 归属哪个 case 必须有显式依据（映射表版本 + 规则 id），不能按"在同一个文件夹里"推断。共享文件夹自动标记。
3. **记录连贯（时间线）**：`reins case <case_id>` 能拉出这个 case 的全部历史：在哪些批次、每个阶段用了哪个模块版本、路由到哪、谁审过、审核结论、交付了什么。
4. 一个 case 有多个输出（多边形 1:N）必须显式声明，读取方不许只留最后一个。
5. 外部键（LA 号、S3 路径、portal keyVal）只能通过有版本号的映射表关联到 case。

**为什么**
- 输入 26,384 个 case，交付 26,044，差的 340 个没人知道（Sheffield）。
- 104 个 case 因为跑过 eval 被移出 rework，结果从没合回，94 个被送人工。
- batch2 流水线 2242 个，交付 2931 个多边形，多出的 689 个从没进流程，其中 441 个被自动放行。
- loader 对 2,674 行 / 2,589 个 case 只保留最后一个多边形。
- 1148905 拿到了 1061534 的决定通知页，日期碰巧合理，全部合理性检查都通过。
- site 和 location 两张图同名，OCR 和指北针结果互相覆盖。
- `Path.stem` 把 `TVN.2959_9` 截成 `TVN`，全部映射到同一个 case_id。

---

## C2 模块版本

**定义**
- `module`：流水线里一个可独立替换的部件，名字在 `reins.toml` 里登记，全局唯一（如 `extract`、`judge`）。
- `module_version` = `<module>-<suffix>-<YYYYMMDD>-<n>`，当天第一份 `-1`，第二份 `-2`。例：`extract-roadnames-20261002-1`。
  `module` 和 `suffix` 只用小写字母、数字、下划线（`-` 是分隔符）。
- 一个模块版本固定以下全部内容（缺一个都不算同一版本）：代码 commit + 文件范围、prompt 文本 sha256、模型 id、参数、外部资产（权重文件 sha256、vendored 代码的上游 commit）、价格表版本。
- `release` = 一组模块版本的组合，名字 `<pipeline>-<YYYYMMDD>-<n>`。生产批次只能跑 release。

**规则**
1. 每条输出记录盖戳：`module_version` + `batch_id`。
2. 一个批次内同一阶段出现两个模块版本 → 批次标记 `mixed_version`，必须显式确认。
3. 模块版本状态：`candidate` → `released`（只能经门禁） → `retired`。`reins module status extract` 回答"这个改动进生产了没有"。
4. 运行时缺环境变量导致模型静默退回旧版 = 启动失败，不许继续。

**为什么**
- rework 用了只做过抽样测试的 judge `c1e51ff`，漏了 2 个 HMLR `Fail`。
- batch2 先跑旧 commit `8b4fe21`，第 8 步切到 release-1，第 10 步又自动切到 release-2。
- 893 个 case 用旧 prompt、74 个用新 prompt，只有一个 json 记了。
- 改动只存在于临时驱动脚本里，"下次跑流程这些改进都会丢"。
- 不设 `OCR_DET_MODEL` 会静默退回 v5。
- 价格表是真实价格的一半，所有成本少报 2 倍；另一次少报 7.7 倍。
- 用户问"之前优化的流程加入主 `qa` 生产线了么"，答案要查分支才知道。

---

## C17 工件版本（2026-10-07）

**要版本的不止代码**（用户）：提示词、模型、benchmark 各自有版本，而且不止一套在用；工作流有版本，工作流用的工具也有版本。

| 对象 | 版本名 | 怎么得到版本 | 多套并存怎么表示 |
|---|---|---|---|
| 工具 | `module_version` `<module>-<suffix>-<day>-<n>` | `reins dev finish` 过门禁 | 一个模块可有多个 released 版本，release / 工作流指定用哪个 |
| 提示词 | `prompt-<name>-vN` | 按文本内容哈希：同一段文字同一个版本，改一个字就是新版本。文本从 `reins.toml [prompts]` 声明的位置读（Python 常量或文件），代码不用重写 | 模块版本的 pins 里写明它带的是哪个提示词版本 |
| 模型 | `model-<name>-vN` | `reins.toml [models]`：provider、id、固定参数；参数变了就是新版本 | 同上 |
| 工作流 | `workflow-<name>-vN` | `reins workflow freeze`：每个阶段的生产模块版本 + 当前提示词 / 模型版本冻成一套 | 批次 `--workflow` 指定跑哪一套；来源元组里带全套 |
| **机器学习权重**（自训 YOLO / ConvNeXt、MINIMA、Qwen、PaddleOCR） | `weights-<name>-vN` | 按文件内容哈希（目录按全部模型文件）；同一份权重同一个版本，重训出来的新文件就是新版本。训练记录从权重旁边的文件自动采集：ultralytics 的 `args.yaml` / `results.csv`（基座、数据集、epochs、imgsz、mAP），自家训练器的 `history.json` / `metrics.json` / `train_manifest.csv`（精度、样本数）。开批次前 preflight 核对声明的每个权重文件都在、且内容是登记过的版本，杜绝悄悄换模型 | 模块版本的 pins 里写明它加载的是哪些权重版本；工作流版本钉住整套 |
| **工具**（代码工具、外部接口、数据） | `tool-<name>-vN` | `reins.toml [tools.*]`：`kind = "code"` 按源文件内容哈希（可写仓库外的绝对路径，如 GeoPlanAgent）；`kind = "api"` 按 endpoint + 固定参数；`kind = "data"` 按仓库外文件的清单（名字、大小、最近修改），`hash = true` 时按内容。开批次前 preflight 核对数据类工具没被换掉 | 模块的 `tools` 钉进模块版本；工作流阶段的 `tools` 冻结进工作流版本 |
| benchmark | `bench-<name>-vN` | C4 | 门禁记录对着哪一版打分 |

状态：`candidate`（登记了还没进生产）→ `active`（被某个 released 模块版本或工作流用上）→ `retired`。
`reins artifact diff prompt-judge-v3 prompt-judge-v4` 看两版差异；`reins artifact show` 看它被哪些模块版本和工作流钉住。
重训一个模型：`reins weights register panel_yolo --file .../best.pt --run-dir ... --dataset ... --note "..."`（或者只要文件变了，`reins artifact scan` 就会登记新版本）；`reins weights list` 一屏看全部权重、大小、框架、主指标；`reins weights check` 核对当前文件。

## C3 批次

**定义**：`batch` = 为一个目的、对一个确定的 case 集合、用确定的模块版本，执行一组阶段。一次中断后续跑仍是同一个 batch（记为 `attempt`）。

`batch_id` = `<scope>-<type>-<YYYYMMDD>-<n>`。`scope` 是 1 到 4 段、由项目在 `reins.toml [batch] scope` 里命名的范围（e2e 是 客户-工作包：`sheffield-wp3-production-20261004-1`）。

`type` ∈ {`production`, `rework`, `experiment`, `pilot`, `eval`, `benchmark_build`, `smoke`}。

**批次记录（`batch.json`，开跑前写入）**
- 目的（一句话）、类型、父批次（是谁的子集 / 返工 / 重跑）
- case 集合：清单文件 + sha256 + 来源
- 计划阶段 vs 实际阶段（每阶段：模块版本、开始/结束、状态、输出路径、日志路径、产出计数）
- release、配置哈希、参数
- 持有者（会话 id、进程组 id、启动命令）
- 花费：预估、上限、实际
- 资源前提（需要 GPU 空闲吗）
- 别名：人对它的叫法（"第一批"、"rework"）登记在这里，所有地方只用 `batch_id`

**规则**
1. 没登记的批次不许跑。eval、试跑、小测也要登记（以前"这次是评估不是交付，所以没登记"）。
2. 输出写到批次自己的目录，不写共享文件。批次关闭后目录只读。
3. 每阶段逐条写结果（可续跑），不许只在结束时写；日志不许截断（`| tail -1`）。
4. `reins batch status <id>` 一屏回答：到哪一步、进度（按 case 守恒算，不按日志行算）、ETA、错误、花费。
5. 批次结束必须通知用户；用户不需要问"好了么"。

**为什么**
- rework 批次由另一个会话 `setsid nohup` 启动，这边不知道，kill 被权限拦。
- 第 11 步只在结束时写结果，杀掉就丢 3,337 张 plan_image 的描线。
- 进度面板把跳过的 104 个也算进去，显示 94.8%。
- "第一批"="rework"="batch1"="wp3-rework-e2e"；助手把"这 2600 个 rework 批次"理解成 eval-359。
- 重启后 systemd 把八月的 OCR 任务又拉起来，往已完成的 jsonl 里追加了 7,000 行。
- 单个 case 试跑覆盖了 `records.json`。
- 用户问"好了么"至少十几次。

---

## C4 Benchmark

**定义**：`benchmark` = 冻结的 case 集合 + 冻结输入 + 各阶段冻结输出 + 标签，名字 `bench-<名>-vN`。

**结构**
```
bench-demo-359/v6/
  MANIFEST.json   版本、创建时间、生成冻结输出的模块版本、各文件 sha256、基线数字、覆盖哪些阶段
  cases.csv       case + split（dev / holdout）
  inputs/         冻结输入
  stages/<stage>/ 各阶段冻结输出（标明由哪个模块版本产生）
  labels.jsonl    标签，见下
  CHANGELOG.md    每个版本为什么升
```

**标签分级**（每条标签一行，只追加）

| `label_source` | 可信度 | 用途 |
|---|---|---|
| `golden` | 最高 | 只能来自审核页上的人工裁定（谁、何时、看没看图纸） |
| `customer_result` | 中 | HMLR/LMK 结果，可能错（79109 是没看图纸推断的） |
| `reviewer_verdict` | 中 | 审核员结论，需校准（C6） |
| `reference_geometry` | 低 | 旧交付几何，**不是真值** |

**规则**
1. 冻结后只读（sha256 校验）。改任何东西 = 升版本 + CHANGELOG。
2. **开发集 / 封存集分离**：调规则、调阈值只能用 dev。holdout 只给门禁用，每次访问记日志；在 holdout 上调过参 = 它降级为 dev，需要补新的封存集。
3. 每个阈值登记：值、在哪个 split 上定的、样本量 n。n < 30 自动标 `low_n`，换 scope（新客户、新数据源）时提示重验。
4. 标签只能经审核页进入 golden；在对话里改标签 = 违规（以前助手把 93、95 直接补进标准答案，分数从 13 涨到 15）。
5. **覆盖声明**：门禁结果必须写明测了哪些阶段（以前 CI 只测 stage D，被当作"新裁图流程验证过了"）。
6. **确定性分级**：确定性阶段要求逐字节一致；非确定阶段（托管模型、带时间预算的搜索）要求跑 N 次、给容差；缓存回放和真实漂移测试分开。
7. **前提条件**：需要的资源状态写在 MANIFEST（如 GPU 空闲），不满足就拒绝打分。
8. **新鲜度**：冻结输出的模块版本 ≠ 当前 released 版本时，打分结果带警告（v1 的 georef 是 10-02 前的旧代码）。
9. **回流**：C6 的审核结论 → 候选标签 → 用户在审核页确认 → 进下一版本。

**为什么**
- 20 个调参 case 上 74%，19 个盲测 case 上 53%。
- pilot 61 个误报 23%，全量 151 个误报 43%。
- 阈值在 4 张图、13 张图、2 个错误、1 个 case 上定。
- gemini 对同一个 case 回答"错/对/对"，只看一次就淘汰了 luna-pro，后来推翻。
- HMLR 对"太小"宽松，address lane 在 eval 上 143/143 `Pass`，用户抽检发现错。
- GPU 被占时 3 个配准失败，被误当成版本退化。

---

## C5 门禁

**定义**：`gate` = 对一个 candidate 模块版本，在 benchmark 上跑规定层级，输出 `gate_status`。

**规则**
1. `reins.toml` 声明：每个模块改动必须过哪些层（如改 judge → `judges` 层；改上游 → 还要 `pipeline` 层，或写明跳过理由并记录）。
2. 全局只有两个主指标，所有项目同义：
   - `missed_error`：自动放行里实际是错的数量。**不许增加**（硬门禁）。
   - `review_load`：要人看的 case 数。只算成本。
3. golden 上原本正确的项变错 → 红（以前总数看起来没事，逐 case 比才发现挪坏了 3 个）。
4. 每次门禁写一行 `gate_log.jsonl`：模块版本、benchmark 版本、各层结果、和基线的逐 case 差异文件、跳过的层和理由。
5. 基线只能经 `reins gate approve` 移动，带理由。

---

## C6 人工审核

**规则**
1. 事件只追加（view / verdict / draw / delete），每条带审核员身份。
2. `wrong` 必须带 `error_type`（项目配置的枚举）；`unsure` 必须带备注。以前 64 个错误没类型、35 个疑问没备注。
3. 分配记录只追加；不许打乱审核员已有顺序。
4. 审核员校准：同一队列上审核员之间的差异定期出报告（Maggie 和 Yishan 在每个子组都差 6–24 个点）。
5. 审核平台显示的 lane 文件版本要和当前一致，不一致时页面上提示（以前规则改了 8773 还显示旧结果）。
6. 审核结论回流 C4。

---

## C7 主控路由

**主控路由是唯一入口**：任何批次、任何阶段、任何付费调用都经过它。它做四件事：准入、调度、成本控制、恢复。

**它管两类不可靠的工人**（2026-10-06 补）：
1. 流水线阶段和付费调用（托管模型、按次计费的接口）。
2. **Claude 会话本身。** 复盘里最常出事的是会话：另一个会话 `setsid nohup` 起了 rework 没人知道；把未审的提交合进 main；在运行目录里切分支；在对话里改标准答案。
   所以会话只能通过 `reins` 命令提议五类高风险动作：启动/停止批次、发布版本、改路由规则、花钱、写冻结或交付目录。

**强制手段分两层**：拿不到（锁）比被拦（护栏）可靠。
- 锁：付费 key 只在网关进程里（`reins gateway serve`）；冻结目录和 release worktree 只读；生产批次由 `reins run` 启动，进程组登记在主控名下；租约（C10）按 session id 判归属，fork 出来的会话什么都不拥有。
- 护栏：Claude Code PreToolUse hook（`hooks/guard.py`）拦 detached 启动、手工 merge/push main、写冻结目录、改别人的 worktree。出错时放行并记日志。

**和 LLM harness 的三点不同**：主控是确定性代码，不是模型；管的粒度是阶段和 case，不是每个工具调用；约束靠拿不到资源，不只靠拦截。

### 准入（开跑前，任一不满足就拒绝或停下问用户）
1. 批次已登记（C3），case 集合通过 C1 校验（无空 id、无重复、无表头混入）。
2. 每个阶段指定的模块版本是 `released`；生产/返工批次必须用 release。
3. 资源租约拿到了（GPU、内存上限、端口，C10）。
4. 成本预估 ≤ 批次上限 ≤ 可用余额（见下）。

### 成本控制
- **预算分层**：账户余额 / key 周限额 ⊇ 项目月预算 ⊇ 批次上限 ⊇ 阶段上限 ⊇ 单 case 上限。下层不能超上层。
- **新批次默认上限 $0**：付费阶段在用户给出上限前不运行（`reins approve <batch> --cap 8`）。
- **预估**：每个模块版本从历史账本得出"每 case 成本"和缓存命中率；预估 = 单价 × 需要该阶段的 case 数 × (1 − 预计命中率)。没有历史的新模块版本，先强制跑一个小 pilot（默认 20 个 case）测出单价，再报全量预估。
- **运行中**：每次付费调用经网关预留→结算（C9）。到批次上限 80% 推送通知；到 100% **暂停批次**并问用户加钱还是停，绝不静默转人工。
- **路由层省钱**：case 只有在便宜阶段判不了时才进付费阶段；每条路由规则登记"走这条路的单 case 预计花费"。
- **报表**：花费按 case、模块版本、批次、周汇总，进看板（C14）。

### 恢复（和 C8 Watchdog 配合）
Watchdog 负责发现，主控路由负责按预案处理。**自动修复只做运维动作，从不改代码**：

| 故障 | 自动动作 | 上限 |
|---|---|---|
| 进程死掉（OOM、GPU 被杀、会话结束、重启） | 从检查点续跑；并发减半；在内存上限内重启 | 3 次，超了暂停+通知 |
| 单个 case 卡住 | 杀掉该 case，标 `failed(timeout)`，批次继续；批次末尾用 2 倍超时重试一次 | 重试 1 次，仍失败进人工清单 |
| API 429 / 5xx / 超时 | 指数退避重试 | 5 次，仍失败标 `failed(api)` |
| GPU 被占 | 排队等租约 | 不限，通知一次 |
| 余额 / key 限额 / 批次上限用完 | 暂停批次，通知 | 需要用户给钱 |
| 磁盘 > 90% | 暂停批次，通知 | 需要用户清理 |
| 代码错误（同一阶段多数 case 报同一异常） | 暂停批次，通知；修复走 开发→门禁→release，之后从该阶段续跑，批次记为 `mixed_version` | 需要人 |

只有 `production` / `rework` 批次自动重启；`experiment` / `pilot` 失败就停，不浪费资源。

### 调度：分两级，别混

**C7a 批次编排**（一个批次的阶段怎么走）
- 阶段是声明式 DAG：`stage` 名、依赖、输入、输出、是否付费、是否需要 GPU、单条超时。
- 编排器负责：按依赖执行、续跑（只跑未完成的 case）、检查 C1 守恒后才进下一阶段（以前 OCR 没跑完就进了第 7 步）、付费阶段前停下等批准。
- 需要用户批准的点写死：开始付费、发布到 file-browser-data、交付、改基线。

**C7b case 路由**（一个 case 去哪个 lane）
- 规则是数据，不是散在代码里的 if：每条规则 `rule_id`、条件、目标 lane、证据（benchmark 版本 + n）、决定日期、决定人。
- 每个 case 记录命中的 `rule_id` 列表 → `reins case <id> --why` 回答"为什么是 Q1"。
- 规则变更自动生成 **lane 差异**：哪些 case 从哪个 lane 移到哪个 lane。没有差异报告的规则变更不许生效。
- 全局原则（所有项目共用）：
  1. 便宜的先跑；付费模型只处理本地判不了的。
  2. 没有证据不放行（没图纸 → 永不 auto_accept）。
  3. 模型用来"捞"问题，不用来"放行"；放行要求两个独立来源一致。
  4. 先用旧方法，失败才用新方法（不破坏已成功的）。
- `reins rules list` 用人话列出当前规则（用户说过"我忘记规则了你和我重新说一下"）。

---

## C8 Watchdog

**定义**：一个独立于任何 Claude 会话的守护进程（systemd 服务）。会话结束、重启都不影响它。

**看什么**（看进度，不看进程活着没）

| 监视项 | 触发 | 事故来源 |
|---|---|---|
| 进度心跳 | 批次的输出 N 分钟不增长 | 第 11 步卡 5 小时，进程一直"活着" |
| 单条超时 | 单个 case 超过阶段声明的超时 | 低对比度扫描让形态学运算跑 1 小时 × 22 |
| 内存 | 按速率预判，不等 3 次采样 | 16 进程解码把机器卡死；采样要 6 秒，来不及 |
| GPU | 占用 > 阈值前先拒绝新任务 | 92% 被杀，流水线带着残缺 OCR 继续 |
| 磁盘 | > 90% | /data 92%、94% |
| 花费 | C9 账本：接近上限、余额不足、key 周限额 | 余额中途耗尽，剩下 2,122 个 case 全转人工 |
| 错误分类 | 402/403/413/429 必须是独立状态，不能变成"无结果" | 413 被记成 "no plot" 还照样收费；98 个 429 当成无结果 |
| 静默退化率 | fallback 比例、空输出比例突增 | "只看到 N 就假设朝上"；灰度图把红线去掉 |
| 守恒 | C1 规则 1 失败 | 104、689、340 |
| 服务陈旧 | 审核平台加载的 lane 版本 ≠ 最新 | 8773 显示旧结果 |

**动作分级**（用户 2026-10-06 批准：可以自动暂停批次、自动杀超时 case）
1. 通知（推送给用户，带批次 id 和一句话）
2. 暂停批次（SIGSTOP 整个进程组，记录）
3. 杀掉单个卡住的 case，标 `failed(timeout)`，批次继续
4. 把事件交给主控路由，按 C7 恢复预案自动重启生产批次
5. **从不**改代码、改规则、改数据；修复走 C5

**附带**：进程登记表（哪个批次、哪个会话、哪个进程组），`reins ctl pause|resume|stop <batch_id>` 替代手找 PID。

---

## C9 花费账本

**预算池（2026-10-07 追加，用户要求）**：所有会话合计的花费受池约束，任何一个会话或批次都不能一次花很多。
限额在 `$REINS_HOME/config.toml [pool]`：`hourly_cap`、`daily_cap`、`weekly_cap`（经网关的全部花费，含预留）、`max_batch_cap`（单个批次的上限不能超过它）、
`per_session_daily_cap`（按批次的持有会话算）、`max_consecutive_failures`（同一阶段连续失败这么多次就暂停，不在钱上重试）。
两处执行：`reins batch approve` 时（单批上限、所有未关闭批次上限之和不超当日池）和网关预留时（小时 / 日 / 周 / 会话）。看板成本区第一行就是池的使用情况。
不做"关键任务预留"和"多 agent 交接"：批次串行、会话不互递任务，这两样在这里没有对象。

1. 所有付费调用经一个网关：缓存键 = 精确输入（含图片字节哈希 + prompt sha + 模型 + 参数）。
2. 调用前**预留**、返回后**结算**；并发下按预留算，不会超（以前 6 线程把 $0.5 上限超到 $0.559，40 并发 $7 超到 $7.47）。
3. 每笔归属到 `batch_id` + `module_version`（以前"另外约 4 美元不是这个会话花的"）。
4. 批次开跑前检查：余额、key 周限额、预估 vs 上限。
5. 价格表有版本；可以用 token 数重算历史花费，不用重跑。
6. 上限触发时的去向必须声明（停下等批准，而不是静默转人工）。

---


**全部开销（2026-10-09）。** 网关账本只覆盖经过网关的调用。网关之前（或绕过网关）的花费用 `reins spend import FILE.csv --project P --source '...'`
导入另一张只追加的表（`spend_import`），一个文件按内容只能导一次，来源说明必填。`reins spend all` 和看板"成本"页把两者合起来，按天、用途、
模型、批次展开，并和服务商自己报的累计用量（OpenRouter 的 usage）对账：账户上有、reins 说不出去向的钱单列为"未归属"。
导入文件和生成它的脚本放在数据目录（如 `$REINS_HOME/imports/<name>/`），可复核。
## C10 占用与租约

1. worktree、分支、批次目录、端口、GPU 各有一个持有者（会话 id），记在一个带文件锁的登记表里。
2. 别的会话动它前必须拿到租约或者持有者释放。
3. 正在运行的脚本、正在被批次使用的 release 目录不许改（bash 边跑边读脚本）。
4. GPU 是排队资源：申请 → 排队 → 获得 → 释放。用户随时可以抢占（`reins gpu take`）。

**为什么**：23 个 worktree、25 个分支、10 份 eval 拷贝；`board.json` 被并发写丢；另一个会话在 main 的 worktree 里留下未提交改动；在正在运行的 rework 目录里误切过分支。

---

## C11 验收

1. 每个交付 package 声明：抽样方法（固定 seed）、样本量、错误预算（如 315 抽检，理想错 3、上限 6）、判定人。
2. 结果是 `ship` / `rework` / `ship_with_note`，登记到 `runs.jsonl`，引用 batch_id 和 release。
3. 抽样样本冻结时记录每个 case 当时的 lane；之后 lane 变了，样本标记陈旧（以前 315 样本里 15 个已经不是自动放行）。
4. 批次专属的交付规则（如"这批 Q2 按 Q3 处理"）必须登记，不能只写在交付脚本里。

---

## C12 产物不可变

1. 交付件、冻结件、批次关闭后的输出：只增不改不删。重做 = 新文件名（带版本）。
2. 每个交付件旁边有 manifest：来源 batch、release、case 数、数据摘要（内容哈希，不用 xlsx 文件哈希）。
3. 客户文件只允许英文（已有规则，框架统一检查）。

---

## C13 决定记录

1. 每个决定一条：日期、内容、证据（批次 / benchmark / n）、决定人、状态（active / superseded by #）。
2. 被推翻的结论要原地标 superseded，不是只追加新的（以前"sol-pro 零漏放"被推翻后，另一个会话还在按它做实验）。
3. 会话开始时能查到当前 active 的决定。

---

## C14 主控看板

**界面结构（2026-10-09 重做）。** 前端是独立的静态文件（`reins/board_ui/` 下 index.html、app.css、app.js），不经构建，
只读 `/api/state`。左侧导航分两组：工作区（概览、流程图谱、运行、开发）和治理（成本、工具箱）。概览顶部是告警，
下面四个数（运行中、开发中、今日花费、最低余额），再下面是紧凑的流程图谱，最后左右两栏：正在运行和正在开发、最近发布。
点任何阶段卡片，右侧滑出这一阶段的详情（代码版本、所在批次、开发中、按架子分的工具和版本）。浅色和深色主题、
中英文都可切换（只存在浏览器里）。

**默认首屏是流程图谱（2026-10-08）。** 每个项目一行：最新冻结的工作流，按阶段从左到右一张卡。卡上有：一只像素小螃蟹
（`mascot`：box / scissors / compass / pencil / judge / wrench，不同帽子和道具；有批次在这一阶段时它会动）、阶段名和
步骤范围、这一阶段跑的每个代码模块的生产版本和一句话说明（主模块 + `also`）、正在跑的批次和进度、开发中的候选版本、
它从工具箱里拿的东西（按架子分：自训模型 / 开源模型 / 付费模型 / 提示词 / 代码工具 / 外部接口 / 数据，每件带版本号，
有更新版本时标 ↑）。下面可展开完整工具箱表：每件工具的版本、做什么、细节（大小、主指标、模型 id、文件数）、哪些阶段用。
图谱的数据只来自工作流版本；`reins dev finish` 发布后自动重新冻结（有变化才出新版本），模块说明也跟着 `reins.toml` 同步。

**只显示两样东西：运行中、开发中。** 其余（历史、已发布版本、成本汇总）收在第二个标签页，平时不看。

### 运行中（每个未关闭的批次一张卡）
| 字段 | 来源 |
|---|---|
| 批次 id、类型、目的一句话 | C3 |
| 当前阶段 / 总阶段，进度 n/N（按 case 账算） | C1 账本 |
| ETA（按最近 30 分钟速度，不含缓存命中） | C1 账本 |
| 健康：绿 / 黄（重试中、接近上限）/ 红（暂停、等人） | C8 |
| 自动恢复次数和最近一次原因 | C7 |
| 花费 已用 / 上限 | C9 |
| 用的 release、是否 `mixed_version` | C2 |
| 持有会话 | C10 |
| 等人的事（批准花费、修代码）放在卡片最上面 | C7 |

### 开发中（每个 `candidate` 模块版本一张卡）
| 字段 | 来源 |
|---|---|
| 模块、版本名、改动一句话 | C2 |
| 状态：开发中 / 过门禁中 / 门禁红 / 可发布 | C2 + C5 |
| 开发会话、分支、worktree | C10 |
| 最近一次门禁：`missed_error` 和 `review_load` 相对基线的变化 | C5 |
| 最后活动时间（超过 24 小时标"停滞"） | C2 事件 |
| 和其他开发卡片的文件冲突 | C10 |
| 开发花费 | C9 |

### 形式
- 本地网页，只读，30 秒自动刷新，所有数据来自同一个登记库（`$REINS_HOME/reins.db`，位置见 `~/.config/reins/settings.toml`）。
- 状态变化（批次完成、变红、等人批准、门禁出结果）同时推送通知，不用一直开着看板。

---

## C15 会话索引（2026-10-07）

**开发和运行是主体，会话是它们的来源索引**（用户 2026-10-07）：每个开发项（模块版本，或还没登记的改动）和每次运行（批次的启动 / 暂停 / 停止 / 实验）都记着它来自哪个会话、那个会话的记录文件在哪，用来回答"这个动作是怎么来的"。
查索引：`reins batch status B`、`reins dev show 模块或版本` 末尾的"来源会话"。

所有开发和运行都由 Claude Code 会话发起，通过 reins 命令做的事本来就带会话号；绕过 reins 的改动和运行由 hook 补上来源，索引才完整。
内部把会话归为 `develop` / `run` / `experiment` / `analysis` 之一，只用来把它挂到正确的开发项或运行上。
会话的动作来自 Claude Code hook（`hooks/session_hook.py`，装在用户级 `~/.claude/settings.json`，所有会话自动生效）：

| hook | 记录 / 动作 |
|---|---|
| SessionStart | 登记会话；按会话历史开头识别 fork；告诉会话它是谁、名下有什么、别的会话在做什么 |
| PreToolUse Bash | 命令分类（run_start / run_stop / experiment / merge / git / dev），只读命令不记；该走 reins 的动作拦下并记录 |
| PostToolUse Edit/Write | 改了哪个仓库、哪个 worktree、按 `reins.toml` 归到哪个模块 |
| Stop / SessionEnd | 最后活动时间；结束 |

**归属规则**：改文件的会话 → 该模块的开发（有登记版本就挂到版本上，没有就是"未登记的开发"）；启动 / 停止运行的会话 → 该批次的运行；不经 reins 启动的运行 → "未登记的运行"。会话也可以自己说明：`reins session bind --role ... --purpose ...`。

**冲突**（只看未结束的会话，24 小时窗口）：同改一个文件；同改一个模块；直接改 main；改到别的会话持有的 worktree；对别的会话的批次做运行 / 停止；2 小时内多个会话都启动了运行（抢 GPU）。

**看板不显示任何会话细节**（用户 2026-10-07）：只有"开发"和"运行"两栏。会话只用来把活动归到这两栏；冲突只作为卡片上的一句话（"另一项开发也在改 extract"），不出现会话编号和目录。会话细节在命令行：`reins session list / show / conflicts`。

## 已拍板（2026-10-06）
1. 规范词用英文，中文只作对照。
2. 模块版本：`<module>-<suffix>-<YYYYMMDD>-<n>`，当天第一份 `-1`。
3. Watchdog 可以自动暂停批次、杀超时 case；生产批次要能自动修复重启（运维层面，不改代码）。
4. 主控路由负责成本控制；需要主控看板，只关注运行中、开发中。
5. 先落地最基础的：C0 词表、C1 Case、C2 模块版本、C3 批次。

## 待定
- 审核队列（P1–P5）是否改名：等 C6 落地时再定。

## 版本对象一览（2026-10-07）

原则：凡是能改变结果的东西都有版本；每条产出记录带着生产它的全部版本（来源元组）。

| 对象 | 版本形式 | 命令 |
|---|---|---|
| 代码 + prompt + 模型 + 参数 + 资产 | `module_version` `<module>-<suffix>-<YYYYMMDD>-<n>` | `reins module` / `reins dev` |
| 一组模块版本 | `release` `rel-<project>-<YYYYMMDD>-<n>`，git tag + 只读树 | `reins dev release` |
| 批次 | `batch_id`，续跑 = 同批次新 attempt | `reins batch` |
| case 集合 / 配置 / 输入表 | 内容 sha256，存在批次记录里 | `reins batch open --config --input` |
| benchmark | `bench-<name>-vN`，冻结后只读，`labelset` 子版本 | `reins bench` |
| 阈值 | 每条带 n、split、benchmark；n < 30 标 low_n | `reins threshold` |
| 路由规则集 | `ruleset-<project>-vN`，v2 起必须附 lane diff | `reins rules freeze` |
| 运行环境 | `env-<name>-vN`，清单哈希相同即同版本 | `reins env snapshot / check` |
| 决定 | 编号 + superseded_by 链 | `reins decide` |
| 价格表 | 每条带 since | `reins spend price` |
| 词表 / 审核错误分类表 | `version` 字段，改义走 retired | `glossary.toml` / `review.toml` |
| 交付件 | `<stem>-vN` + manifest，只读，不覆盖 | `reins deliver register` |

来源元组：`reins case ID --provenance` → batch_id · module_versions · release · ruleset · env · config_sha · input_shas · case_set_sha。

## 实现状态（2026-10-07，v0.3）

| 契约 | 状态 | 在哪 |
|---|---|---|
| C0 词表 | 有：31 词 + `reins lint`（表头、文档），词表带 `version` | `reins/glossary.toml`, `glossary.py` |
| C1 Case | 有：清单校验、逐 case 事件、守恒门、`reins case [--provenance]` | `batches.py` |
| C2 模块版本 | 有：命名、candidate→released→retired、pins | `modules.py` |
| C3 批次 | 有：登记、阶段顺序、paid `spend_cap` 门、mixed_version、别名、来源元组、关闭冻结 | `batches.py` |
| C4 Benchmark | 有：init（dev/holdout 分割）、add-stage/input、分级标签（golden 必须真人 + 看过图）、freeze/verify/bump、holdout 访问记录与 compromised、阈值带 n | `bench.py` |
| C5 门禁 | 有：gate_log、green/red、只认冻结的 benchmark、compromised 标注 | `gate.py`, `dev.py` |
| C6 人工审核 | 有：只追加事件、署名、wrong 必填 error_type（项目 review.toml）、unsure 必填备注、分配只追加不重排、审核员校准、候选标签导出 | `review.py` |
| C7 主控路由 | 有：准入、`reins run`/`ctl`、规则即数据 + lane diff + ruleset 版本、preflight 门 | `runner.py`, `rules.py`, `preflight.py` |
| C8 Watchdog | 有 | `watchdog.py`, `systemd/` |
| C9 花费账本 | 有；drift 批次绕过缓存 | `spend.py`, `gateway.py` |
| C10 租约 | 有 | `leases.py`, `hooks/guard.py` |
| C11 验收 | 有：固定 seed 抽样、P/A/F 评分、lane 陈旧检查、ship / ship_with_note / rework | `accept.py` |
| C12 产物不可变 | 有：交付件契约检查（守恒、CJK、重复、截断长度、空/常量列、sheet 名）+ 版本化只读副本 + manifest | `deliver.py` |
| C13 决定记录 | 有 | `decisions.py` |
| C14 看板 | 有 | `board.py` |
| 输入体检 | 有：内置检查 + 项目 `[[preflight]]`；production/rework 第一阶段前必须通过 | `preflight.py` |
| 环境版本 | 有 | `envs.py` |
| 漂移测试 | 有：`--type drift` 批次不读不写缓存；比较用 `reins rules diff --lane <col>` | `gateway.py` |

下一步：接入第一个项目（`e2e-plan-extract`），只做接口：reins.toml、每步 `reins batch mark`、付费调用走网关、现有 benchmark 登记为 `bench-sheffield-wp3-359`。
