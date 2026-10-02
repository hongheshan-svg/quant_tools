# 研究与任务接口契约

以下路径均位于 `/api/v1`，继续使用现有登录 cookie。身份解析与所有读取查询共用；不要求客户端更换为上游路径。

## 标的身份

`600519`、`SH600519`、`600519.SH` 为同一 A 股。`600519.SZ`、冲突前后缀和非法长度拒绝。`000001` 为个股，`sh000001`/`000001.SH` 为上证指数；指数别名查询不会展开裸数字。ETF 保留六位 canonical code，日线仍在 `fund_daily`。

## 证据与 profile

`GET /stocks/{code}/profile?history_days=90` 返回 quote/research/signals/portfolio/monitors/history/intelligence 七块，每块有 `status`、`limitations` 和可用时的 `data`。profile 的 fresh 表示本地可用块，不承诺实时行情；行情附 trade_date。history_days 范围 1..3650，近期列表最多 20 条，total 为匹配记录总数。块失败互不拖累，不联网补造数据。

诊断中的 `context_pack.pack_version=1.0`；块和字段分别携带 `source/as_of/status/value`。状态支持 available、missing、not_supported、fallback、stale、estimated、partial、fetch_failed。指数/ETF 无个股财务、筹码、资金流、公告时标 not_supported；估算筹码明确标 estimated。行情比有效行情日落后时标 stale，季度财务不因早于行情日期就机械判过期。

`research.data.structured_report.schema_version=research-artifact-v1` 包含 subject、thesis、strategy_synthesis、evidence、invalidation_conditions、next_actions、data_quality。读取旧诊断时可以生成没有证据的兼容产物，limitations 为 legacy_report_without_context_pack。空数组/空块不加入证据；数值与来源时间按原始采集精度保留。

## 后验

`GET /stocks/diagnoses/{id}/outcomes` 与 `GET /signals/{id}/outcomes` 读取已经计算的结果，不主动触发模型或采集。后验写入由现有评估流程完成；尚未运行评估时可返回空数组。

`owner_type + owner_id + horizon + engine_version` 为唯一键。观察窗口 1/3/5/10 个交易日，版本 `daily-return-v1:neutral={百分比}`，完成结果 status=evaluated；未成熟为 pending；缺基准价或缺中间交易日为 unable。原因值包括 missing_base_price、insufficient_daily_bars、missing_trading_day。计算只用评估时点及以前的本地行情，别名日线按交易日去重。

中性命中为 `abs(return_pct) <= neutral_band_pct`；看多/看空分别要求超过正/负中性带。已完成版本不可覆盖，改中性带会产生不同版本。价格计划触发、信号关闭与反馈继续使用原有生命周期，不等同于新日收益命中。

## 问股与轨迹

`POST /chat/sessions/{id}/ask` 和 `/ask/stream` 在旧 question/perspective 字段上新增：

```json
{"question":"复核趋势与风险","stock_context":{"code":"600519.SH"},"skills":["volume_breakout"]}
```

请求保存为 canonical code；补充 skills 最多四个，未知策略返回 422。限定标的时个股工具先核验 code/query/name，越界查询不执行；market/watchlist/resolve_stock 仍提供辅助背景。web_search 对显式六位代码做范围检查，不能声称对任意自然语言主体完成语义强约束。同轮相同工具及规范化参数的成功结果复用，失败不缓存。

ChatTurn 附 `run_log.trace_id/total_ms/steps`，流的 done 事件保留概览但不重复发送完整工具结果；完整工具结果持久化在会话。取消、客户端关闭与超时保留已产生的文字。context 字符预算默认 12000，本轮问题和范围优先保留。

## 通用任务

原提交接口返回 `{id,status,revision,trace_id,...}`；`GET /tasks/{id}` 为快照；`GET /tasks/{id}/events?after_revision=0` 返回默认 message 类型的 SSE，revision 作为 id，data 为任务快照。活跃任务有 heartbeat，终态 done/error 后流结束。SSE 失败时 Web 回退轮询。快照可能合并快速连续更新，不保证保存每一条阶段事件。

TaskRun 持久化公开脱敏快照；内存恢复最近 200 条，更老 task ID 从数据库读取。服务重启后 pending/running 变为 error，中断原因明确，revision 递增；不自动重跑。单业务失败检测必须由提交接口显式指定，批量部分成功保留原行为。

## 来源、设置和批量操作

| 接口 | 契约 |
| --- | --- |
| `GET/POST /intelligence/sources`、`PUT /intelligence/sources/{id}` | 来源 name/url/enabled/symbol/market=CN/sector；新 URL 必须是无凭据 HTTP(S)。managed_by_config 的源回到原 YAML 设置编辑。 |
| `POST /intelligence/sources/{id}/fetch` | 提交单源拉取任务；重复 source ID 的活跃任务去重。失败保留旧条目与 last_error。 |
| `GET /intelligence/items` | symbol/market/sector/days/limit 过滤；symbol 指定来源精确归属，未指定 symbol 的全市场条目可以按标题相关性匹配；兼容旧 FinanceNews。 |
| `GET /screening/runs` | 最近成功/不完整运行、样本覆盖及 notes；不完整批次不覆盖上次成功持久化结果。 |
| `GET /settings/schema` | 读取 example 默认值构建类型、枚举与范围，不读取当前密钥。 |
| `GET/PUT /settings/scheduler` | 现有九项时间/间隔的完整配置；时间 HH:MM，间隔 1..1440 分钟；保存后刷新本进程 scheduler。 |
| `POST /stocks/diagnoses/delete` | `{ids:[1,2]}` 或 `{code:"600519"}` 必须且只能提供一个过滤器；ids 最多 200，禁止无过滤器清库。Web 提供明确确认。 |
| `POST /watchlist/report/selected` | `{codes:["600519"]}` 只能选择当前自选名单，规范化和去重；沿用日报结果存储，选中子集可成为当天最新仪表盘。 |

数据库启动新增 TaskRun、ResearchOutcome、ResearchCache、IntelligenceSource/Item、AlertRule/Cooldown、ScreeningRun 等表，并只给现有表追加兼容列。诊断删除沿用历史删除语义，不声称会自动级联清理所有已有决策信号或后验审计记录。
