# 数据质量表改为接口获取：方案

把「数据质量表」的 Excel 上传替换为调用后端接口 `POST /flight/data-quality/query`（验价生单数据质量），并按产品给的《数据质量分析规则》为问答提供分析工具。拉取与缓存的做法参考查订比接口（`app/erp/look2book.py` + `app/erp/cache.py`）：历史整天缓存、当天实时拉取、每天 8 点同步前一天。

## 0. 现状

- 数据质量表目前只能上传 Excel，写入 `supply_quality`，字段按 Excel 表头命名（`verify_total`、`timeout_5s`…）。
- 除了 `get_data_coverage` 展示日期范围外，**没有任何分析工具使用过这张表**。所以替换数据来源的同时，必须补上分析能力，否则接口数据拉回来也无处可用。

## 1. 已确认的决策

| 事项 | 结论 |
|---|---|
| 接口地址 | 环境变量 `DATA_QUALITY_BASE_URL`，必填，部署时手动填写；不与 `ERP_BASE_URL` 绑定 |
| 鉴权 | 内网访问，免鉴权。不带 Cookie，不带 `Authorization` |
| `Operator`、`B2bCode` 请求头 | 不传，接口有默认值 |
| 旧的上传数据 | 不保留。迁移时删除 `supply_quality` 表和 `file_type = quality` 的上传批次 |
| 数据归属 | 接口对所有人返回同一份数据，因此缓存**全局共享一份**，不按 `user_id` 隔离，也不需要服务账号登录。与订单、查询量"仅本人可见"的规则不同，原因是这份数据本来就不区分人 |
| 时间区间 | 按天拉取传 `start_time = 当天 00:00:00`、`end_time = 当天 23:59:59`（北京时间）。接口若按半开区间处理，最多少 1 秒的数据，可忽略 |
| 分页 | `page_size = 500`，累计条数达到 `total` 即停，`MAX_PAGES` 防御上限 |
| 比率字段 | schema 写 `integer`，示例是小数。按 `numeric` 存，仅作"源字段"对照；计算一律用数量字段反算（规则文档要求） |
| 时间段分析 | 一期支持。整天数据走缓存；某个时间段（小时级，如 10:00～12:00）每次实时调接口，不落库。多日同一时段时逐日各调一次，最多 7 天 |

## 2. 数据模型

新建两张表，不改造 `supply_quality`（字段名、唯一键、`batch_id`、`user_id` 都不再适用）。

```text
data_quality_stat                     验价生单数据质量（接口数据，整天，全局一份）
  id                    bigserial
  stat_date             date
  agent_code            varchar(64) 渠道（客户）
  supplier_code         varchar(64) 供应商
  ticket_carrier        varchar(8)  航司
  total                 bigint      以下数量字段与接口同名
  timeout_5_10          bigint
  timeout_gt_10         bigint
  verify_success        bigint
  verify_fail           bigint
  supplier_success      bigint
  supplier_fail         bigint
  supplier_changed      bigint
  unbook                bigint
  booking_success       bigint
  booking_fail          bigint
  booking_rate          numeric(10,6)  源字段，只用于"口径不一致"对照
  booking_success_rate  numeric(10,6)  同上
  timeout_rate          numeric(10,6)  同上
  唯一键 (stat_date, agent_code, supplier_code, ticket_carrier)
  索引   stat_date

data_quality_sync                     历史整天已缓存的标记（当天不记）
  stat_date  date   主键
  row_count  int
  synced_at  timestamptz
```

接口的 `summary`（每日汇总）不单独存：它就是 `items` 按日求和，工具里现算。拉取时用它核对一次求和结果，不一致记日志。

迁移 `xxxx_data_quality_api.py`：建上面两张表；`DELETE FROM upload_batch WHERE file_type = 'quality'`；`DROP TABLE supply_quality`。不支持回滚（数据已删）。

## 3. 接口客户端与缓存

### `app/erp/data_quality.py`（新）

- `DataQualityRow`：与表字段一一对应的 dataclass。
- `DataQualityClient(base_url)`：
  - `fetch_range(start: datetime, end: datetime) -> list[DataQualityRow]`：按分页规则拉取任意时间区间。同一键重复出现取最后一条。
  - `fetch_day(day)`：`fetch_range(day 00:00:00, day 23:59:59)`，整天缓存用。
  - 时段用 `fetch_range(day HH:MM:00, day HH:MM:00)`，即「10:00～12:00」就是 10:00:00 到 12:00:00，不多算一分钟；结束为 23:59 时取到 23:59:59，与整天口径一致（`period_bounds`）。
  - 请求头只带 `Cloud-Trace-Id`（随机 uuid，便于后端查日志）和 `Content-Type`。
  - 响应校验：非 200、非 JSON、`code != 0`、`data` 结构不对 → `ErpUpstreamError`，带上 `message`。401/403 也按上游错误处理（接口免鉴权，出现就是环境问题）。
  - 每行只按接口字段取值，缺失按 0；比率字段用 `Decimal`。

### `app/erp/quality_cache.py`（新，结构参考 `cache.py`）

- `ensure_days(session, days, client=None, force=False)`：`force=True` 时历史日也重新拉取（每日 8 点同步和手动回填用）。历史日查 `data_quality_sync`，缺的逐日拉取后 `_replace_day`（先删该天的行再 upsert）并记同步表；当天每次实时拉取覆盖、不记同步表。接口错误统一转成 `ImportValidationError("数据质量获取失败：…")`，模型能原样告知用户。
- `load_rows(session, day_from, day_to, period=None, client=None) -> list[DataQualityRow]`：工具层统一入口。`window` 为整天时先 `ensure_days` 再读库；为时段时逐日 `fetch_range` 实时拉取，结果只在内存里聚合，不写库、不记同步表。时段最多 7 天（每天一次接口调用，避免一次问答等太久）。
- 不需要 `erp_sid`、`user_id` 参数，也不需要 `make_service_client`。
- 只写 `stat_date` 等于该天的行；接口返回了其他日期的行时丢弃并记警告日志，同步表记实际写入行数。
- 并发：两个用户同时触发同一历史日拉取时，`_replace_day` 先删后 upsert，结果一致，同步表用 `merge`，不会报错。

### 每日同步 `app/erp/sync.py`（改）

- 8 点任务里在查订比之后再同步前一天的数据质量，独立 advisory lock key（锁由独占连接持有，不受其他会话提交、回滚影响），一个失败不影响另一个。
- 数据质量的前一天用 `force=True` 重拉，覆盖 0～8 点间用户按需拉到的、可能不完整的缓存。
- 数据质量同步不依赖 ERP 服务账号，未配置服务账号时查订比同步跳过、数据质量同步照常。
- 配置：沿用 `QUERY_SYNC_ENABLED` 控制整个定时任务，不再加开关。

### 历史数据回填（启动时自动执行）

目标：代码部署到测试环境后，不用人工操作，自动把 2026-09-22 到昨天的数据补齐。

- 配置 `DATA_QUALITY_BACKFILL_FROM=2026-09-22`。后端启动时，定时任务线程在进入每日循环之前先执行一次回填：对 `[BACKFILL_FROM, 昨天]` 逐日调用 `ensure_days`。
- 幂等：`ensure_days` 只拉 `data_quality_sync` 里没有的日期，所以每次重启只补缺的天，已有的不重复请求；全部补齐后启动时只查一次同步表就结束。
- 并发：与每日同步共用同一个 advisory lock，多实例只有一个在补；补的过程中用户问答触发同一天的按需拉取也不冲突（先删后写，结果一致）。
- 失败处理：某一天拉取失败记日志并继续下一天，不中断启动，也不影响服务提供接口；下次重启会再补这几天。启动不等待回填完成（后台线程）。
- 按 30 天、每天几页估算，整个回填一两分钟内完成；完成后记一条日志"数据质量回填完成：补了 N 天"。
- `DATA_QUALITY_BACKFILL_FROM` 为空则不回填。日期晚于昨天也不回填。

另提供手动命令，用于临时补某个区间或接口改口径后重拉（会先删同步标记再拉）：

```bash
docker exec fa-backend python -m app.erp.quality_backfill 2026-09-22 2026-10-21 --force
```

## 4. 分析指标 `app/metrics/data_quality.py`（新）

全部按规则文档实现，纯函数部分不依赖数据库，便于单测。

**聚合**：`aggregate(rows, dims)` 先对数量字段求和，再算比率；分母为 0 返回 `None`，输出文案"无有效数据"，绝不返回 0%。

**比率**（每个分组都算全）：

| 指标 | 公式 |
|---|---|
| 渠道校验成功率 / 失败率 | `verify_success / total`、`verify_fail / total` |
| 报表超时率（`timeout_rate`） | `(timeout_5_10 + timeout_gt_10) / total` |
| 校验成功内超时率 | `(timeout_5_10 + timeout_gt_10) / verify_success`，再拆 5～10 秒、>10 秒两项 |
| 供应校验总数 / 成功率 / 失败率 | `supplier_success + supplier_fail`；`supplier_success / 总数`；`supplier_fail / 总数` |
| 供应变价率 | `supplier_changed / supplier_success` |
| 渠道预定率 | `(booking_success + booking_fail) / (booking_success + booking_fail + unbook)` |
| 预定成功率 / 失败率 | `booking_success / (booking_success + booking_fail)`、`booking_fail / …` |
| 未预定率 | `unbook / (booking_success + booking_fail + unbook)` |

**关注等级** `attention(sample, fail_rate)`：样本量渠道、航司用 `total`，供应商用 `supplier_success + supplier_fail`。判定顺序：分母 0 → 无有效数据；样本 >100 且失败率 ≥10% → 重点关注；样本 ≥20 且 >10% → 需要关注；样本 <20 且 >10% → 展示但不提醒（标 `小样本`）；其余不标。用未四舍五入的原始比率判断，展示保留两位小数。

**失败贡献率**：供应商 = 该供应商 `supplier_fail` / 该渠道全部供应商 `supplier_fail`；航司 = 该航司 `verify_fail` / 该渠道全部航司 `verify_fail`。

**排序**：重点关注 → 需要关注 → 其余；同级按贡献率降序，再按失败数降序；小样本高失败率永远排在有等级的对象之后。

**数据校验** `check_rows(rows)`（规则第七节）：`total == verify_success + verify_fail`、`timeout_5_10 + timeout_gt_10 <= verify_success`、`supplier_changed <= supplier_success`、数量非负、源比率字段与反算值差异 > 0.5 个百分点。只提示不拦截，结果作为"数据提示"随工具返回，模型据此说"数据口径不一致"。

## 5. 问答工具 `app/llm/tools.py`（改）

新增两个工具，数据不足时与其他工具一样返回结论性文字。两个工具不使用 `ToolContext` 里的 `user_id` 和 `erp_sid`。

两个工具共用的时间范围参数，与查订比工具一致：`range_type`（`full_day` 默认 / `period`）、`period_start`、`period_end`（`HH:MM`，`period` 时必填，开始早于结束）。`period` 表示每一天的同一时段，例如 10-20～10-22 的 10:00～12:00 是三天各自的两小时。返回里的"范围"字段写明"完整一天"或"10:00～12:00"，范围含当天时提示是截至查询时刻的累计。

### `quality_summary`

验价生单数据质量汇总。参数：`date_from`、`date_to`（整天最多 60 天，时段最多 7 天）、时间范围参数、`group_by`（`overall` / `channel` / `supplier` / `airline` / `channel_supplier` / `channel_airline` / `channel_supplier_airline`）、`by_day`（是否每个分组再按日分别计算；看逐日走势用 `overall` 加 `by_day`）、`agent_code`、`supplier_code`、`ticket_carrier` 过滤、`sort_by`（`sample` / `verify_fail_rate` / `supplier_fail_rate` / `timeout_rate` / `booking_rate`）、`ascending`（默认从高到低，看预定率最低的用 true；没有有效数据的分组永远排最后）、`top_n`。

返回：每组的样本量、全部比率、关注等级、合计行、数据提示；`by_day=true` 时按日分别计算，否则多日先累计再算比率。用于回答"某渠道这周的校验失败率""哪些供应商超时严重""各航司预定率""今天上午 10 点到 12 点哪个渠道失败率高"等取数和排名问题。

### `quality_diagnose`

规则第六节的问题定位流程。参数：`date_from`、`date_to`（默认同一天）、时间范围参数、`agent_code`（必填）、`top_n`。

返回结构固定，模型只需按格式复述：

1. 渠道层：总验价数、失败数、渠道校验失败率、关注等级；
2. 供应商层：每个供应商的供应校验总数、失败数、供应侧失败率、失败贡献率、等级，按排序规则排好；
3. 航司层：每个航司的渠道侧验价数、失败数、失败率、贡献率、等级；
4. 下钻：主要异常供应商（取等级最高的前 3 个）各自的航司拆分；主要异常航司各自的供应商拆分；
5. 结论字段：`主要异常组合`（供应商－航司）、`排序依据`；
6. 超时部分同时给出报表超时率和校验成功内超时率；
7. 措辞限定随结果返回：只能说"异常主要集中在"，不能说"根因"。

### `get_data_coverage`

把"数据质量表日期"改为"验价生单数据质量（接口）"：按需获取、可查任意历史日期、支持小时级时间段、当天为实时累计，并列出已缓存的整天日期范围（全局，不按人）。

### 系统提示词 `app/llm/chat.py`

补充三条规则：

- 问"报表里的 timeout_rate 怎么算"时直接给公式 `(超时5～10秒 + 超时>10秒) / 总验价数`；问超时问题时同时给报表口径和校验成功内口径，并说明后者是主要关注指标。
- 数据质量分析只能说"异常主要集中在 / 主要异常对象"，没有错误码、日志或事故结论不得说"根因"。
- 分母为 0 的指标说"无有效数据"；小样本（<20）高失败率只展示不提醒。
- 用户问某个时间段（如"上午 10 点到 12 点"）的数据质量时，用时段参数直接查，不要追问是否要整天；时段数据是实时取的，不做历史缓存。

## 6. 删除上传路径

| 位置 | 改动 |
|---|---|
| `backend/app/importers/quality.py`、`tests/test_quality_import.py` | 删除 |
| `backend/app/api/uploads.py` | `IMPORTERS` 去掉 `quality`，错误文案改为 `order / query`；批次明细去掉 `SupplyQuality` 分支 |
| `backend/app/models/analysis.py` | 删除 `SupplyQuality`，新增 `DataQualityStat`、`DataQualitySync` |
| `backend/app/models/upload.py`、`importers/common.py` | 注释里去掉数据质量 |
| `frontend/src/pages/Upload/index.tsx` | 文件类型去掉数据质量表；`needsWindow` 只看 `query`；底部说明改为"查询量表同一日期与范围的记录会被覆盖更新" |
| `frontend/src/api/uploads.ts` | `FileType` 改为 `"order" \| "query"`，标签同步 |
| `frontend/src/pages/Grants/index.tsx` | 授权说明去掉"数据质量表" |
| `docs/erp-login.md` | 数据范围一行补充：数据质量来自接口，全局共享，不按人隔离 |

## 7. 配置

`.env.example` 新增：

```env
# 验价生单数据质量接口（内网，免鉴权），必填，如 http://10.0.0.1:8080
DATA_QUALITY_BASE_URL=
# 后端启动时自动补齐从该日期到昨天的数据质量数据（只补缺的天）；为空则不回填
DATA_QUALITY_BACKFILL_FROM=2026-09-22
```

`config.py` 新增 `data_quality_base_url: str = ""`、`data_quality_backfill_from: str = ""`。地址为空时不报错启动，但拉取时返回"未配置 DATA_QUALITY_BASE_URL，无法获取数据质量数据"，定时同步与回填跳过并记日志。

## 8. 测试

- `tests/test_data_quality_client.py`：用 `httpx.MockTransport` 覆盖分页拼接、重复键取最后一条、`code != 0`、非 JSON、整天与时段两种请求体的时间格式（`23:59:59`、`HH:MM:59`）。
- `tests/test_data_quality_cache.py`（需要 `TEST_DATABASE_URL`）：历史日只拉一次、当天每次重拉且不记同步表、接口失败回滚并抛 `ImportValidationError`；时段查询不写库、不记同步表、逐日各调一次接口、超过 7 天报错。
- `tests/test_data_quality_metrics.py`：规则文档里的例子直接做断言（2/2 不提醒、20/20 需要关注、101 次失败率 10% 重点关注）、分母 0 返回 `None`、贡献率排序、校验提示。
- `tests/test_data_quality_tools.py`：两个工具已注册、参数校验（时段缺开始结束、开始晚于结束）、空数据文案。
- 现有 `test_erp_sync.py` 补一条：数据质量同步失败不影响查订比同步，未配置服务账号时数据质量同步照常执行。
- 回填：`backfill_days(from, today)` 纯函数返回待补日期列表（空配置、晚于昨天都返回空）；缓存测试里加一条：已缓存的天不再请求、某天失败后继续补后面的天。

## 9. 实施顺序

1. 模型 + 迁移 + 配置；
2. 接口客户端（整天 + 时段）+ 缓存 + 定时同步 + 启动回填 + 手动回填命令；
3. 指标函数 + 单测；
4. 工具与提示词；
5. 删除上传路径（后端、前端、文档）；
6. 联调：测试环境 `.env` 填好地址与回填起始日期，部署后看日志确认"数据质量回填完成"，再核对某一天 `summary` 与自行求和一致；同一天分别查 10:00～12:00 和整天，时段的验价总数应明显小于整天，确认小时过滤生效。

二期（视需要）：前端数据质量查看页。
