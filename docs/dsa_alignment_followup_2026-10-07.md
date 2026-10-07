# daily_stock_analysis 对齐后全面复核（2026-10-07）

> 后续修复状态：F01～F14 已完成实现及离线/浏览器回归，见[修复验收记录](dsa_fixes_2026-10-07.md)。以下保留修复前的审计结论和复现证据，不代表当前代码仍存在同样问题。

复核业务基线 `6a6bae8193184724ea6a3f9b9b0a5ece6c5315d1`，参考上游固定提交 [`ce364e457aab288863a5707e7b3df79786ad07f2`](https://github.com/ZhuLinsen/daily_stock_analysis/commit/ce364e457aab288863a5707e7b3df79786ad07f2)。第二次复核开始时本地 HEAD 为 `90b5e93`，与业务基线相比只有第一轮审计文档和证据。重新拉取上游、查询 GitHub 主分支及 Release，确认上游 HEAD、本仓库远端业务提交及最新发行版均未变化。

结论：最初 12 项和随后 18 项的功能入口、模块与常规路径已接入，但仍有 **14 类实质差距**。第一次复核的 F01～F10 均未修复；第二次增加 F11～F14，并把盘中后验冻结补入 F01，不重复计数。优先修复 F01～F05、F11～F13。另有发行产物待更新，以及 6 类可选范围差异。不能把“已有实现、现有测试通过”作为完全对齐的结论。

本轮为审计，没有修改业务实现。保留此前实施记录，同时新增可以再次运行的复现脚本和实际结果。

## 第二次复核增补

| 编号 | 新确认的行为 | 影响 |
| --- | --- | --- |
| F01 扩展 | 11:00 的盘中价得到 `evaluated`，收盘价更正后仍沿用旧结果 | 错误收益和命中可能永久进入后验与权重样本 |
| F11 · P1 | 旧信号字段另算收益，绕过统一引擎的截止日期、缺交易日和复权检查 | 同一信号同时显示 `unable/pending` 与已命中；缺日样本仍计入 100% 命中率 |
| F12 · P1 | 删除报告不清理策略样本/后验；已删除的报告 ID 还能插入样本 | 孤儿记录继续参与统计，晚到写入能重新留下无来源样本 |
| F13 · P1 | 仲裁接受方向反转及提高信心，并覆盖共识、解除信心上限 | 没有守住上游“修订只能更保守、投影不覆盖权威结果”的边界 |
| F14 · P2 | 历史运行日志丢保存步骤，事件接收器错误向主流程传播 | 页面追溯与实时结果不一致，诊断记录失败可能影响业务成功状态；排障摘要与运行流契约仍不完整 |

本次新增 9 个离线复现场景，相关现有测试 130 条全部通过。上表是缺陷复现与源码契约对照，未声称已修复，也未把测试通过当作这些问题不存在的证据。

## 全面核对范围

按[上游文档索引](https://github.com/ZhuLinsen/daily_stock_analysis/blob/ce364e457aab288863a5707e7b3df79786ad07f2/docs/INDEX.md)、相关服务、API、Web 和测试交叉核对，判断功能和运行行为，不以文件名或接口路径必须相同为标准。

| 领域 | 本地当前能力 | 本轮结论 |
| --- | --- | --- |
| 证券身份与研究范围 | 统一代码/别名、A 股、国内 ETF/指数 | 国内范围已接入；海外为可选扩展 |
| 行情与供应商 | 实时/日线回退、复权与单位、TickFlow/妙想等配置 | 已接入；时效消费仍有 F01，后验旧路径另有 F11 |
| 数据源稳定性 | 总预算、超时、冷却、在途隔离、错误/空结果区分 | 本轮未发现新增阻塞差距 |
| 数据中心 | 能力、优先级、健康状态、本地最新批次 | 实际供应商与精确能力追溯仍有 F08 |
| 模型与搜索 | 主备、多 Key、原生工具、参数兼容、Requesty、Anspire/MiniMax 等 | 已接入；真实付费账户能力需另验 |
| 新闻与情报 | RSS/Atom/NewsNow、资讯管理、搜索失败状态 | 本轮未发现新增阻塞差距 |
| 个股/ETF/指数诊断 | 多分析员、决策风格、阶段护栏、校准、报告 | 日线质量与建议护栏未贯通，见 F01 |
| ContextPack / ResearchArtifact | 分块证据、双时间、历史报告身份、可见摘要 | 结构已有；质量仍有 F01 |
| 个股聚合 API | 行情、研究、信号、持仓、监控、历史、情报 | 已接入，行情质量单独披露 |
| 策略选股 | 硬规则、多因子、财务补数、风险、集中度、模型复核 | 主要流程已有；来源运行历史见 F10 |
| 选股解释与快照 | why_selected/why_now、逐条件通过/失败/未知 | 已接入；无新增缺失模块 |
| ETF 双动量 | 固定池/槽位、绝对动量、防守、缓冲、费用、执行滞后 | 服务与报告仍有 F01、F05、F06 |
| 实盘/模拟盘风险 | 出入金、现金锚点、公司行为、单位净值、价格/行业质量 | 回撤重放 F02；AI 风险联动 F07 |
| 决策信号 | 生命周期、反馈、作废、后验、诊断复用 | 已有信号系统；组合/告警衔接见 F07，后验双轨见 F11 |
| 策略会诊与仲裁 | 有效观点过滤、少数意见、受限轮数、非法响应回退 | 单调保守修订与权威共识保护仍有 F13 |
| 后验与学习样本 | 1/3/5/10 日窗口、版本、最小样本门槛、去重与有限权重 | 完整收盘时点 F01、旧字段 F11、报告删除与晚到写入 F12 |
| 运行诊断与历史 | trace_id、源尝试、耗时、阶段列表、持久化任务 | 历史记录一致性、异常隔离、排障摘要/运行流见 F14 |
| 告警中心 | 股票/自选/账户作用域、规则来源、冷却和渠道结果 | 主要流程已有；决策信号关联见 F07 |
| 自选与下一步 | 无报告/历史/更新/运行/失败/未知，报价质量另列 | 已接入；不是缺少自选状态功能 |
| 问股与流式阶段 | 确定性任务、歧义确认、上下文继承、阶段回放 | 范围 F03、任务顺序 F09 |
| 设置草稿 | 分类切换保留、离开确认、失败保留、导入后重建 | 常规路径已有；并发保护 F04 |
| 截图与分享 | A 股截图识别、自选确认、历史报告/复盘/自选分享图 | 已有；不能列为“缺截图识别/分享图” |
| 调度与 CLI | 隔离任务、--once、SQLite 调度占位、预算和取消 | 本轮相关回归通过，无新增确认差距 |
| Web / Electron / Docker / CI | 同一 Web、无 Qt6、三平台打包、多架构镜像、英文 Actions、中文说明 | 源码已有；正式发行版仍旧，见发布状态 |

“本轮未发现”表示本次对照没有确认新的差距，不是对所有输入、真实供应商或操作系统的无条件保证。

## F01 · P1：日线时效契约未贯通诊断、证据包、ETF 与后验

统一的 `daily_quality()` 已能识别盘中缓存跨收盘，但诊断护栏只传入行情日期；`market_phase._is_stale()` 仅比较日期是否早于应有日期。ContextPack 的行情/日线/技术块也仅按日期判断陈旧。ETF 服务查询没有读取 `updated_at` 来核对盘后完整性。

复现：固定 9 月 18 日 16:00 的盘后阶段，行情日期也是 18 日、取得时刻为 14:00。`daily_quality` 返回 `stale/cached_before_close`，实际诊断护栏却保留 `buy/高`，没有时效限制。ETF 把同类 14:00 日线纳入当日排名及下一交易日目标，没有缓存跨收盘告警。另将取得时刻设置到 10 月 25 日，`daily_quality` 仍返回 `available`。

应把观测日期、取得时刻和完整日线质量传入建议护栏及证据状态；实时/历史模式分别处理，校验明显的未来取得时间。不能只在选股/profile 或界面层计算质量。

第二次复核增加后验消费面：`OutcomeEngine.price_path()` 只按 `trade_date <= now.date()` 截断，没有把盘中日线排除出可终结的日收益。9 月 14 日基价 10，15 日 11:00 缓存价 12，1 日结果立刻成为 `evaluated/+20%/hit=true`；15 日 16:00 将正式收盘价改为 9 后重评，仍为 `+20%/hit=true`，正确收盘收益应为 `-10%`。原因是已评估结果随后不可变。这个冻结缺陷需要先确保输入完整才能进入终态；已污染结果应通过版本化重评修正，不能直接修改所有历史终态。共享引擎供诊断、信号和策略观点使用；信号 API 可以手动触发评估。

- 本地：[诊断护栏](../src/services/stock_diagnosis.py#L781)、[日期护栏](../src/services/market_phase.py#L98)、[证据包](../src/services/research_artifact.py#L39)、[ETF 取数](../src/services/etf_rotation.py#L86)、[日线时效](../src/services/data_freshness.py#L24)、[后验窗口与终态](../src/services/outcome_engine.py#L30)、[手动评估入口](../api/v1/signals.py#L47)。
- 对照：[上下文契约](https://github.com/ZhuLinsen/daily_stock_analysis/blob/ce364e457aab288863a5707e7b3df79786ad07f2/docs/analysis-context-pack.md)。诊断跨缓存边界和未来时间为本地正确性复现，不声称上游存在一模一样的函数或测试。

## F02 · P1：实盘净值遗漏无报价日的公司行为和整日缺价

`PortfolioRiskService._drawdown()` 从已有报价、成交、出入金、现金锚点拼接日期，没有加入公司行为日期，也没有补全应有的交易日。`ledger_nav()` 只执行传入日期上的事件，导致没有报价当天的分红、税款或送股可能直接消失。所有持仓同时没有报价的一整天，也不会进入缺价检查。

复现：14 日入金 200；15 日以 10 买入 10 股；16 日分红 10、当天无报价；18 日收盘价仍为 10。实际现金重放为 110，资产应为 210；回撤服务期末样本却是 200，遗漏 16/17 日，并给出 `quality=available`、零回撤。

应按可靠日历与所有账本事件重放，缺失行情不能通过删除日期来隐去。公司行为必须累计生效，缺价估值和可验证收益区间应分别披露。

- 本地：[回撤日期生成](../src/services/portfolio_risk.py#L218)、[事件重放](../src/services/portfolio_nav.py#L10)。
- 对照：[组合风险质量](https://github.com/ZhuLinsen/daily_stock_analysis/blob/ce364e457aab288863a5707e7b3df79786ad07f2/docs/portfolio-risk-exposure-dashboard.md)。这是本地实盘账本扩展的正确性缺陷，不是声称上游提供了同一套出入金净值引擎。

## F03 · P1：问股歧义确认可以绕过限定证券

普通问题在 `resolve()` 末尾校验 `stock_context`，但确认候选的分支提前 `_finish()`，没有重新校验范围。执行器又使用任务候选代码生成子任务范围。

复现：限定 `600519`，询问“分析平安”，随后确认 `000001`，得到可直接执行的平安银行任务，`requires_confirmation=false`。直接询问“分析平安银行”的现有测试能拦截，未覆盖两轮确认。

应在所有出口及执行前统一检查规范证券身份，确认状态也须受当前会话限定约束。

- 本地：[确认分支与范围检查](../src/services/web_intent.py#L17)、[子任务范围](../src/services/chat_sessions.py#L68)。
- 对照：[上游意图解析](https://github.com/ZhuLinsen/daily_stock_analysis/blob/ce364e457aab288863a5707e7b3df79786ad07f2/src/agent/web_intent_resolver.py)、[证券范围契约](https://github.com/ZhuLinsen/daily_stock_analysis/blob/ce364e457aab288863a5707e7b3df79786ad07f2/src/agent/stock_scope.py)及本地已有的“限定股票”契约。

## F04 · P1：设置保存与整体导入存在覆盖竞态

已确认普通页签切换、保存失败和接受导入后的草稿清理实现存在；缺口是请求在途期间的保护。

真实 Chromium 复现两条路径：

1. 提交财务候选数 `11`，等待保存时仍能编辑为 `19`；旧响应返回后，表单重新变成 `11`，新输入丢失。未保存提示仍显示，不能恢复已经被覆盖的 `19`。
2. `13` 的保存请求未完成时，整体导入 `29` 成功；随后旧保存落库，把配置重新写成 `13`。导入完成后的重建不能阻止已经发送的旧请求覆盖服务器。

应统一协调保存、导入、重置、刷新与离开；对返回结果进行编辑版本校验，并保护实际服务端写入顺序。只增加 dirty 标记不能解决竞态。

- 本地：[选股设置保存](../apps/web/src/pages/settings/ScreeningSettingsPanel.tsx#L11)、[请求事件](../apps/web/src/api/client.ts#L24)、[草稿边界](../apps/web/src/components/SettingsDraftBoundary.tsx#L11)。
- 对照：[上游在途操作约束](https://github.com/ZhuLinsen/daily_stock_analysis/blob/ce364e457aab288863a5707e7b3df79786ad07f2/docs/settings-draft-protection.md#L24)。
- 截图：[等待响应时为 19](audits/dsa-followup-2026-10-07/settings-before-response.png)、[旧响应覆盖回 11](audits/dsa-followup-2026-10-07/settings-after-response.png)。

## F05 · P1：ETF 的交易日历与截止日仍采用降级口径

本地日历覆盖不足时仅添加 `calendar_uses_weekday_fallback`，继续产生回测、指标和目标；价格表索引只延伸到 `observed_end`，没有延伸到请求截止交易日。上游在日历不可用时停止报告，并保留所有标的同时缺报价的交易日，包括末尾缺口。

复现：指定结束日 9 月 18 日、全部价格停在 17 日，返回 `parameters.end=18`，但 `as_of` 和曲线末日均为 17 日。另模拟日历无覆盖，仍返回年化指标和 21 日执行目标，只有 partial 告警。

应取得真实区间日历并补全到截止日；没有日历则阻止生成依赖交易日的报告。末尾缺价必须保留为缺价，而不是缩短报告日期。

- 本地：[日历与索引](../src/services/etf_rotation.py#L75)。
- 对照：[上游加载与日历对齐](https://github.com/ZhuLinsen/daily_stock_analysis/blob/ce364e457aab288863a5707e7b3df79786ad07f2/src/services/etf_rotation_service.py#L153)、[规则说明](https://github.com/ZhuLinsen/daily_stock_analysis/blob/ce364e457aab288863a5707e7b3df79786ad07f2/docs/etf-rotation.md#L22)。

## F06 · P2：ETF 报告缺少短样本和防守资产边界

纯规则引擎的固定槽位、缓冲、费用、缺价不成交等已经接入，但服务没有完整保留上游报告层行为：

- 短于一年仍返回年化、夏普、Calmar、年度收益、调仓及参数扫描；只是增加历史不足提示。复现不足两个月的合成价格，仍出现 `cagr≈1.074` 和 `sharpe≈55.371`。上游短样本省略这些报告部分。
- 防守 ETF 最新日没有报价时，目标仍显示该 ETF，而非现金，也没有防守不可用提示。复现风险池负动量、防守报价停在 17 日，18 日目标仍为 `511880: 100%`。

应分开研究期望目标、实际持仓和能否确认成交；短历史保留信号与告警，省略不适用指标。历史刷新目前只有东财/腾讯专用链，默认 8 年真实前复权历史的取得和异常跳变/混源诊断也未完成在线验收，不能把引擎单测视为历史质量验收。

- 本地：[指标与目标](../src/services/etf_rotation.py#L102)、[刷新链](../src/services/etf_rotation.py#L33)。
- 对照：[上游防守可用性与短样本](https://github.com/ZhuLinsen/daily_stock_analysis/blob/ce364e457aab288863a5707e7b3df79786ad07f2/src/services/etf_rotation_service.py#L358)。

## F07 · P2：决策信号没有贯通组合风险与告警

本地已有完整的 `DecisionSignal` 生命周期、反馈、评估及诊断关联；剩余差距不是“缺信号系统”。组合风险没有读取持仓的有效防御型信号，告警也没有关联当前信号摘要。

复现：向临时库加入持仓 `600519` 的有效 `active/sell` 信号，风险报告没有对应风险字段、旗标或提示。源码中告警存储/推送没有 `DecisionSignal` 关联；上游会关联最新 active 信号，并在组合风险汇总持仓的防御信号。

应补只读的有效信号摘要，保留来源报告及有效期；查询失败必须披露未知，避免推送原始敏感上下文。相关提示不能自动升级为下单。

- 本地：[风险报告](../src/services/portfolio_risk.py#L102)、[告警存储](../src/services/alert_service.py#L638)、[信号模型](../src/database/models.py#L435)。
- 对照：[上游联动契约](https://github.com/ZhuLinsen/daily_stock_analysis/blob/ce364e457aab288863a5707e7b3df79786ad07f2/docs/decision-signals.md#L208)。

## F08 · P2：数据中心缺实际批次来源与精确能力追溯

供应商和优先级页面已有，最新批次也已展示日期、取得时间、质量与行数。但批次 snapshot 未返回数据库行的 `source`；矩阵的 `fetched_at` 使用供应商健康检查 `last_success`，不是该批次取得时间。资产类型和场景主要按数据集名称推导，尚未形成明确的逐供应商能力定义。

复现：最新行 `source=audit-local-source`，返回的日线 snapshot 没有来源字段。用户仍无法直接从该批次核对实际命中的供应商。当前页面已注明“最新行不代表全市场覆盖”，这条限制不重复列为缺失。

应展示实际批次/标的来源、混源与覆盖，分开供应商健康时间和行情取得时间；能力定义明确到供应商和数据集，声明能力不等于实际本批次成功。

- 本地：[数据中心组装](../src/services/data_capabilities.py#L165)。
- 对照：[精确能力与实际来源](https://github.com/ZhuLinsen/daily_stock_analysis/blob/ce364e457aab288863a5707e7b3df79786ad07f2/docs/data-center-diagnostics.md)。

## F09 · P2：同一句话的多任务顺序不按用户顺序

“然后/接着”分句的顺序已实现；但没有这些分隔词时，识别到股票就先插入股票任务，其他任务按固定类别顺序追加。

复现：“先看看持仓再分析贵州茅台”实际计划为 `stock_analysis → portfolio_risk`，与请求相反。

应按原句位置和“先/再/最后”等顺序词排列任务，保留歧义确认后的完整顺序，并测试非股票任务在前的表达。

- 本地：[分句和任务生成](../src/services/web_intent.py#L37)。
- 对照：[上游意图解析与任务生成](https://github.com/ZhuLinsen/daily_stock_analysis/blob/ce364e457aab288863a5707e7b3df79786ad07f2/src/agent/web_intent_resolver.py)、[解析回归](https://github.com/ZhuLinsen/daily_stock_analysis/blob/ce364e457aab288863a5707e7b3df79786ad07f2/tests/test_web_intent_resolver.py)。

## F10 · P2：选股缺少按运行聚合的数据源历史

本地已有 `/screening/runs`、逐候选来源/质量和数据源健康统计；不能说没有任何运行历史或来源记录。尚缺上游 `/screening/source-history` 对选股运行的源命中、失败、回退/降级趋势聚合，以及对应查询展示。

`ScreeningRun` 保存选股结果，但没有完整的本次采集源尝试链；诊断 `RunLog` 和当前健康计数不能替代它。只把最终成功来源写到 pick 中，也无法解释首选源为何持续回退。

应在采集/选股运行身份下保留低敏来源尝试及质量摘要，再聚合历史。可以沿用现有 SQLite 和日志系统，不需要复制上游数据库结构或新建一套选股引擎。

- 本地：[选股历史](../src/strategy/screener.py#L703)、[现有路由](../api/v1/screening.py#L106)。
- 对照：[上游历史入口](https://github.com/ZhuLinsen/daily_stock_analysis/blob/ce364e457aab288863a5707e7b3df79786ad07f2/api/v1/endpoints/screening.py#L305)、[聚合实现](https://github.com/ZhuLinsen/daily_stock_analysis/blob/ce364e457aab288863a5707e7b3df79786ad07f2/src/services/screening_service.py#L1682)。本项由 API/服务/数据模型对照确认，不是假装已做网络失败趋势的在线复现。

## F11 · P1：统一后验之外仍有一套不受同等约束的旧信号计算

`DecisionSignalService.evaluate()` 先调用共享 `OutcomeEngine`，再调用旧 `_evaluate_one()`。旧 `_load_bars()` 没有限制行情日期不晚于评估截止日，没有拒绝观察窗口中缺失的交易日，也没有携带复权口径；按第 N 条可用记录直接计算 `ret_1d/3d/5d`、最大不利/有利波动和目标/止损状态。`is_hit()`、`stats()` 和 `review()` 继续消费旧字段，而不是共享引擎的有效后验。

三个隔离场景均已复现：

- 截至 9 月 14 日评估，数据库包含 15 日价格 12、目标价 11：旧字段已经 `hit_target/+20%`，共享引擎是 `pending`。这是带历史截止时点评估的未来数据泄漏。
- 14 日基价 10，缺 15 日日线，16 日价格 12：旧字段将两日间隔计为 `ret_1d=20%`，且统计命中率为 100%；共享引擎返回 `unable/missing_trading_day`。
- 基准不复权 10、下一日前复权 5，缺少可证明可比的涨幅：旧字段给出 `-50%`，共享引擎返回 `unable/incomparable_price_basis`。

应使用一个经过截止日期、完整性和价格口径检查的窗口派生旧兼容字段与触价事件；收益是否可用、信号是否命中、统计是否纳入必须一致。触价所用高低价也需要相同质量约束，不能只修页面上的收益展示。

- 本地：[两套路径及旧查询](../src/services/decision_signals.py#L155)、[命中和统计](../src/services/decision_signals.py#L255)、[统一窗口](../src/services/outcome_engine.py#L30)。
- 对照：[上游后验契约](https://github.com/ZhuLinsen/daily_stock_analysis/blob/ce364e457aab288863a5707e7b3df79786ad07f2/docs/multi-strategy-contract.md#L13)。本地双轨矛盾由实测确认；不要求复制上游字段或目录。

## F12 · P1：删除历史与策略样本的生命周期未对齐

单条删除和批量删除都只删除 `StockDiagnosis`，没有清理关联 `SkillOpinion` 及其 `ResearchOutcome`。样本插入虽然已有 `BEGIN IMMEDIATE` 和幂等指纹，却没有在该事务中验证父报告仍存在。`_stats()` 也不检查样本的报告是否仍存在。

复现：一个报告、一条有效策略样本、四个后验窗口；删除后报告数为 0，样本仍为 1，后验仍为 4，统计依然计入这一条命中。用已删除报告 ID 录入另一策略，返回插入成功 1 条。单删、批删结果一致。这个顺序也对应“删除先完成，分析任务随后晚到写样本”的交错；无需真实并发即可证明缺少父对象校验。

应将关联后验→样本→报告的删除放在同一写事务，插入时在同等事务保护下检查父报告。历史清理后是否保留独立决策信号需要明确产品规则，不能让引用自动悬空；本项直接复现的是策略样本与后验，不推定所有独立信号都应删除。

- 本地：[单删和批删](../src/services/data_query_service.py#L185)、[样本插入](../src/services/skill_consult.py#L211)、[表现统计](../src/services/skill_consult.py#L268)。
- 对照：[上游样本原子写入](https://github.com/ZhuLinsen/daily_stock_analysis/blob/ce364e457aab288863a5707e7b3df79786ad07f2/src/repositories/skill_opinion_sample_repo.py#L20)、[关联删除事务](https://github.com/ZhuLinsen/daily_stock_analysis/blob/ce364e457aab288863a5707e7b3df79786ad07f2/src/storage.py#L3134)。

## F13 · P1：策略仲裁没有落实单调保守与权威共识保护

目前校验仲裁回复的条数、skill 集合、评分合法性及理由，但不检查相对原观点是否反转方向、提高信心或变得更激进。通过格式校验后直接重算并覆盖 `consensus`，冲突消失就解除 `confidence_cap`。示例配置默认启用仲裁。

复现：原观点为看多 80/信心中与看空 20/信心中，确定性共识为中性 50、信心上限低；假模型把两者均改为看多 95/信心高，服务接受为 `completed`，共识变为看多 95，上限变为 `null`。本地保留了 `original_opinions/minority`，但仅保存原文并不能阻止风险约束被放宽。诊断主流程根据这个上限决定是否强制降低最终信心。

上游要求修订只能维持或保守弱化，越界回退上一轮已验证结果；修订投影用于预览，不覆盖权威信号、评分或信心。应在本地枚举和评分体系下落实同等规则，并保持原始共识、修订预览及最终决策的来源清楚。已有“非法 JSON 回退”“双方转中性”的测试不覆盖方向反转和提高信心。

- 本地：[仲裁校验及覆盖](../src/services/strategy_synthesis.py#L35)、[诊断信心上限消费](../src/services/stock_diagnosis.py#L279)、[默认配置](../config/settings.yaml.example#L455)。
- 对照：[上游修订约束](https://github.com/ZhuLinsen/daily_stock_analysis/blob/ce364e457aab288863a5707e7b3df79786ad07f2/docs/multi-strategy-contract.md#L251)、[预览边界](https://github.com/ZhuLinsen/daily_stock_analysis/blob/ce364e457aab288863a5707e7b3df79786ad07f2/docs/multi-strategy-contract.md#L278)。

## F14 · P2：运行诊断的历史一致性、异常隔离与排障契约仍有缺口

本地已有 trace_id、数据源尝试、阶段列表、耗时与持久化任务，并非“没有运行日志”。这次确认以下边界：

1. 诊断先保存 `StockDiagnosis.run_log`，后追加“报告保存”步骤时只刷新 `result_json`。历史查询优先返回旧列，工作台又用顶层 `run_log` 覆盖 `result.run_log`。保留真实 `diagnose/_save/get_diagnosis`，只替换取数、模型、护栏和信号副作用的探针确认：实时为“决策→报告保存”，历史顶层只有“决策”，历史 JSON 内又包含保存步骤。
2. `RunLog._append()` 调用事件接收器没有异常隔离。给一个成功步骤注入抛错的接收器，错误向调用方传播。上游明确要求记录先落内存，接收器错误只告警，不改变分析/保存/通知成功状态。本次没有模拟真实客户端断线，因此不把所有断线都说成会导致此问题。
3. 相比上游运行诊断摘要与 `RunFlowSnapshot`，本地仍缺可复制的脱敏排障摘要、稳定的阶段/节点/事件关系与历史回填契约。普通任务进度只保存最新一条事件；现有完成后步骤列表不能等同完整运行流。可以扩充现有日志，不必照搬图形界面。

应先统一实时、持久化及页面读取的日志真源，隔离接收器异常，再补低敏摘要与可恢复运行流。F10 针对选股数据源按运行聚合的历史，本项针对诊断执行过程，二者没有重复计数。

- 本地：[保存先后顺序](../src/services/stock_diagnosis.py#L288)、[历史查询优先级](../src/services/data_query_service.py#L111)、[工作台消费](../apps/web/src/pages/WorkspacePage.tsx#L27)、[事件接收器](../src/services/run_log.py#L79)、[最新事件覆盖](../api/tasks.py#L116)、[日志展示](../apps/web/src/components/RunLogView.tsx#L11)。
- 对照：[摘要与复制文本](https://github.com/ZhuLinsen/daily_stock_analysis/blob/ce364e457aab288863a5707e7b3df79786ad07f2/docs/run-diagnostics-p2.md)、[运行流与异常隔离](https://github.com/ZhuLinsen/daily_stock_analysis/blob/ce364e457aab288863a5707e7b3df79786ad07f2/docs/run-diagnostics-p3.md)、[接收器保护实现](https://github.com/ZhuLinsen/daily_stock_analysis/blob/ce364e457aab288863a5707e7b3df79786ad07f2/src/services/run_diagnostics.py#L464)。

同时复查了任务取消与重启：本地已有问股取消和任务持久化/中断恢复；上游普通分析任务中的取消状态枚举，不等于已经提供普通任务的用户取消 API。因此本次没有把“缺通用任务取消”单独列成对齐缺陷。

## 发布与真实环境待验收

[GitHub 最新 Release 仍为 v1.1.1](https://github.com/hongheshan-svg/quant_tools/releases/tag/v1.1.1)，发布时间 2026-10-02 14:14:50 UTC，标签对应 `821f00fe`；本轮审计的主分支比它多 23 个提交。因此主分支的功能不能视为已经进入现有 Windows/macOS/Linux 安装包。

发布工作流已经支持 Windows、macOS arm64/x64、Linux，以及 Docker amd64/arm64；`main` push 和手动演练不会发布新的正式镜像/安装包。后续修复完成后应建立新版本标签，并验收正式附件和多架构镜像，不覆盖旧标签。本次审计未发版。

妙想/Requesty 等实际账户权限、真实 8 年 ETF 历史、各平台最新版安装启动仍需要使用对应凭据和发行产物验收。这里只标记未验证，没有把凭据缺失推定为实现错误。

## 六类可选范围差异

这些属于产品范围或扩展，不计入上述 14 类必要衔接差距：

| 类别 | 仍有差异 | 当前判断 |
| --- | --- | --- |
| 海外证券与组合 | 港美日韩台标的、日历、币种/汇率质量、Futu/Longbridge 持仓导入 | 当前国际背景采集不等于海外个股研究；国内范围无需硬补全部市场 |
| 宏观工具 | 上游 FXMacroData 操作目录 | 本地已有国际背景，不缺少所有宏观能力；特定供应商可选 |
| 模型运行后端 | Codex/Hermes 等 CLI 后端及会话续接 | 当前 SDK/LiteLLM 路由已有，不等于必须新增 CLI |
| 外部代理兼容 | OpenClaw Skill、Grok Bot 等特定 API/Skill 契约 | 通用 REST/Bot 不等于逐外部协议兼容；按实际使用需求补 |
| 通知与语言 | Feishu/Lark 主动文件/云文档、Slack Bot、AstrBot、韩语、海外社交 | 本地已有 14 个通知渠道及飞书聊天机器人；不能重复计为缺一套推送 |
| 近分候选变体 | 匿名 `variant_seed`、有界近分池轮换 | 当前严格确定性排名是合理选择；采纳前应明确是否希望不同运行展示不同近分候选 |

相关上游参考：[数据稳定性](https://github.com/ZhuLinsen/daily_stock_analysis/blob/ce364e457aab288863a5707e7b3df79786ad07f2/docs/data-source-stability.md)、[宏观工具](https://github.com/ZhuLinsen/daily_stock_analysis/blob/ce364e457aab288863a5707e7b3df79786ad07f2/src/agent/tools/fxmacrodata_tools.py)、[外部 Skill](https://github.com/ZhuLinsen/daily_stock_analysis/blob/ce364e457aab288863a5707e7b3df79786ad07f2/docs/openclaw-skill-integration.md)、[通知](https://github.com/ZhuLinsen/daily_stock_analysis/blob/ce364e457aab288863a5707e7b3df79786ad07f2/docs/notifications.md)、[近分轮换](https://github.com/ZhuLinsen/daily_stock_analysis/blob/ce364e457aab288863a5707e7b3df79786ad07f2/docs/screening-engine.md#L118)。

## 验证与证据

- 基线提交的[CI](https://github.com/hongheshan-svg/quant_tools/actions/runs/37561601741)为 success，`headSha=6a6bae81`；后端三个分片、Web、桌面测试、浏览器及 Docker 均通过。
- 第一次复核运行相关 7 个后端测试文件，**41 passed**；4 个第三方弃用提示。第二次运行下列 6 个后端文件，分两批得到 **78 passed + 52 passed = 130 passed**，每批各有 2 个相同的第三方弃用提示。没有重复宣称本次重跑全部 2001 条或所有平台安装包。
- 第一次离线探针包含 11 个受控场景，真实浏览器复现 2 个设置竞态，页面异常为 0。第二次增加 9 个离线场景，共保留 22 个场景证据；第二次没有重跑浏览器。结果记录的是当前缺陷行为，不是修复后通过的新回归测试。
- 临时数据库、合成价格、受控阶段/日历、隔离配置；模型、调度、真实交易和推送未运行。浏览器屏蔽非本地页面请求。
- [离线探针](audits/dsa-followup-2026-10-07/offline_probe.py)与[结果 JSON](audits/dsa-followup-2026-10-07/offline_results.json)；[浏览器探针](audits/dsa-followup-2026-10-07/browser_probe.py)与[结果 JSON](audits/dsa-followup-2026-10-07/browser_results.json)。
- 第二次复核：[离线探针](audits/dsa-deep-followup-2026-10-07/offline_probe.py)、[9 个场景结果](audits/dsa-deep-followup-2026-10-07/offline_results.json)。使用临时 SQLite，屏蔽 socket 连接，只有合成行情和假模型；结果输出 `/tmp/quant-dsa-deep-followup-results.json`。

从仓库根目录复现：

```bash
.venv/bin/python docs/audits/dsa-followup-2026-10-07/offline_probe.py
.venv/bin/python docs/audits/dsa-followup-2026-10-07/browser_probe.py
.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_web_intent_stages.py tests/test_alignment_data_contracts.py tests/test_portfolio_nav_quality.py tests/test_etf_rotation.py tests/test_data_center.py tests/test_alert_scopes.py tests/test_scheduled_claims.py

# 第二次复核增补
.venv/bin/python docs/audits/dsa-deep-followup-2026-10-07/offline_probe.py
.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_decision_signals.py tests/test_skill_consult.py tests/test_run_log.py tests/test_diagnosis_history.py
.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_full_alignment.py tests/test_research_alignment.py
```

探针需要现有依赖中的 pytest、Playwright/Chromium；浏览器使用已经构建的 `apps/web/dist`，没有产物时先运行 `cd apps/web && npm ci && npm run build`。浏览器临时监听 `127.0.0.1:8796`，结束会终止服务；两份探针的新增输出写入 `/tmp/quant-dsa-followup-*`，不会覆盖生产配置或数据。

建议先修复 F01～F05、F11～F13，并把这些复现转为失败前/修复后回归；随后完成 F06～F10、F14，再发布包含修复的新版本。修复时以契约和场景验收，不以再补几个接口或测试总数作为“全面对齐”的替代指标。
