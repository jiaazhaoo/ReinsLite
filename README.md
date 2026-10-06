# ReinsLite

自动检查与审核流水线的管理体系：契约 + 登记库 + 命令行。第一个接入的项目是 `e2e-plan-extract`。

设计和全部契约：[docs/DESIGN.md](docs/DESIGN.md)。词表：[reins/glossary.toml](reins/glossary.toml)。

## 已落地（v0.1）

| 契约 | 命令 |
|---|---|
| C0 词表 | `reins lint PATH...`：检查表头（csv/tsv/xlsx）和文档用词 |
| C1 Case | 批次的 case 集合校验（空、重复、表头混入、格式）；每阶段逐 case 记结果；阶段结束前检查守恒；`reins case ID` 查一个 case 的全部经历 |
| C2 模块版本 | `reins module add / new / release / retire / status / list`；版本名 `<module>-<suffix>-<YYYYMMDD>-<n>` |
| C3 批次 | `reins batch open / stage-start / mark / stage-end / skip-stage / pause / resume / fail / close / status / list` |

登记库在 `$REINS_HOME/reins.db`（默认 `/data/reins`，不允许放在 `/env/code` 下）。

## 用法

```bash
bin/reins --help
bin/reins module add georef --project e2e-plan-extract --about "place plan images on the map"
bin/reins module new georef roadnames --about "plan road names before geocode"   # -> georef-roadnames-20261006-1
bin/reins batch open --project e2e-plan-extract --council sheffield --wp wp3 --type pilot \
    --purpose "try road names on 20 failing cases" --cases cases.csv --stage ocr --stage georef=georef-roadnames-20261006-1
bin/reins batch stage-start sheffield-wp3-pilot-20261006-1 ocr
bin/reins batch mark sheffield-wp3-pilot-20261006-1 ocr --file ocr_outcomes.tsv   # oachargeid, status, reason
bin/reins batch stage-end sheffield-wp3-pilot-20261006-1 ocr                       # 有 case 没结果就拒绝
bin/reins batch status sheffield-wp3-pilot-20261006-1
```

项目根目录放一个 `reins.toml`：

```toml
project = "e2e-plan-extract"
case_id_pattern = "^[0-9]+$"
```

## 测试

```bash
python3 -m unittest discover -s tests
```
