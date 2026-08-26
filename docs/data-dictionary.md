# Short Tracker 数据字典

本文档描述 Short Tracker 当前使用的外部来源、SQLite 表、派生字段、价格结果、缓存文件和本地 HTTP API。除非另有说明：

- 日期使用 ISO `YYYY-MM-DD`；
- 时间使用带时区的 ISO 8601，数据库和 API 优先使用 UTC；
- `NULL`/JSON `null` 表示未知、未提供或不适用，不表示数值为零；
- 空头仓位 API 数值是“占已发行股本的百分比数”，例如 `0.75` 表示 `0.75%`，不是小数比例 `0.0075`；
- SQLite 的 `*_bp` 是内部精确整数单位：`1 bp = 0.01` 个百分点，因此 `75 bp = 0.75%`；
- 价格保留 provider 的原始货币标签，尤其不会把 `GBp` 静默转换为 `GBP`。

## 1. FCA 外部来源

同步会读取六份 FCA 官方文件。每个字节不同的版本都会生成新的 `raw_snapshots` 记录和按 SHA-256 寻址的原始文件。

| `source_key` | 文件及 URL | 格式 | 是否形成激活数据集 |
|---|---|---|---|
| `legacy_named_xlsx` | [旧制个人净空头仓位](https://www.fca.org.uk/publication/data/short-positions-daily-update.xlsx) | XLSX | `legacy_named` |
| `ansp_current_csv` | [当前 ANSP](https://www.fca.org.uk/publication/documents/aggregated-current-net-short-positions.csv) | CSV | `ansp_current` |
| `ansp_historic_csv` | [历史 ANSP](https://www.fca.org.uk/publication/documents/aggregated-historic-net-short-positions.csv) | CSV | `ansp_historic` |
| `ansp_combined_xlsx` | [当前与历史 ANSP 合并文件](https://www.fca.org.uk/publication/documents/aggregated-net-short-positions.xlsx) | XLSX | 否；仅原始证据/交叉检查格式 |
| `rsl_csv` | [Reportable Shares List](https://www.fca.org.uk/publication/documents/uk-reportable-shares-list.csv) | CSV | `reportable_shares` |
| `rsl_xlsx` | [Reportable Shares List XLSX](https://www.fca.org.uk/publication/documents/uk-reportable-shares-list.xlsx) | XLSX | 否；仅原始证据/交叉检查格式 |

四个激活数据集由 `dataset_heads` 指向最近一次完整成功同步的快照。六份来源中的任一下载失败，或四个导入中的任一失败，都不会移动任何激活指针。

## 2. 官方源字段与内部映射

### 2.1 旧制具名披露 `legacy_named`

源文件是阈值披露事件，不是每天的持仓快照。

| FCA 列 | 内部字段 | 类型/单位 | 含义 |
|---|---|---|---|
| `Position Holder` | `position_holder` | text | 被公开的持仓人姓名或法人名称 |
| `Name of Share Issuer` | `issuer_name` | text | FCA 文件中的发行人名称 |
| `ISIN` | `isin` | text | 该披露行的证券 ISIN |
| `Net Short Position (%)` | `position_bp` | integer bp | 该持仓人的公开净空头仓位；小于 0.50% 的行用于关闭/移除公开状态 |
| `Position Date` | `position_date` | date | 仓位创建、变化或结束的日期 |

重建规则：

1. 状态键为 `(issuer_id, position_holder, isin)`；
2. `position_bp >= 50` 时，该持仓人贡献计入公开合计；
3. 后续低于 50 bp 的行，包括 0 或 0.49%，会移除该状态；
4. 同一状态键、同一日期出现多行时，按 FCA 源文件 `row_number` 顺序处理，最后一行获胜；
5. `legacy_issuer_aggregate` 在发行人有源事件的日期记录重建后的公开合计。

该合计只能表示旧制公开阈值以上持仓人的合计，不能恢复 0.50% 以下的真实仓位。

### 2.2 当前 ANSP `ansp_current`

| FCA 列 | 内部字段 | 类型/单位 | 含义 |
|---|---|---|---|
| `Name of Company` | `company_name` | text | FCA 公司名称 |
| `International Securities Identification Number (ISIN)` | `isin` | text | FCA RSL 所用主普通股 ISIN |
| `Aggregated net short position (%)` | `aggregate_bp` | integer bp | FCA 计算的公司级匿名净空头仓位合计 |
| `Position date` | `position_date` | date | 被纳入 ANSP 的通知中最新的 position date，不保证所有匿名构成都在该日更新 |

`ansp_current` 是 FCA 官方值；应用不重建、不拆分、也不推断匿名持仓人数量。

### 2.3 历史 ANSP `ansp_historic`

除当前 ANSP 的四个字段外，增加：

| FCA 列 | 内部字段 | 类型 | 含义 |
|---|---|---|---|
| `Date the aggregated net short position became historical` | `became_historical_date` | date | 该 ANSP 被新值替代、所有构成跌破报告门槛，或股票退出 RSL 的日期 |

数据库保留 FCA 原始 `position_date`。该字段是组成某个聚合值的通知中最晚的持仓日期；迟报或更正可以使连续两次聚合值的 `position_date` 倒退，因此它不是每个聚合值的生效横坐标。

API 按 ISIN 和 `became_historical_date` 升序，把 ANSP 值链接为 step-after 状态区间：

1. FCA 在 `2026-07-13` 首次发布的 ANSP 代表 `2026-07-09` 午夜的持仓。初始 RSL 批次即使 `date_added = 2026-07-13`，`ansp_scope_start` 仍为 `2026-07-09`；首次发布后新增的 RSL 股票使用实际 `date_added`；
2. 第一条历史值的 `date` 为 `max(ansp_scope_start, 第一条 position_date)`；
3. 后续历史值的 `date` 是上一条的 `became_historical_date`；
4. 当前值的 `date` 是最后一条历史值的 `became_historical_date`；没有历史值时，使用 `max(ansp_scope_start, 当前 position_date)`。

`first_published_on = 2026-07-13` 是公开制度标记，不替代上述持仓/区间生效日。API 同时保留原始 `position_date`、历史行的 `became_historical_date`、区间终点 `interval_end` 和 `chart_date_basis`，供 tooltip 与审计使用。若没有 current 行，最后一条历史区间在自身 `became_historical_date` 结束，此后必须显示断档，不得补零。

### 2.4 Reportable Shares List `reportable_shares`

| FCA 列 | 内部字段 | 类型 | 含义 |
|---|---|---|---|
| `Share ISIN` | `isin` | text | 报告清单中的股票 ISIN |
| `Company name` | `company_name` | text | 公司名称；拥有最高内部规范名称优先级 |
| `Date added` | `date_added` | date/null | 加入 RSL 的日期；FCA 为空时保留 null |
| `Class of share (Main or Other Class of Shares)` | `share_class` | text | 主股类或其他股类标识 |

RSL 用于标记当前 `reportable` 状态和选择 `primary_isin`，不是历史上所有曾被披露公司的全集。

## 3. 制度与度量边界

| 属性 | `legacy_named` 派生序列 | FCA `ansp_*` 序列 |
|---|---|---|
| 制度 | 旧制公开实名披露口径 | ANSP 于 2026-07-13 首次公开；首批值代表 2026-07-09 午夜持仓 |
| 公开门槛 | 单个持仓人通常 >= 0.50% | 纳入 FCA 汇总的单个可报告仓位通常 >= 0.20% |
| 身份 | 公开持仓人 | 匿名，不公开构成人数 |
| 数值形成 | 应用重放公开事件后求和 | FCA 官方公司级合计 |
| 可否视为全部市场空头 | 否 | 否 |
| 可否直接拼接 | 否 | 否 |

前后制度的水平差异可能完全或部分来自纳入门槛变化。API、图表和导出必须保留 `regime`；不得把两段数据改名为一个无分界的“short interest”。

## 4. SQLite 表

数据库默认路径为 `data/short_tracker.sqlite`。

### 4.1 `schema_migrations`

| 字段 | 类型 | 含义 |
|---|---|---|
| `version` | integer PK | 已应用的 schema 版本 |
| `applied_at` | text | SQLite 记录的应用时间 |

### 4.2 `raw_snapshots`

| 字段 | 类型 | 含义 |
|---|---|---|
| `id` | integer PK | 原始快照 ID |
| `source_key` | text | 六个 FCA 来源之一 |
| `source_url` | text | 实际下载 URL |
| `file_name` | text | FCA 官方文件名 |
| `sha256` | text | 下载字节的 SHA-256；与 `source_key` 联合唯一 |
| `byte_size` | integer | 文件字节数 |
| `content_type` | text/null | HTTP Content-Type |
| `archive_path` | text | 相对 `data/` 的不可变文件路径 |
| `first_retrieved_at` | text | 首次取得该哈希的 UTC 时间 |
| `last_checked_at` | text | 最近确认该版本的 UTC 时间 |
| `http_last_modified` | text/null | FCA HTTP `Last-Modified` |
| `etag` | text/null | FCA HTTP ETag |
| `effective_date` | text/null | 从 HTTP 元数据推定的来源有效日期 |
| `metadata_json` | text JSON | 预留的附加来源元数据 |

原始文件路径为：

```text
data/raw/<source_key>/<sha256>/<file_name>
```

### 4.3 `sync_runs`

| 字段 | 类型 | 含义 |
|---|---|---|
| `id` | integer PK | 同步批次 ID |
| `started_at` / `completed_at` | text/null | UTC 开始与完成时间 |
| `status` | text | `running`、`success` 或 `failed` |
| `force` | integer boolean | 是否忽略条件缓存强制获取 |
| `details_json` | text JSON | 各来源、导入及快照结果 |
| `error` | text/null | 失败类型和信息 |

### 4.4 `import_runs`

| 字段 | 类型 | 含义 |
|---|---|---|
| `id` | integer PK | 导入批次 ID |
| `dataset_key` | text | 四个激活数据集之一 |
| `snapshot_id` | integer FK | 使用的原始快照 |
| `importer_version` | integer | 解析/派生规则版本 |
| `started_at` / `completed_at` | text/null | UTC 时间 |
| `status` | text | `running`、`success` 或 `failed` |
| `row_count` | integer/null | 成功导入的源数据行数 |
| `profile_json` | text JSON | 日期范围、重复行计数、冲突样例等质量画像 |
| `error` | text/null | 导入错误 |

`(dataset_key, snapshot_id, importer_version)` 唯一，使相同文件和相同导入器的同步具有幂等性。

### 4.5 `dataset_heads`

| 字段 | 类型 | 含义 |
|---|---|---|
| `dataset_key` | text PK | `legacy_named`、`ansp_current`、`ansp_historic` 或 `reportable_shares` |
| `snapshot_id` | integer FK | 当前供查询使用的快照 |
| `import_run_id` | integer FK | 对应成功导入 |
| `sync_run_id` | integer FK | 激活它的完整同步批次 |
| `activated_at` | text | UTC 激活时间 |

所有读取查询通过该表选择一套最后已知良好数据。不要手工移动指针。

### 4.6 发行人身份表

#### `issuers`

| 字段 | 类型 | 含义 |
|---|---|---|
| `id` | integer PK | 内部稳定发行人 ID |
| `canonical_name` | text | 当前规范显示名称 |
| `normalized_name` | text | Unicode NFKC、casefold、标点折叠后的搜索名称 |
| `canonical_priority` | integer | 名称来源优先级：RSL 30、ANSP 20、legacy 10 |
| `created_at` / `updated_at` | text | UTC 时间 |

#### `issuer_identifiers`

| 字段 | 类型 | 含义 |
|---|---|---|
| `isin` | text PK | 精确 ISIN |
| `issuer_id` | integer FK | 关联发行人 |
| `first_seen_at` / `last_seen_at` | text | 首次/最近在来源中见到的时间 |

#### `issuer_aliases`

| 字段 | 类型 | 含义 |
|---|---|---|
| `id` | integer PK | 别名记录 ID |
| `issuer_id` | integer FK | 关联发行人 |
| `name` | text | 来源中的原始公司名称 |
| `normalized_name` | text | 可索引的规范化名称 |
| `source_key` | text | 产生该名称的 FCA 来源 |
| `first_seen_at` / `last_seen_at` | text | UTC 时间 |

身份解析先使用精确 ISIN；只在需要时回退到精确规范化名称。不进行模糊发行人合并，避免名称相似公司被错误拼接。Yahoo ticker 搜索是独立的建议流程，不会改变 FCA 发行人身份表。

### 4.7 `legacy_events`

| 字段 | 类型 | 含义 |
|---|---|---|
| `snapshot_id` | integer FK | 旧制 XLSX 快照 |
| `row_number` | integer | FCA 工作表源行号；与 snapshot 构成主键 |
| `issuer_id` | integer FK | 内部发行人 |
| `position_holder` | text | 公开持仓人 |
| `issuer_name` | text | 该行原始发行人名称 |
| `isin` | text | 该行 ISIN |
| `position_bp` | integer bp | 持仓百分比的精确整数表示 |
| `position_date` | date | FCA 仓位日期 |
| `row_hash` | text | 解析后关键字段的确定性 SHA-256，不等同于文件 SHA-256 |

### 4.8 `legacy_issuer_aggregate`

| 字段 | 类型 | 含义 |
|---|---|---|
| `snapshot_id` | integer FK | 来源快照 |
| `issuer_id` | integer FK | 发行人 |
| `position_date` | date | 该发行人发生一个或多个旧制披露事件的日期 |
| `aggregate_bp` | integer bp | 当日全部仍处于公开状态的具名持仓合计 |
| `active_holder_count` | integer | 当日合计中活跃的公开持仓状态数；同一持仓人不同 ISIN 状态可能分别计入 |
| `event_count` | integer | 当日应用的 FCA 源事件行数 |

### 4.9 `ansp_current`

| 字段 | 类型 | 含义 |
|---|---|---|
| `snapshot_id` / `row_number` | integer | 来源快照和源 CSV 行号，联合主键 |
| `issuer_id` | integer FK | 内部发行人 |
| `company_name` | text | FCA 公司名称 |
| `isin` | text | FCA 主普通股 ISIN |
| `aggregate_bp` | integer bp | 当前 ANSP |
| `position_date` | date | FCA position date |
| `row_hash` | text | 解析后关键字段的 SHA-256 |

### 4.10 `ansp_historic`

字段与 `ansp_current` 相同，另有：

| 字段 | 类型 | 含义 |
|---|---|---|
| `became_historical_date` | date | ANSP 成为历史的日期 |

### 4.11 `rsl_entries`

| 字段 | 类型 | 含义 |
|---|---|---|
| `snapshot_id` / `row_number` | integer | 来源快照和源 CSV 行号，联合主键 |
| `issuer_id` | integer FK | 内部发行人 |
| `company_name` | text | FCA 公司名称 |
| `isin` | text | 股票 ISIN |
| `date_added` | date/null | 加入 RSL 的日期 |
| `share_class` | text | Main 或其他股类标签 |
| `row_hash` | text | 解析后关键字段的 SHA-256 |

## 5. 查询服务对象

### 5.1 Security

| 字段 | 类型 | 含义 |
|---|---|---|
| `id` | integer | 内部发行人 ID |
| `name` | text | 规范显示名称 |
| `normalized_name` | text | 内部搜索名称 |
| `primary_isin` | text/null | 优先取 RSL 主股类，否则取已知 ISIN 排序首项 |
| `isins` | array[text] | 所有已关联 ISIN |
| `aliases` | array[text] | 所有 FCA 来源名称 |
| `reportable` | boolean | 是否存在于当前激活 RSL |
| `reportable_shares` | array[object] | 当前 RSL 股类明细 |
| `current_ansp` | object/null | 当前官方 ANSP、单位、position date 和 ISIN |

### 5.2 Short series

#### `legacy[]`

| 字段 | 类型 | 含义 |
|---|---|---|
| `date` / `position_date` | date | 旧制事件日期 |
| `value` | number | 旧制公开可见合计，百分点 |
| `unit` | constant | `percent_of_issued_share_capital` |
| `active_disclosed_holders` | integer | 活跃公开状态数 |
| `source_event_count` | integer | 当日源事件数 |
| `regime` | constant | `legacy_named_public_disclosures` |

#### `ansp[]`

| 字段 | 类型 | 含义 |
|---|---|---|
| `date` | date | 聚合值有效区间起点；首条使用 scope/组成通知日期，后续使用上一状态的历史化日期，不是网页公布日 |
| `position_date` | date | FCA 原始组成通知 position date；可倒退，不作为后续状态的生效日 |
| `became_historical_date` | date/null | 历史 ANSP 被替代日期；当前行没有 |
| `interval_end` | date/null | 该值的有效区间终点；历史行等于自身 `became_historical_date`，current 为 null |
| `chart_date_basis` | enum | `initial_ansp_scope_and_constituent_position_date` 或 `previous_became_historical_date` |
| `chart_interpolation` | constant | `step_after`；值从 `date` 起保持至 `interval_end` 或下一状态 |
| `ansp_scope_start` | date | 初始批次为 `2026-07-09`；首次发布后加入 RSL 的股票为其实际 `date_added` |
| `first_published_on` | date | `2026-07-13`；用于公开制度标记，不是每个值的横坐标 |
| `value` | number | FCA ANSP，百分点 |
| `unit` | constant | `percent_of_issued_share_capital` |
| `isin` | text | FCA ISIN |
| `is_current` | boolean | 是否来自当前 ANSP 文件 |
| `transition_date_clamped` | boolean | 兼容性诊断：`date` 是否不同于原始 `position_date`；日期依据以 `chart_date_basis` 为准 |
| `regime` | constant | `anonymous_fca_ansp` |

例如 4IMPRINT 的历史值依次为 `1.05% (position 2026-06-29, end 2026-07-14)`、`0.85% (position 2026-07-14, end 2026-08-03)`、`0.56% (position 2026-08-03, end 2026-08-05)`，而 current `0.27%` 的原始 position date 倒退到 `2026-05-01`。正确图表日期仍依次为 `2026-07-09`、`2026-07-14`、`2026-08-03`、`2026-08-05`。

`coverage` 为四个激活数据集分别返回快照 URL、哈希、大小、获取/导入/激活时间、行数和质量画像。它是页面“数据新鲜度”和审计信息的依据。

### 5.3 Current ranking item

当前做空比例排行榜只从 `dataset_heads.dataset_key = 'ansp_current'` 指向的激活快照读取。排行榜主键粒度是 FCA 当前 ANSP 行所代表的 reportable share/ISIN，不是 `issuer_id`；同一 `security_id` 因不同股类/ISIN 出现多行时不得自行求和、取最大值或去重。`security_id` 只用于点击后导航到详情页。

| 字段 | 类型 | 含义 |
|---|---|---|
| `rank` | integer | 当前激活快照在全局原始 `short_percent` 降序中的 1 起始稳定名次；不因搜索、分页或用户改用其他显示排序而重算 |
| `security_id` | integer | 内部发行人导航 ID；不是排行榜聚合键 |
| `name` | text | FCA 当前 ANSP 行对应的公司名称 |
| `isin` | text | FCA 当前 ANSP 行的 ISIN；排行榜的证券/股类粒度标识 |
| `ticker` | text/null | 已保存的本地 Yahoo 行情代码映射；缺失不影响 FCA 排名 |
| `ticker_provenance` | object/null | 行情代码的独立本地来源、更新时间和复核提示；与 FCA ANSP provenance 分开。若同一发行人当前出现多个 ISIN 且映射没有明确 ISIN，服务不会复用 issuer 级 ticker |
| `short_percent` | number | FCA `Aggregated net short position (%)`，单位为百分点；例如 `1.25` 表示 `1.25%` |
| `aggregate_bp` | integer | 与 `short_percent` 完全对应的内部精确整数 bp；`125` 表示 `1.25%` |
| `position_date` | date | FCA 披露中的 position date，不是下载日、同步日或页面访问日 |
| `position_age_days` | integer/null | `age_reference_date` 与有效 `position_date` 的 UTC 日历日差；未来或无法解析的日期为 `null`。数值较大本身不证明数据下载失败或快照过期 |
| `position_date_in_future` | boolean | 有效日期晚于 `age_reference_date` 时为 true；不会把未来日期伪装成“今天” |

全局 `rank` 的规范顺序为 `aggregate_bp DESC`，同值再按 `company_name COLLATE NOCASE ASC`、公司名原文 `ASC`、`isin ASC`、FCA 源 `row_number ASC` 稳定打破。源行号用于确定性排序，不要求作为公开响应字段返回。

[FCA Handbook SSR 6.3.1G](https://handbook.fca.org.uk/handbook/ssr6/ssr6s3) 规定，`position_date` 是纳入相关 ANSP 计算的通知中最近的持仓日期；即使 FCA 后来收到的某份通知包含更早的持仓日期，也不改变这一字段的定义。因此当前快照可以合法包含很早的 `position_date`。判断文件新鲜度应查看顶层 `source`/`coverage` 的快照检查、获取和激活时间，不能只看该行日期。

## 6. Yahoo 价格对象

Yahoo 是独立、可替换的第三方 provider。价格对象不能写入 FCA 权威表。

### 6.1 Symbol suggestion

| 字段 | 类型 | 含义 |
|---|---|---|
| `symbol` | text | Yahoo ticker；伦敦证券通常以 `.L` 结尾 |
| `display_name` | text | Yahoo 名称 |
| `exchange` / `exchange_display` | text | 交易所代码和显示名称 |
| `quote_type` | text | 当前只接受 equity 候选 |
| `score` | number 0..1 | 基于 ISIN 查询、名称相似度和伦敦市场的排序分数，不是身份保证 |
| `matched_by` | array[text] | `isin_query`、`company_name_query` 或 `manual_override` |
| `source_url` | text | 此候选使用的搜索 URL；人工覆盖为空 |
| `is_manual_override` | boolean | 是否由用户精确输入 |

`review_recommended` 表示自动结果分数较低或候选接近。即使为 false，也应在首次映射时核对 ISIN、股类、交易所和币种。

### 6.2 Daily price bar

| 字段 | 类型 | 含义 |
|---|---|---|
| `date` | date | 交易日期 |
| `open` / `high` / `low` | number/null | provider 原始 OHLC；缺失时保留 null |
| `close` | number | 原始收盘价 |
| `adjusted_close` | number | Yahoo 调整后收盘价；Yahoo 未提供时回退为 close |
| `volume` | integer/null | 成交量 |

历史结果另带 `symbol`、`display_name`、`currency`、`exchange`、`exchange_timezone`、请求日期范围、`first_trade_time_utc`、`fetched_at_utc`、来源 URL、attribution 和 limitations。

### 6.3 Latest price

| 字段 | 类型 | 含义 |
|---|---|---|
| `price` | number | 时间戳最新的分钟 close 或 regular-market price |
| `as_of_utc` | datetime | 该价格观察的时间，而不是抓取时间 |
| `price_kind` | text | `intraday_close`、`regular_market_price` 或极端情况下的 `previous_close_fallback` |
| `previous_close` | number/null | provider 前收盘 |
| `day_high` / `day_low` | number/null | provider 当日高低 |
| `day_volume` | integer/null | provider 当日成交量 |
| `market_state` | text | `PRE`、`REGULAR`、`POST`、`CLOSED` 或 `UNKNOWN` |
| `delayed_by_minutes` | integer/null | Yahoo 明示的延迟；null 不代表实时 |
| `fetched_at_utc` | datetime | 本地抓取时间 |
| `currency` | text | 常见为 `GBp`；不要按 `GBP` 格式化 |

任何消费者判断“是否最新”时都必须使用 `as_of_utc`，不能使用缓存读取时间代替行情时间。

## 7. 本地价格缓存与代码映射

### 7.1 `data/cache/prices/*.json`

文件名为：

```text
<safe-symbol>-<symbol-sha256-prefix>-<kind>.json
```

外层 envelope：

| 字段 | 类型 | 含义 |
|---|---|---|
| `symbol` | text | 规范化大写 ticker |
| `kind` | text | `history` 或 `latest` |
| `cached_at_utc` | datetime | 写入缓存时间 |
| `payload` | object | provider 结果 |

命中缓存后响应增加：

```json
{
  "cache": {
    "hit": true,
    "cached_at_utc": "2026-08-24T10:00:00+00:00",
    "age_seconds": 42
  }
}
```

正常缓存 TTL 为：

- `history`：12 小时；
- `latest`：2 分钟。

`GET /api/security/{id}/prices?refresh=1` 会绕过上述正常 TTL。若 Yahoo 刷新失败，服务可在最多约 10 年的旧缓存范围内回退，并在 `cache.stale_fallback` 和响应 `warnings` 中明确标记。未来时间戳、超过回退范围或无法解析的缓存均视为未命中。缓存采用同目录临时文件后原子替换；解析失败不会污染 FCA 数据。

### 7.2 `data/settings/price-symbols.json`

根对象包含 `version` 和以 `issuer_id` 字符串为键的 `mappings`：

| 映射字段 | 类型 | 含义 |
|---|---|---|
| `symbol` | text | 用户确认或自动建议的 Yahoo ticker |
| `source` | text | 当前为 `user_reviewed` 或 `automatic_yahoo_suggestion` |
| `display_name` | text/null | 候选显示名称 |
| `review_recommended` | boolean | 是否仍建议人工复核 |
| `updated_at_utc` | datetime | 最近保存时间 |

该设置独立于 SQLite/FCA 快照。同步 FCA 不会覆盖用户已确认的 ticker。

## 8. HTTP API 契约

服务只监听 `http://127.0.0.1:8777`。以下为当前前端使用的规范契约；后端可返回额外审计字段，客户端应忽略未知字段。

### `GET /api/health`

当前实现把 `/api/health` 作为 `/api/status` 的别名，返回同一完整状态。成功响应至少包含：

```json
{"ok": true}
```

### `GET /api/status`

```json
{
  "ok": true,
  "ready": true,
  "service": "UK Short Tracker",
  "mode": "local_read_only_research",
  "last_sync_at": "2026-08-24T09:00:00Z",
  "security_count": 1234,
  "datasets": {},
  "sync": {},
  "regime_start": "2026-07-13"
}
```

### `GET /api/rankings/current`

严格读取当前激活的 `ansp_current` 快照。查询参数：

| 参数 | 默认值 | 约束与含义 |
|---|---|---|
| `q` | 空 | 按公司名称、ISIN 或已保存 ticker 搜索；只过滤结果，不改变每行的全局 `rank` |
| `sort` | `short_percent` | 允许 `short_percent`、`name`、`position_date` |
| `order` | `desc` | 允许 `asc` 或 `desc` |
| `page` | `1` | 从 1 开始的页码 |
| `page_size` | `50` | 每页行数，最大 `2000`；前端为本地 Top-N、搜索、排序和分页交互会请求 `2000` |

规范响应：

```json
{
  "ok": true,
  "items": [
    {
      "rank": 1,
      "security_id": 123,
      "name": "Example plc",
      "isin": "GB00EXAMPLE1",
      "ticker": "EXM.L",
      "short_percent": 6.78,
      "aggregate_bp": 678,
      "position_date": "2026-08-21",
      "position_age_days": 3
    }
  ],
  "count": 1,
  "total": 321,
  "page": 1,
  "page_size": 50,
  "total_pages": 7,
  "sort": "short_percent",
  "order": "desc",
  "as_of_date": "2026-08-22",
  "age_reference_date": "2026-08-24",
  "age_reference_timezone": "UTC",
  "source": {
    "authority": "UK Financial Conduct Authority",
    "dataset": "aggregated_current_net_short_positions",
    "source_key": "ansp_current_csv",
    "url": "https://www.fca.org.uk/publication/documents/aggregated-current-net-short-positions.csv",
    "snapshot_id": 2,
    "sha256": "..."
  },
  "source_name": "FCA current aggregate net short positions (ANSP)",
  "source_total": 321,
  "source_limit": 2000,
  "source_truncated": false,
  "coverage": {},
  "methodology": {}
}
```

- `count` 是本页实际返回行数；`total` 是应用 `q` 后、分页前的已载入匹配总数；当前来源未截断时，它就是完整匹配总数；
- `as_of_date` 是当前快照中最新的 FCA `position_date`；`age_reference_date` 是服务计算 `position_age_days` 时使用的 UTC 日历日期，两者都不替代每行 FCA `position_date`；
- `age_reference_timezone` 固定为 `UTC`；未来或无效日期不会被伪装成 0 天；
- `source` 是 FCA/ANSP 来源对象，包含权威机构、数据集、官方 URL、快照 ID 和 SHA-256；没有激活快照时可为 `null`。`source_name` 是界面可直接显示的短标签；权威审计身份以 `source` 和 `coverage` 为准；
- `source_total`、`source_limit`、`source_truncated` 说明数据层总体及 2,000 行载入上限。若 `source_truncated=true`，API 的搜索、排序、`total` 与分页只覆盖已载入行，不能描述为完整总体；
- `methodology` 重申 ISIN 粒度、排序规则、报告门槛及非完整 short interest 限制；
- `rank` 始终来自未过滤的当前快照规范降序，搜索、分页和 `name`/`position_date` 显示排序不会重编号；
- 前端 Top-N 是对这组当前快照结果的展示截取，不会重新聚合公司或跨股类相加。点击一行使用 `security_id` 进入历史双图。

### `GET /api/securities?q=<text>`

```json
{
  "items": [
    {
      "id": 123,
      "name": "BP p.l.c.",
      "isin": "GB0007980591",
      "market": "UK market",
      "price_symbol": "BP.L"
    }
  ]
}
```

搜索匹配公司规范名称、FCA 别名和 ISIN，也会优先匹配本地已保存 ticker 的精确值或前缀。只有在这些本地结果均为空且查询形似短 ticker 时，服务才会进行一次 Yahoo 候选查询；候选 symbol 必须与输入的完整 ticker 或基础 ticker 精确对应，再通过候选公司名称回查 FCA 身份。此回查不会写入行情映射，Yahoo 不可用时也不会破坏离线的 FCA 名称/ISIN 搜索。

### `GET /api/security/{id}`

返回 `security` 对象、同步时间和来源信息。`id` 是内部发行人 ID；实现也可在内部服务层接受精确 ISIN。

### `GET /api/security/{id}/short-series`

规范响应可提供按日期合并的 `items`：

```json
{
  "items": [
    {"date": "2026-07-09", "legacy_percent": null, "ansp_percent": 1.05}
  ],
  "legacy": [],
  "ansp": [
    {
      "date": "2026-07-09",
      "position_date": "2026-06-29",
      "became_historical_date": "2026-07-14",
      "interval_end": "2026-07-14",
      "value": 1.05,
      "chart_date_basis": "initial_ansp_scope_and_constituent_position_date",
      "chart_interpolation": "step_after",
      "ansp_scope_start": "2026-07-09",
      "first_published_on": "2026-07-13"
    }
  ],
  "coverage": {},
  "methodology": {},
  "latest_date": "2026-08-21"
}
```

前端也接受分别提供的 `legacy` 和 `ansp` 数组。不要通过填补数值把两个制度变成同一个连续指标；即使两种口径都出现 `2026-07-09`，也不得连接两条 trace。没有 current ANSP 时，应在最后一条历史值的 `interval_end` 终止 ANSP trace 并保留断档。

### `GET /api/security/{id}/prices`

```json
{
  "items": [
    {"date": "2026-08-21", "close": 549.5}
  ],
  "symbol": "BP.L",
  "currency": "GBp",
  "source": {"name": "Yahoo Finance"},
  "latest_date": "2026-08-21",
  "cache": {"hit": false, "age_seconds": 0}
}
```

`close` 与 `currency` 必须共同解释。响应同时包含完整 `history`、`latest`、`mapping`、`warnings` 和缓存信息。若发行人还没有行情映射，本端点会先使用公司名称及 `primary_isin` 自动建议并保存 `automatic_yahoo_suggestion`，所以这个 GET 可能写入本地 settings；它仍不会修改任何外部系统。`?refresh=1` 绕过 12 小时历史/2 分钟最新价的正常 TTL。

### `GET /api/price-search?q=<text>`

```json
{
  "items": [
    {"symbol": "BP.L", "name": "BP p.l.c.", "exchange": "LSE", "currency": null}
  ]
}
```

这是 Yahoo 候选搜索，不会自动改变发行人身份或保存映射。搜索结果本身不调用 chart 端点，因此 `currency` 当前为 null；币种要到实际价格读取后才能确定。

### `POST /api/security/{id}/price-symbol`

请求：

```json
{"symbol": "BP.L"}
```

成功响应至少包含 `ok: true`。该操作只更新 `data/settings/price-symbols.json`，不写 FCA 表。

### `POST /api/sync`

请求正文可为空或为：

```json
{"force": true}
```

服务以 HTTP 202 接受后台同步，并返回 `accepted` 和当前同步快照。已有同步正在运行时返回 HTTP 409 和 `sync_running`；重复同步不得并发移动不完整的数据集头。

### 错误响应

可预期错误使用相应 HTTP 状态，并采用：

```json
{
  "ok": false,
  "error": {
    "code": "security_not_found",
    "message": "未找到该证券。"
  }
}
```

JSON POST 正文上限为 64 KiB，必须是 UTF-8 JSON 对象。

## 9. 数据质量与使用约束

- FCA 文件是仓位持有人提交数据的公开结果，可能因迟报、更正或核验而被追溯修改；原始快照用于识别这些修订。
- `UNKNOWN`、null、空列表或没有当前 ANSP 不等于确认不存在空头仓位。
- 排行榜缺少某个证券不等于其市场 short interest 为零；ANSP 只包含达到 FCA 报告门槛并被纳入计算的净空头仓位。
- 排行榜以当前 ANSP 行/ISIN 为粒度；不得因 `security_id` 相同而合并不同股份类别。
- 当前快照中的较早 `position_date` 不等于文件下载失败或快照陈旧；下载新鲜度必须从 `source`/`coverage` 判断。
- ANSP `position_date` 可以因迟报或更正而倒退；图表必须使用链接后的有效区间 `date`，tooltip/审计仍显示原始日期。
- `2026-07-13` 是首份 ANSP 的公开制度标记，首批 ANSP 的持仓范围从 `2026-07-09` 午夜起；两个日期不得互相替代。
- 缺少 current ANSP 表示公开数据在最后一个 `interval_end` 后未知，必须画断档而不是 0%。
- FCA 没有发布的阈值以下仓位、豁免仓位及无法公开的构成不能由本工具补全。
- 旧制行数、持仓人数和新制 ANSP 不能相互推导。
- 价格代码自动匹配必须复核；FCA ISIN 与 Yahoo ticker 是两个独立身份域。
- Yahoo 行情是非执行级行情，也不是监管数据；延迟字段为空不能解释为实时。
- 所有图表或导出必须保留来源、货币、观察日期、制度标签和数据覆盖说明。
