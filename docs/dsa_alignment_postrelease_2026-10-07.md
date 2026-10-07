# v1.2.0 发布后的 daily_stock_analysis 对齐复核

> 后续状态：本报告列出的 R01–R08 与用户选定的 E1–E6 已完成本轮实现，见[对齐验收与功能边界](dsa_full_alignment_2026-10-07.md)。下文保留发现时的证据。

审计日期：2026-10-07。结论：确认 **8 类仍需处理的问题，涉及 9 个复现场景**；另有 **6 类可选扩展**。已有功能入口和此前修复不再整项列为缺失，但几个消费端和异常边界仍没有遵守同一质量契约。

本轮只增加审计报告、探针和证据，没有修改业务实现。P1 表示可能污染研究或学习结果、应优先处理；P2 表示会丢失用户输入、漏执行任务、误导页面状态或存在条件性运行错误。

## 参考基线与方法

- 本地：已发布的 `v1.2.0`，业务提交 `976920ab254e08a6ef6e4096eb2a89fbabf3906c`。审计开始时工作区干净。
- 上游：[ZhuLinsen/daily_stock_analysis](https://github.com/ZhuLinsen/daily_stock_analysis)，固定提交 [`ce364e457aab288863a5707e7b3df79786ad07f2`](https://github.com/ZhuLinsen/daily_stock_analysis/commit/ce364e457aab288863a5707e7b3df79786ad07f2)。本轮查询 GitHub 主分支仍为该提交，时间为 2026-10-05 07:52:26 UTC，与上次审计相同。
- 对照上游文档、服务实现、API、页面和测试；以行为契约为准，不要求目录、接口名、算法参数完全相同。
- 运行相关现有测试，并用临时 SQLite、合成行情/日历和真实 Chromium 补充边界复现。离线探针禁止联网；浏览器只允许访问隔离的本地服务，不调用真实模型、交易或通知。
- 这份报告接续[此前 14 类修复验收](dsa_fixes_2026-10-07.md)。R01、R02、R03、R06 是原质量/版本/并发主题下尚未覆盖的路径，不表示原有修复全部失效。

## 已确认的问题

| 编号 | 优先级 | 剩余差距 | 已复现的结果 |
| --- | --- | --- | --- |
| R01 | P1 | 问股/深度研究共享证据包缺失市场阶段 | 9 月 30 日读取 9 月 25 日行情，直接质量检查为 `stale`，共享包却为 `available` |
| R02 | P1 | 未验证日历仍可终结并冻结后验 | `calendar_verified=false` 时生成收益；补齐日历后仍保留错误终日和收益 |
| R03 | P1 | 无版本的旧策略样本绕过当前后验约束 | 没有任何当前后验，30 条旧命中样本仍把权重提高到 `1.176` |
| R04 | P2 | 信号复盘隐藏不可评估样本，缺少质量门槛 | 20 条结束信号仅 3 条可评估，显示 100% 命中、判断正常 |
| R05 | P2 | 多标的歧义确认覆盖已识别标的 | “分析贵州茅台和平安”确认平安银行后，贵州茅台消失 |
| R06 | P2 | 设置保存后的刷新不在草稿保护范围内 | 新输入 `audit-unsaved-model` 被迟到刷新覆盖为 `audit-saved-model` |
| R07 | P2 | 研究概览仍使用旧资产/信号状态假设 | 真实 ETF 持仓显示不可用；过期 active 信号仍显示为新鲜有效 |
| R08 | P2 | 市场阶段使用主机时间，没有统一市场时区 | 同一瞬间的 UTC 输入判盘前，上海时区输入判盘后 |

### R01：共享事实包遗漏行情质量所需的市场阶段

[local_context_pack](../src/services/research_artifact.py#L103) 从数据库构造行情、日线和财务事实后，没有给 `build_context_pack()` 传 `phase`。builder 在[质量检查](../src/services/research_artifact.py#L85)中显式传入空字典；[daily_quality](../src/services/data_freshness.py#L36) 缺少应有交易日时直接返回初始 `available`。

复现：固定 2026-09-30 16:00，最后行情和取得时刻均在 9 月 25 日。直接检查返回 `stale_quote`，共享包的 `phase={}`，quote 和 daily 两块却都为 `available`。这条入口供[问股工具](../src/services/chat_tools.py#L84)和[深度研究](../src/services/research.py#L204)消费，主诊断已传阶段的路径不能覆盖它。

对照：[上游 ContextPack 阶段与字段状态契约](https://github.com/ZhuLinsen/daily_stock_analysis/blob/ce364e457aab288863a5707e7b3df79786ad07f2/docs/analysis-context-pack.md#L67)。本地问题是阶段缺失导致质量判断被跳过，不要求上游与本地采用同一 freshness 函数。

修复验收：共享包在生成处绑定市场时刻和阶段；缺少足够上下文时明确未知/受限。分别验证问股、深度研究中的旧日期、盘中缓存跨收盘和未来取得时刻，确保质量状态与主诊断一致。

### R02：日历质量只是附注，未阻止后验进入不可变终态

[OutcomeEngine](../src/services/outcome_engine.py#L80) 使用降级交易日规则取得价格路径；虽然在第 102 行记录 `calendar_verified`，第 122 行仍可写入 `evaluated`。同版本已评估结果随后直接返回。[交易日历](../src/trading_calendar.py#L105) 无覆盖时把周一至周五视为交易日。

复现：无日历时，9/25 基价 10、9/28 收盘 11、9/29 收盘 12，1 日后验得到 9/28、+10%、`calendar_verified=false`。随后安装合成日历 `{9/25, 9/29, 9/30}`，按该日历应为 9/29、+20%，重评却仍为 9/28、+10%。这是明确的合成日历实验，不声称现实中的 9/28 是休市日。

问题影响共用该引擎的诊断、信号和策略观点；学习统计也没有过滤这个日历质量标记。对照[上游缺日历阶段保持未知](https://github.com/ZhuLinsen/daily_stock_analysis/blob/ce364e457aab288863a5707e7b3df79786ad07f2/src/core/trading_calendar.py#L375)与[按版本聚合策略后验](https://github.com/ZhuLinsen/daily_stock_analysis/blob/ce364e457aab288863a5707e7b3df79786ad07f2/src/services/skill_opinion_performance_service.py#L41)。冻结错误收益是本地复现，不推定上游存在相同的冻结实现。

修复验收：日历未经验证时不生成可用于学习的权威终态；补齐日历后允许完成。已经冻结的不可靠记录应通过新评估版本重算并保留旧证据，不能仅修改界面标签。

### R03：旧策略样本仍能参与当前自动调权

[SkillOpinionService._stats](../src/services/skill_consult.py#L271) 仅在 `row.skill_version` 非空时校验策略 hash 和当前版本后验；为空时仍回退使用历史 `hit/ret_5d`。

复现：30 条独立旧样本，`skill_version=None`、全部命中、收益 5%，当前 `ResearchOutcome` 数量为 0。`performance()` 仍返回 30 条、100%，`weights()` 返回 `1.176`。因此“旧数据可显示”和“旧数据可参与当前权重”尚未分离。

对照：[上游当前版本表现统计与最小样本数](https://github.com/ZhuLinsen/daily_stock_analysis/blob/ce364e457aab288863a5707e7b3df79786ad07f2/src/services/skill_opinion_performance_service.py#L41)、[权重对 unable 比例的约束](https://github.com/ZhuLinsen/daily_stock_analysis/blob/ce364e457aab288863a5707e7b3df79786ad07f2/src/services/skill_opinion_weight_service.py#L165)。本地还有单一 5 日统计与上游多窗口/无法评估桶的深度差异，不另计一类，也不要求照抄 Beta 先验参数。

修复验收：旧样本可保留历史展示，但只有已明确绑定当前策略及可靠当前后验的样本能调权；没有有效当前样本时权重保持 1。补充无版本、版本变更、日历未知、无法评估和重复样本的组合测试。

### R04：复盘没有暴露评估覆盖率和弱证据状态

[DecisionSignalService.review](../src/services/decision_signals.py#L250) 丢弃 `is_hit=None` 的信号，仅用剩余样本生成命中率；达到 3 条就输出偏差判断，没有 unable 比例或质量约束。[诊断提示词](../src/services/stock_diagnosis.py#L341) 会消费该复盘。

复现：20 条结束信号及对应当前版本后验，3 条已评估命中、17 条 unable；返回 `samples=3`、`hit_rate=100`、`bias=正常`，文字仅为“近 3 次信号命中 3 次（100%）”。数学上的 3/3 没错，但没有告诉读者 85% 样本无法评估，也没有观察性降级状态。

对照：[上游 ReviewMemory](https://github.com/ZhuLinsen/daily_stock_analysis/blob/ce364e457aab288863a5707e7b3df79786ad07f2/src/services/decision_signal_outcome_service.py#L384) 分别统计完成和无法评估，样本不足、无法评估比例高、质量低时保持 `observe`。上游阈值为 10 条、50%；本地可以采用经说明的阈值，但不能隐藏覆盖不足。

修复验收：返回总数、完成数、无法评估数/比例、版本和质量限制，弱证据不产生方向或信心校准；API、研究概览和提示词使用相同摘要。

### R05：确认一个歧义标的时丢掉同句中的明确标的

[web_intent.resolve](../src/services/web_intent.py#L23) 的确认分支执行 `task["targets"] = [answer]`，覆盖此前已识别的 targets；同一分句也只记录第一组歧义。

复现：“分析贵州茅台和平安”先得到明确目标 `600519` 和平安的两个候选；回复 `000001` 后，任务只剩平安银行，`requires_confirmation=false`。当前已修复的范围校验、任务顺序仍然存在，但不能防止同一任务内部丢目标。

对照：[上游确认结算](https://github.com/ZhuLinsen/daily_stock_analysis/blob/ce364e457aab288863a5707e7b3df79786ad07f2/src/agent/web_intent_resolver.py#L975) 保留已消解实体，并在原位置结算候选组。

修复验收：保留明确目标、表达顺序和多个待确认组；确认完成后实际执行两只股票，不能只测解析器返回不报错。范围限制仍需逐轮和执行前校验。

### R06：设置写入互斥没有覆盖紧随其后的刷新

[请求封装](../apps/web/src/api/client.ts#L24) 在 PUT 结束时解除设置写入锁。[LLMSettingsForm](../apps/web/src/pages/SettingsPage.tsx#L257) 随即 `void reload()`，刷新完成前表单已恢复可编辑；`data` 更新又[无条件覆盖本地表单](../apps/web/src/pages/SettingsPage.tsx#L224)。

真实 Chromium 复现：保存模型名 `audit-saved-model`，延迟其后的 GET；此时输入框已启用，可以改成 `audit-unsaved-model`；放行 GET 后又变成 `audit-saved-model`，没有页面异常。证据：[刷新前](audits/dsa-v1.2.0-review-2026-10-07/settings-refresh-before.png)、[刷新后](audits/dsa-v1.2.0-review-2026-10-07/settings-refresh-after.png)。

对照：[上游设置草稿契约](https://github.com/ZhuLinsen/daily_stock_analysis/blob/ce364e457aab288863a5707e7b3df79786ad07f2/docs/settings-draft-protection.md#L10) 明确覆盖模型保存及后续刷新。之前延迟 PUT 的测试通过，不等同于保存后的 GET 也得到保护。

修复验收：把保存和刷新协调成完整操作，或使用草稿修订号拒绝迟到响应覆盖；同时验证刷新失败、保存后切分类、导入后的刷新及连续编辑。全程锁定和允许继续编辑但保护新草稿均可，不能静默丢输入。

### R07：研究概览的资产能力与有效信号查询仍未统一

同一个 [StockProfileService](../src/services/stock_profile.py#L112) 有两条已确认的旧假设：

1. `_portfolio()` 只允许 `kind=stock`。临时实盘账本持有 `510300` 100 份，行情质量为 available、价格为 4；profile 对 `kind=etf` 却返回 `unavailable/not_tradable`。[页面](../apps/web/src/pages/StockPage.tsx#L160) 因而显示“持仓数据不可用”。
2. `_signals()` 按数据库 `status=active` 查询，没有查询时有效期检查。固定 9/30，9/10 已到期的买入信号仍以 `fresh` 返回；已经修复的组合风险 `active_summary()` 对同一信号返回空。[页面](../apps/web/src/pages/StockPage.tsx#L162) 显示买入、止损和目标，未展示该条信号的到期日。后台生命周期尚未运行、关闭调度或恢复旧数据时会出现此状态差异。

对照：[上游 profile 的分块质量和缓存持仓身份](https://github.com/ZhuLinsen/daily_stock_analysis/blob/ce364e457aab288863a5707e7b3df79786ad07f2/docs/stock-profile-api.md#L18)、[决策信号生命周期](https://github.com/ZhuLinsen/daily_stock_analysis/blob/ce364e457aab288863a5707e7b3df79786ad07f2/docs/decision-signals.md)。本地 ETF 持仓是现有业务能力，本项是聚合消费端未跟上；不声称上游也有相同 ETF 账本或相同 signals 分块。

修复验收：按统一证券能力读取股票/ETF 持仓，保留价格质量；统一查询“当前有效信号”，页面体现有效期和来源。指数不应因此被误认为可直接交易。

### R08：A 股市场阶段依赖主机时区

[current_phase](../src/services/market_phase.py#L65) 使用 `datetime.now()` 或输入 datetime 的原始小时；[交易时段判断](../src/trading_calendar.py#L120) 也使用主机时间。调度触发器却显式使用 Asia/Shanghai，存在两种时钟口径。

复现同一瞬间 `2026-09-30 08:00 UTC` 与 `16:00 Asia/Shanghai`：前者为盘前、完整日线日为 9/29；后者为盘后、完整日线日为 9/30。源码默认时钟也表明非中国时区主机存在同类风险。本轮没有修改主机时区。

对照：[上游 get_market_now](https://github.com/ZhuLinsen/daily_stock_analysis/blob/ce364e457aab288863a5707e7b3df79786ad07f2/src/core/trading_calendar.py#L215) 会将有时区的输入转换到市场时区，默认也使用该时区。

修复验收：建立统一市场时钟，明确无时区输入语义；同一瞬间的 UTC、上海时区、非中国主机环境应得到相同阶段、有效交易日和日线完整性。标准 Docker 已设置中国时区，不能把这个问题表述为所有默认容器都会出错。

## 全面核对范围

“本轮未确认新缺陷”表示源码对照及所选测试没有发现新的具体反例，不是所有真实供应商和平台的无条件保证。

| 领域 | 当前情况与剩余边界 |
| --- | --- |
| 国内证券身份、股票/ETF/指数 | 规范代码及别名已接入；聚合消费端有 R07 |
| 行情链、超时、冷却、复权、时间戳 | 已有实现；R01/R02/R08 是消费与时间判断问题，不是缺少数据源系统 |
| 数据中心、精确能力、混源批次、选股来源历史 | 已接入；此前 F08/F10 的实现和相关回归存在，本轮未确认新缺陷 |
| 模型、工具、主备、多 Key、搜索服务商 | SDK/LiteLLM、兼容参数和已有搜索源已接入；CLI/专用后端为可选扩展，付费账户需真实验收 |
| 资讯、RSS/Atom、NewsNow、情报管理 | 已接入；本轮未确认新缺陷 |
| 多分析员诊断、阶段护栏、决策风格 | 已接入；共享包和市场时钟有 R01/R08 |
| ContextPack、ResearchArtifact、历史报告身份 | 结构及报告绑定已接入；共享构建入口有 R01 |
| 选股规则、多因子、补数、风险惩罚、模型复核、解释 | 已接入；近分候选个性化轮换为可选差异 |
| ETF 双动量、真实交易日、短历史报告、防守现金 | 已接入；此前相关修复回归通过，本轮未把“缺 ETF 轮动”列为问题 |
| 真实/模拟账户、净值、公司行为、组合风险 | 已接入；净值质量相关测试通过，研究概览 ETF 持仓有 R07 |
| 决策信号、生命周期、反馈、风险与告警联动 | 已接入；后验/复盘/概览消费有 R02/R04/R07 |
| 多策略会诊、保守仲裁、观点样本 | 已接入；无版本旧样本有 R03，不能据此否认仲裁修复 |
| 统一后验、版本、不可变记录与学习 | 已接入；日历终态和统计证据门槛有 R02/R03/R04 |
| 运行日志、脱敏摘要、任务历史和恢复 | 已接入；相关回归通过，本轮未确认新缺陷 |
| 自选状态、下一步、截图识别、分享图 | 已接入；本轮未确认新缺陷 |
| AI 问股、限定证券、流式阶段、确认与任务顺序 | 已接入；混合明确/歧义实体有 R05 |
| 设置草稿、失败保存、导入、写入互斥 | 已接入；保存后刷新有 R06 |
| 调度、CLI、预算与取消 | 已接入；任务使用的市场时钟仍受 R08 影响 |
| 通知/Bot、降噪、测试与路由 | 已有多渠道和交互 Bot；同渠道专用模式不完全相同，见可选扩展 |
| Web、Electron、Docker、CI、发布 | v1.2.0 已正式发布，三平台附件及多架构镜像发布流程成功；已无 Qt6 运行链 |

## 六类可选扩展

这些确实与上游范围不同，但不应混入上述本地缺陷数量。是否全部引入应由本项目的目标市场和使用场景决定。

| 类别 | 上游能力与本地差异 | 上游依据 |
| --- | --- | --- |
| 1. 海外证券与组合 | 港美日韩台证券身份、日历、币种/汇率质量，以及 Futu 持仓标的读取、Longbridge 行情和券商 CSV 导入；本地国际资讯不等同海外个股引擎 | [证券范围](https://github.com/ZhuLinsen/daily_stock_analysis/blob/ce364e457aab288863a5707e7b3df79786ad07f2/docs/stock-profile-api.md)、[Futu 持仓](https://github.com/ZhuLinsen/daily_stock_analysis/blob/ce364e457aab288863a5707e7b3df79786ad07f2/src/brokers/futu/portfolio.py)、[Longbridge 行情](https://github.com/ZhuLinsen/daily_stock_analysis/blob/ce364e457aab288863a5707e7b3df79786ad07f2/data_provider/longbridge_fetcher.py)、[券商 CSV](https://github.com/ZhuLinsen/daily_stock_analysis/blob/ce364e457aab288863a5707e7b3df79786ad07f2/src/services/portfolio_import_service.py) |
| 2. 专用宏观工具 | FXMacroData 指标目录、历史序列和发布日历；本地已有国际背景，但没有这套专用工具目录 | [FXMacroData](https://github.com/ZhuLinsen/daily_stock_analysis/blob/ce364e457aab288863a5707e7b3df79786ad07f2/docs/FXMACRODATA.md) |
| 3. 模型运行后端 | Codex 本地 CLI/App Server 及其运行诊断，Hermes 专用本地 HTTP 生成渠道；本地自定义 OpenAI 兼容地址不等同专用后端完整约束。Hermes 不能笼统称为 CLI 后端 | [Codex Agent](https://github.com/ZhuLinsen/daily_stock_analysis/blob/ce364e457aab288863a5707e7b3df79786ad07f2/src/agent/codex_agent_backend.py)、[Hermes](https://github.com/ZhuLinsen/daily_stock_analysis/blob/ce364e457aab288863a5707e7b3df79786ad07f2/src/llm/hermes.py) |
| 4. 外部助手接入 | OpenClaw Skill、Grok Bot 的示例和协议衔接；本地有 REST/Bot，但未提供这些特定集成 | [OpenClaw](https://github.com/ZhuLinsen/daily_stock_analysis/blob/ce364e457aab288863a5707e7b3df79786ad07f2/docs/openclaw-skill-integration.md)、[Grok Bot](https://github.com/ZhuLinsen/daily_stock_analysis/blob/ce364e457aab288863a5707e7b3df79786ad07f2/docs/grok-bot-integration.md) |
| 5. 通知模式、语言与海外情报 | 飞书/Lark App Bot 主动推送、Slack Bot 同频道文字/图片、AstrBot；韩文报告、美股 Reddit/X/Polymarket 情报。本地已有飞书交互 Bot 和 webhook、Slack webhook、中文/英文报告，不能说这些渠道整体缺失 | [通知模式](https://github.com/ZhuLinsen/daily_stock_analysis/blob/ce364e457aab288863a5707e7b3df79786ad07f2/docs/notifications.md#L8)、[报告语言](https://github.com/ZhuLinsen/daily_stock_analysis/blob/ce364e457aab288863a5707e7b3df79786ad07f2/src/report_language.py#L11)、[社交情报](https://github.com/ZhuLinsen/daily_stock_analysis/blob/ce364e457aab288863a5707e7b3df79786ad07f2/README.md#L60) |
| 6. 近分候选轮换 | 匿名 `variant_seed` 只在质量接近且满足约束的候选间进行有界轮换；本地固定排序仍是有效产品选择 | [选股轮换边界](https://github.com/ZhuLinsen/daily_stock_analysis/blob/ce364e457aab288863a5707e7b3df79786ad07f2/docs/screening-engine.md#L118) |

## 验证记录与证据

本轮执行的相关回归：

```sh
.venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_dsa_audit_regressions.py tests/test_dsa_trace_regressions.py \
  tests/test_web_intent_stages.py tests/test_alignment_data_contracts.py \
  tests/test_portfolio_nav_quality.py tests/test_research_alignment.py \
  tests/test_full_alignment.py
```

结果：**105 passed，4 个依赖弃用警告**。这些现有测试通过，但未覆盖本轮新复现的边界。

新增探针均为审计证据，记录当前问题行为，不是要求修复后继续保持这些行为的回归测试：

```sh
PYTHONPATH=. .venv/bin/python docs/audits/dsa-v1.2.0-review-2026-10-07/offline_probe.py
.venv/bin/python docs/audits/dsa-v1.2.0-review-2026-10-07/browser_probe.py
```

- 离线 **8 个场景**，均运行完成，见[脚本](audits/dsa-v1.2.0-review-2026-10-07/offline_probe.py)和[原始结果](audits/dsa-v1.2.0-review-2026-10-07/offline-results.json)。
- Chromium **1 个场景**，明确复现草稿覆盖，**0 个页面异常**，见[脚本](audits/dsa-v1.2.0-review-2026-10-07/browser_probe.py)和[原始结果](audits/dsa-v1.2.0-review-2026-10-07/browser-results.json)。首次浏览器启动被本机沙箱禁止，获准在沙箱外启动后完成；不是产品启动故障。
- [验证汇总](audits/dsa-v1.2.0-review-2026-10-07/verification.json)固定了基线、场景和验证范围。

## 发布及未验证范围

[v1.2.0 正式 Release](https://github.com/hongheshan-svg/quant_tools/releases/tag/v1.2.0) 于 2026-10-07 07:11:55 UTC 发布，非草稿、非预发布，14 个附件包含 Windows 安装包、macOS arm64/x64 的 dmg/zip、Linux AppImage 及更新元数据。[发行流程 37583590548](https://github.com/hongheshan-svg/quant_tools/actions/runs/37583590548) 对应本次业务基线，16 个 job 全部成功，包括后端/Web/桌面/浏览器检查、四个桌面构建、Docker amd64/arm64 与多架构 manifest 发布。因此以前“正式版本仍是 v1.1.1”的结论已过期。

本轮没有重新运行全量测试、重新安装所有操作系统的发行包，也未用真实付费账户测试所有模型/行情源、完整 8 年 ETF 在线回补或通知投递。相关缺少验收的事项不能自动推定为实现错误。此次复核已足以确认上列 8 类问题，尚未实施修复。
