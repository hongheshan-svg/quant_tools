# daily_stock_analysis 全面复核（2026-10-07）

## 基线与结论

本次重新获取并读取 [ZhuLinsen/daily_stock_analysis](https://github.com/ZhuLinsen/daily_stock_analysis)，固定上游为 [`ce364e457aab288863a5707e7b3df79786ad07f2`](https://github.com/ZhuLinsen/daily_stock_analysis/commit/ce364e457aab288863a5707e7b3df79786ad07f2)，提交时间 2026-10-05 15:52:26 +08:00。相比此前参考的 `be148f39`，上游增加了 **22 个提交**。本地比较对象是已经推送的 `44936fc4bcf9ef76122dc647dc68e859079a8408`。

**此前最初 12 项和扩展补齐已有实现；当前仍有 18 项具体差距：7 项现有流程的正确性与可靠性问题，11 项功能深度或新增能力。** 不能把旧审计中的修改前状态重复算作缺失，也不能把同名模块、测试通过或依赖版本更高解释成全部行为一致。

范围沿用此前的 A 股、国内 ETF/指数。海外个股、多币种持仓、本地 CLI 模型和新增机器人作为可选扩展单列。上游也有自己的边界，以下不是要求照搬目录、接口路径、技术依赖或策略收益承诺。

本轮完成源码比较、临时数据库和假供应商复现、真实 Chromium 设置页验证、GitHub CI/发行/数据源检查只读核实。**本轮没有修复下面的问题。** 前期测试数字见[最初 12 项核验](dsa_initial_12_completion_2026-10-07.md)，不能代替本轮新增场景。

## 现有流程优先修复：P1

### 01 · 行情新鲜度没有贯通选股和 profile

- 本地 [StockProfileService](../src/services/stock_profile.py) 第 63 行只把股票日期与数据库的最大股票日期比较；整库都旧时仍返回 `fresh`。ETF/指数分支将 `latest_market` 设为空，只要有记录便返回 `fresh`。
- [StrategyScreener.run](../src/strategy/screener.py) 第 396 行默认取数据库最大日期，成功门槛是覆盖数量和历史长度；[quality](../src/strategy/screening_pipeline.py) 第 39 行检查口径和来源，但没有校验当前应有的已收盘交易日。
- 离线数据库只有 `2025-11-07` 的末尾行情时，股票和 ETF 都返回 `fresh`；在覆盖门槛为 1 的隔离配置下，实时选股仍返回 `success`。降低门槛只为缩小样例；全库都有足够旧数据时同样缺少日期门槛。`updated_at` 为今天也不能证明源行情为今天。

上游参考：[日线缓存校验](https://github.com/ZhuLinsen/daily_stock_analysis/blob/ce364e457aab288863a5707e7b3df79786ad07f2/src/services/screening/daily.py#L515)。需要统一按实际市场阶段、交易日历、观测日期和抓取时刻判断新鲜度；显式历史查询与实时运行分别处理。验收覆盖整库陈旧、停牌、节假日、盘中缓存跨收盘，以及 ETF/指数。

### 02 · 每日任务失败仍可能显示完成

[run_job](../src/scheduler.py) 第 484 行丢弃 `run_isolated()` 的布尔结果。[立即运行接口](../api/v1/system.py) 第 172 行没有结果错误判定。[后台任务](../api/tasks.py) 按正常返回写入 `done`。另外 `_run_signal_generation()` 等内部任务捕获异常并返回，`run_job_entry()` 第 492 行随后返回退出码 0。

两条路径都已复现：隔离任务返回 `False` 后，任务为 `done / error="" / result=null`；信号生成抛出异常、错误报告已调用，子进程入口仍返回 0。这是已有任务契约修复中的遗漏，不是所有后台任务都没有错误处理。

验收：采集/分析失败、合法跳过、部分结果、超时和异常退出具有明确结果；API、退出码、运行记录、通知和页面终态一致。

### 03 · `--once` 绕过每日任务隔离

[ONCE_STEPS / run_once](../src/scheduler.py) 第 725、733 行直接执行原始每日函数，没有走 `run_job()`。离线探针确认开启 `scheduler.isolate_daily_jobs` 仍不调用隔离入口。[每日 Actions](../.github/workflows/daily-analysis.yml) 使用该入口，因此每日任务分钟级预算没有贯通 CLI/Actions。

这不表示所有 `--once` 网络请求都没有预算：采集、模型和个股诊断已有自己的限制。缺口是每日步骤的外层隔离及整步失败传播。验收应覆盖 CLI、Web 手动运行和定时运行使用相同的每日任务入口与退出语义。

### 04 · Web 与 CLI 调度缺少跨进程计划去重

[build_scheduler](../src/scheduler.py) 第 554 行配置各进程的 APScheduler；[TaskManager](../api/tasks.py) 第 89 行仅在本管理器内去重正在运行的手动任务。没有以“计划时刻 + 工作内容”在共享数据库原子认领每日任务的实现。并行运行 `server.py` 和 `main.py` 时，可能重复分析、写报告和推送。

上游新增 [scheduled_analysis_claim.py](https://github.com/ZhuLinsen/daily_stock_analysis/blob/ce364e457aab288863a5707e7b3df79786ad07f2/src/services/scheduled_analysis_claim.py)，CLI/Web 在实际执行边界共用 SQLite 唯一认领，认领时冻结配置。失败后保留认领，避免已经部分推送的任务自动重发；手动补跑另行允许。

验收：两个进程同一计划仅执行一次；不同计划/工作内容独立；错误认领不能悄悄继续执行；配置刷新不改变已认领工作的内容。该判断来自源码，本轮没有启动真实调度器或发送通知。

### 05 · 设置分类切换会丢弃未保存编辑

[SettingsPage](../apps/web/src/pages/SettingsPage.tsx) 第 47 行按分类条件挂载表单，各表单拥有自己的局部状态。切换分类会卸载表单；没有统一草稿登记、站内离开保护或浏览器关闭保护。

真实 Chromium 复现：选股动量权重从 `0.2` 编辑为 `0.73`，切到“推送”再返回，恢复为 `0.2`，没有确认弹窗。截图：[切换前](screenshots/dsa-audit-2026-10-07/settings-draft-before.png)、[切换后](screenshots/dsa-audit-2026-10-07/settings-draft-after.png)。

上游参考：[设置草稿保护](https://github.com/ZhuLinsen/daily_stock_analysis/blob/ce364e457aab288863a5707e7b3df79786ad07f2/docs/settings-draft-protection.md)。除分类切换保留草稿外，还应覆盖返回/离开、刷新/关闭、导入备份、重置、退出登录和保存后的刷新失败。上游草稿也只保存在页面内存，不承诺强制退出后恢复。

### 06 · 持仓估值和风险缺少可用性门槛

[RealPortfolioService.positions](../src/services/real_portfolio.py) 第 541 行没有报价时直接用平均成本填入 `market_price`，返回中没有报价可用性、观测日期、来源和陈旧标记。[PortfolioRiskService](../src/services/portfolio_risk.py) 第 100 行直接据此计算权重和止损。[RiskPanel](../apps/web/src/components/RiskPanel.tsx) 第 8 行在回撤缺失时用零占位；行业依据最近一次涨停记录，未知分类没有覆盖率状态。

已复现：没有任何行情的持仓返回成本价 `10`、市值 `100`，报价质量字段均缺失。会把成本估值带入风险指标。行业“未知”已有文字，问题是缺少整体质量状态，不能把局部已分类部分解释成完整行业风险。

上游参考：[组合风险与暴露的可用性边界](https://github.com/ZhuLinsen/daily_stock_analysis/blob/ce364e457aab288863a5707e7b3df79786ad07f2/docs/portfolio-risk-exposure-dashboard.md)。国内单币种也需要价格/分类覆盖和至少两个有效估值点；缺价、过期价、成本代替报价和无历史必须披露，并对相关风险块降级。这一项不要求扩展海外汇率。

### 07 · 出入金已经记账，但组合回撤尚未使用

[PortfolioRiskService._fills_and_initial_cash / _drawdown](../src/services/portfolio_risk.py) 第 182、200 行用“当前现金 + 全部买入 − 全部卖出”反推一个固定期初资金；[replay_nav](../src/services/portfolio_risk.py) 第 31 行只重放买卖与收盘价，没有出入金、费用和公司行为的完整事件序列。

临时真实账本复现：入金 200，买入 100，持仓市值随后降为 50，第二天收盘后出金 100。现金账本正确返回 0；风险模块反推期初资金为 100，回撤为 **−50%**。以原有资产 200 为起点并剔除期末出金影响，该例回撤应为 **−25%**。

这是本地指标正确性问题，不声称上游已解决任何形式的现金锚点、税费或公司行为。验收应先定义按日期末处理资金流的收益/回撤口径，再将完整账本接入；不能用累计收益金额代替时间加权净值，也不能改写已存历史结果。

## 功能深度与新增能力：P2

| 编号 | 当前与上游的具体差距 | 本地证据与验收重点 |
| --- | --- | --- |
| 08 · 证据时间语义 | 上游把 `provider_timestamp`、context `timestamp` 和 `fetched_at` 分开，公共概览也保留允许公开的时间。本地主要用单个 `as_of`，研究产物没有统一传递观测/抓取两种时间。 | [research.py](../src/schemas/research.py)、[research_artifact.py](../src/services/research_artifact.py) 第 38、123 行。资讯库存储了发布时间/采集时间，不能说没有任何时间字段；缺口在统一研究协议及页面。验收：观测时间未知时不能拿报告创建时间替代，新抓到的旧报价不能标新鲜。 |
| 09 · 超时请求隔离 | 已有最多 8 个工作槽、总预算和外层进程终止；没有按供应商/接口隔离仍在运行的超时请求。相同阻塞接口再次调用会占新槽。 | [bounded_call](../src/collectors/request_budget.py) 第 26 行。两次超时启动了两个相同工作线程。参考上游 provider-operation quarantine，按接口隔离，不能连健康后备源一起封锁；不是“无限新增线程”。 |
| 10 · ETF 双动量轮动 | 缺纯规则 ETF 资产轮动、周/月信号、次一交易日执行、正动量筛选、防守资产/现金、换仓缓冲、费用、参数平原和基准报告。现有 ETF 诊断、指数和股票回测不能代替它。 | [fund_diagnosis.py](../src/services/fund_diagnosis.py)、[strategy_backtest.py](../src/strategy/strategy_backtest.py)、[main.py](../main.py)。本地没有 `--etf-rotation` 或对应服务。验收：固定池、已收盘日线、明确前复权、真实日历、缺价不虚构成交。上游目前也是 CLI 能力，没有 Web 设置页，也未接入其 `--schedule`。 |
| 11 · 入选与时点解释 | 已有分因子分数、策略理由、模型理由、风险扣分和候选取证；没有逐项 `why_selected / why_now` 的来源、`observed/inferred/unknown` 和解释质量契约。 | [Pick](../src/strategy/screener.py) 第 268 行、[ScreeningPage](../apps/web/src/pages/ScreeningPage.tsx) 第 69 行。验收：真实零值与缺失区别；旧新闻/无日期新闻不能成为当前事实；模型推断和后分析调分分别留来源；历史页保留当次解释。 |
| 12 · 单候选逐条件检查 | 上游新增只接受用户提供的有限扁平 snapshot 的 `/screen/check`，返回每个硬条件的通过/失败/缺失，不取实时数据。本地只有整批运行和结果，没有等价诊断入口。 | [screening.py](../api/v1/screening.py)、[screening_rules.py](../src/strategy/screening_rules.py)。验收：逐条件解释“不入选”的原因；缺字段不当作零；未知策略/非法输入准确报错；没有外网或额外模型调用。 |
| 13 · 能力中心精度 | 已有“运行状态 / 能力总览 / 来源与优先级”、源健康记录和回退链；相比上游，缺精确 provider × 数据集 × 适用场景、当前数据集质量/实际使用源和优先级配置来源的统一只读响应。 | [data_capabilities.py](../src/services/data_capabilities.py)、[SourcesPage](../apps/web/src/pages/SourcesPage.tsx)。验收：某源曾成功不能说明当前批次正常；能力未知与探测成功分开；冷启动、部分失败、缓存降级分别展示。范围先覆盖国内股票/ETF/指数，不需要生成海外空矩阵。 |
| 14 · 确定性问股意图与歧义确认 | 已有显式 `stock_context`、股票别名搜索、工具范围约束、多策略和原生工具协议；自由文本主要交给模型选择工具，没有上游的规则解析、多任务保序、追问继承和歧义确认状态。 | [stock_chat.py](../src/services/stock_chat.py) 第 178、246 行、[chat.py](../api/v1/chat.py)。验收：简称歧义先确认；“分析 A，然后看看组合”产生保序任务；“这个呢”正确继承；新问题撤销旧确认；任何路径仍受原工具范围约束。 |
| 15 · 问股流式阶段契约 | 已有 SSE、状态文字、工具与结果、增量答案和取消/总预算；没有稳定的阶段开始/完成、超时与预算跳过结构，页面也没有未知进度事件的通用回退。 | [stock_chat.py](../src/services/stock_chat.py) 第 447 行、[ChatPage](../apps/web/src/pages/ChatPage.tsx) 第 124 行。验收：阶段、耗时、剩余预算、跳过原因可回放；未知事件保留；连接关闭清理；最终失败不只有模糊提示。现有 RunLog 不能替代进行中的阶段反馈。 |
| 16 · 自选行“状态与下一步” | 本地显示最新行情、诊断、来源、汇总今日已分析数量和独立任务面板；缺每行聚合“今日已更新/历史待更新/查询未知/任务运行中/下一步”。 | [WorkspacePage](../apps/web/src/pages/WorkspacePage.tsx) 第 90、100 行、[WatchlistPage](../apps/web/src/pages/WatchlistPage.tsx) 第 104 行。验收：今日/历史/无报告、查询失败、任务运行中分别提示，主体仍打开报告，删除与批量分析语义保持一致。上游这一功能也没有价格变化或 thesis 触发状态，不把规划写成已实现。 |
| 17 · 告警作用域与规则来源 | 单股价格/量能/技术规则、自动持仓止损、大盘环境提醒、持久冷却、稳定 ID、日志过滤已有；缺可配置规则的自选集合/指定账户持仓/账户级风险 scope，规则 API 也没有配置文件/环境覆盖来源及有效数量说明。 | [alert_service.py](../src/services/alert_service.py) 第 65、241 行、[market.py](../api/v1/market.py) 第 186 行。验收：集合动态展开，账户集中度/回撤/价格陈旧独立规则，父 scope 不作股票代码校验；来源与生效数正确；过滤条件在增删后保留。与现有自动提醒区分，避免重复触发。 |
| 18 · 新增供应商入口 | 缺上游新加的妙想（Miaoxiang）行情适配器和 Requesty 模型平台预设。 | [数据采集器](../src/collectors)、[llm_platforms.py](../src/analyzers/llm_platforms.py)、[build_route](../src/analyzers/llm_client.py) 第 225 行。妙想需超时、数据能力和万/亿/币种数值校验。Requesty 属方便配置的差距：本地可手动填 OpenAI 兼容地址，且现有路由已统一包 `openai/`，不能认定其 vendor/model 前缀必然失效。补预设后需离线路由回归及有权限账户验证。 |

上述 P2 对应的上游固定源码：

- 08：[公共证据概览](https://github.com/ZhuLinsen/daily_stock_analysis/blob/ce364e457aab288863a5707e7b3df79786ad07f2/src/analysis_context_pack_overview.py#L197)、[研究证据](https://github.com/ZhuLinsen/daily_stock_analysis/blob/ce364e457aab288863a5707e7b3df79786ad07f2/src/services/research_artifact_service.py#L107)。
- 09：[供应商/接口超时隔离](https://github.com/ZhuLinsen/daily_stock_analysis/blob/ce364e457aab288863a5707e7b3df79786ad07f2/data_provider/base.py#L3381)。
- 10：[ETF 轮动规则和边界](https://github.com/ZhuLinsen/daily_stock_analysis/blob/ce364e457aab288863a5707e7b3df79786ad07f2/docs/etf-rotation.md)。
- 11：[选股解释契约](https://github.com/ZhuLinsen/daily_stock_analysis/blob/ce364e457aab288863a5707e7b3df79786ad07f2/docs/screening-explanations.md)。
- 12：[snapshot 检查接口](https://github.com/ZhuLinsen/daily_stock_analysis/blob/ce364e457aab288863a5707e7b3df79786ad07f2/api/v1/endpoints/screening.py#L170)。
- 13：[数据能力服务](https://github.com/ZhuLinsen/daily_stock_analysis/blob/ce364e457aab288863a5707e7b3df79786ad07f2/src/services/data_capability_service.py)、[数据中心](https://github.com/ZhuLinsen/daily_stock_analysis/blob/ce364e457aab288863a5707e7b3df79786ad07f2/docs/data-center-diagnostics.md)。
- 14：[意图解析器](https://github.com/ZhuLinsen/daily_stock_analysis/blob/ce364e457aab288863a5707e7b3df79786ad07f2/src/agent/web_intent_resolver.py)。
- 15：[流式事件协议](https://github.com/ZhuLinsen/daily_stock_analysis/blob/ce364e457aab288863a5707e7b3df79786ad07f2/docs/agent-stream-events.md)。
- 16：[自选状态映射](https://github.com/ZhuLinsen/daily_stock_analysis/blob/ce364e457aab288863a5707e7b3df79786ad07f2/docs/watchlist-next-action.md)。
- 17：[告警 scope 矩阵](https://github.com/ZhuLinsen/daily_stock_analysis/blob/ce364e457aab288863a5707e7b3df79786ad07f2/docs/alerts.md#L281)、[规则来源计数](https://github.com/ZhuLinsen/daily_stock_analysis/blob/ce364e457aab288863a5707e7b3df79786ad07f2/src/services/alert_worker.py#L203)。
- 18：[妙想适配器](https://github.com/ZhuLinsen/daily_stock_analysis/blob/ce364e457aab288863a5707e7b3df79786ad07f2/data_provider/miaoxiang_fetcher.py)、[Requesty 平台预设](https://github.com/ZhuLinsen/daily_stock_analysis/blob/ce364e457aab288863a5707e7b3df79786ad07f2/apps/dsa-web/src/components/settings/llmProviderTemplates.ts#L164)。

## 已覆盖：不再重复列为缺失

| 领域 | 当前已有行为 | 仍需注意的边界 |
| --- | --- | --- |
| 最初 12 项 | 每日任务隔离、搜索结果状态、冻结产物自检、实盘出入金、完整英文诊断、7 块 profile、信号关闭/作废、RSS 模板、35 指数、股票别名、Anspire/MiniMax 搜索、6 个新模型预设 | 01–03、06–07 是接入端的遗漏，不能因主能力存在而忽略。 |
| A 股数据链 | AkShare、Tushare、Pytdx、Baostock、TickFlow、efinance、腾讯/新浪等，回退、熔断、工作预算、尝试持久化与子进程回传 | 超时重复请求与端到端新鲜度见 01、09；未配置账户的权限不由离线测试保证。 |
| 数据真实性 | 金额单位/有限值校验、合法空与失败区分、历史修订归档、价格口径、财务不可变快照与候选受控补数 | 时间语义传递和组合质量门槛见 06、08。 |
| 选股 | 6 原有策略、5 YAML 规则、10 多因子配置；候选新闻/事件、风险否决、集中度、可选模型重排、深度复核与历史运行 | 数量不是全部行为一致的证据；剩余解释与单条件诊断见 11–12。热点/主线已有热度、阶段、龙头与跟随股，不是完全缺热点功能。 |
| 研究与后验 | ContextPack、ResearchArtifact、报告身份绑定、多策略共识/审议、1/3/5/10 日后验、冻结结果、版本/价格修订、审慎样本权重 | 股票策略回测目前披露为观察收益，不能当成含共享资金和完整成交成本的组合收益。ETF 轮动另见 10。 |
| 问股 | 显式股票范围、最多 4 策略、原生工具 ID/轨迹、JSON 后备、上下文预算、独立进程和取消 | 规则意图和流式阶段仍有差距，见 14–15。 |
| 提醒与资讯 | 稳定规则 ID、持久冷却、触发/渠道日志、分页筛选、测试；RSS/Atom/NewsNow 持久源管理与去重 | 配置来源与集合/账户规则见 17。 |
| Web/桌面 | React + TypeScript + Vite + Tailwind，同一套 Electron 页面；路由、历史报告、导出、配置、登录限流和任务恢复已有验收 | 设置草稿见 05；不能以以前的页面冒烟推导所有新增错误状态已经覆盖。 |
| 工程门禁 | 3 分片后端、Python 编译与 Ruff 关键错误、资源检查、Web lint/test/build、桌面测试、真实浏览器隔离服务和 Docker 启动冒烟 | 已排除 LiteLLM 1.82.7/1.82.8；旧审计“无浏览器门禁/无 Docker 冒烟/未排除版本”的状态已过时。新增场景需补回归。 |
| 发布 | 英文 Actions 名称、中文 Release Notes、Windows/Linux/macOS 双架构安装包、Docker amd64/arm64 工作流和更新清单合并 | 最新源码与现有安装包版本不同，见运行证据。macOS Developer ID 公证不是本地独有的上游对齐差距。 |

## 上游新增 22 个提交的映射

| 上游提交 | 主题 | 本地判定 |
| --- | --- | --- |
| `ce364e45` | ETF 双动量轮动 | 未实现，10 |
| `fff9dd6a` | 排名前补充并隔离新闻/事件 | 候选取证已在重排前执行；来源/日期解释还不足，08、11；本轮未认定本地发生了跨候选串证 |
| `08d9a0c7` | 选股入口与状态失败 | 入口、独立错误和部分数据提示已有，不列为整项缺失 |
| `77e41928` | 日线缓存新鲜度与策略元数据 | 元数据已有；新鲜度缺口，01 |
| `b15101f2` | 设置草稿与保存状态 | 未对齐，05 |
| `40fbc67d` | 妙想超时和能力限制 | 没有适配器，18 |
| `280cdb66` | 妙想接入与金额解析 | 没有适配器，18 |
| `ebab2ba9` | 数据能力中心 | 已有总览，契约深度不同，13 |
| `61463531` | 确定性 Web 意图 | 未对齐，14 |
| `139672ed` | 自选下一步 | 已有工作台，缺逐行映射，16 |
| `b210d2c6` | 组合风险与暴露看板 | 国内组合已有，价格/分类/历史质量不足，06；海外币种部分为扩展范围 |
| `283186e9` | 观测/抓取时间 | 部分字段已有，统一协议未贯通，08 |
| `5fa42fd9` | 选股解释来源 | 已有理由，缺来源与事实/推断分类，11 |
| `1a0948a3` | Requesty 预设与路由 | 可手配兼容地址，缺预设；不认定已有路由必坏，18 |
| `18154989` | FXMacroData 只读宏观工具 | 未实现，可选扩展 |
| `382b71aa` | snapshot 逐条件检查 | 未实现，12 |
| `709393be` | macOS 选股资源打包 | 本地已有 YAML/策略/模板打包与实际冻结产物自检，无需照搬 argv |
| `0050f83e` | 超时 AkShare 请求重复抑制 | 已有全局槽位上限，缺接口隔离，09 |
| `d34e5ee9` | 共享每日调度去重 | 未对齐，04 |
| `c8227945` | 环境规则来源/有效数量 | 缺等价来源摘要，17 |
| `b6ee2948` | 告警操作后保留筛选 | 本地筛选在页面局部状态，已有日志筛选；不列为“无筛选”，新增 scope 时要回归 |
| `9d7d333a` | 基本面等待的实际耗时 | 本地预算已用 `time.monotonic()`，没有发现相同固定扣减问题 |

## 可选范围扩展

- **海外个股与多币种组合**：港/美/日/韩/台市场标的、日历、币种和数据路由，以及 Futu/Longbridge 只读持仓导入与 FX 质量。本地国际背景采集不是海外个股分析。继续按当前国内范围评估，不把这些当成必须修复。
- **FXMacroData 工具**：上游通过内置操作目录注册只读宏观接口，USD 基础能力无需 Key。可补国内分析的宏观背景，但没有这个特定供应商不表示本地没有任何宏观/国际信息。参考[工具实现](https://github.com/ZhuLinsen/daily_stock_analysis/blob/ce364e457aab288863a5707e7b3df79786ad07f2/src/agent/tools/fxmacrodata_tools.py)。
- **其他模型运行后端**：Codex/Hermes 等 CLI 后端、会话续接和外部 Skill/API 兼容层。当前 LiteLLM/OpenAI SDK 的主备、多 Key、冷却、原生工具、参数兼容和用量已经具备，扩展后端是产品选择。
- **通知和语言扩展**：Feishu/Lark 主动 App Bot/文件或云文档、Slack Bot、AstrBot、韩语报告、海外社交舆情。本地 14 个通知渠道与已有聊天机器人不能按上游渠道名称数判“缺一套通知”。参考[上游通知说明](https://github.com/ZhuLinsen/daily_stock_analysis/blob/ce364e457aab288863a5707e7b3df79786ad07f2/docs/notifications.md)。

## 复现与实际运行证据

[离线结果 JSON](dsa_alignment_probe_2026-10-07.json) 全部来自假报价、假供应商和临时 SQLite；没有读取生产账户或真实持仓，没有调用模型、交易或推送。

| 探针 | 结果 | 对应项 |
| --- | --- | --- |
| 旧股票/ETF 报价 | 末尾日期 `2025-11-07`，均 `fresh` | 01 |
| 旧库实时选股 | 覆盖与历史门槛满足，仍 `success` | 01 |
| 假隔离失败 → TaskManager | `done`、空错误、空结果 | 02 |
| 信号生成内部异常 | 错误报告被调用，入口退出码仍 0 | 02 |
| `--once` 开启隔离 | 没有调用隔离执行 | 03 |
| 缺价持仓 | 成本替代报价，质量字段缺失 | 06 |
| 实盘出入金与回撤 | 现金为 0；期初反推为 100；回撤 −50%，流量调整样例为 −25% | 07 |
| 同接口连续超时 | 两个仍在运行的相同工作线程 | 09 |
| Chromium 未保存设置 | `0.2 → 编辑 0.73 → 切分类 → 0.2`，无提示 | 05 |

实际 GitHub 状态查询于本次审计：

- 当前代码 CI [`37554100586`](https://github.com/hongheshan-svg/quant_tools/actions/runs/37554100586) 为 `success`，HEAD 为 `44936fc`。这是先前推送的工作流，不是本轮新增场景的回归通过证明。
- 数据源检查 [`37439511256`](https://github.com/hongheshan-svg/quant_tools/actions/runs/37439511256)（10 月 6 日）、[`37287056356`](https://github.com/hongheshan-svg/quant_tools/actions/runs/37287056356)（10 月 5 日）均成功。它们证明工作流实际运行，不能保证所有供应商长期在线。
- 最新公开版本为 [`v1.1.1`](https://github.com/hongheshan-svg/quant_tools/releases/tag/v1.1.1)，发布于 2026-10-02 14:14:50 UTC。资产包含 Windows 安装程序、Linux x86_64 AppImage、macOS x64/arm64 的 DMG/ZIP，以及三个平台的更新清单和相应 blockmap。
- `v1.1.1` 后本地/远端有 3 个提交，包含完整英文诊断补修、出入金非法金额防护及核验文档。**现有 v1.1.1 安装包不会因 main 更新而自动包含这些提交。** 后续修复验收后应发布新版本，而非移动旧标签。Docker 多架构发布代码已核对，本轮没有拉取镜像或核实其远端 digest。

本轮没有重跑全部后端/前端测试或重新安装三个系统的发行包，也没有验证妙想/Requesty 的真实账户权限。没有源码或实测证据的在线行为保持“待验证”，不自动列为缺失。

## 建议实施顺序

1. **先修 01–07**：时效、失败终态、CLI 隔离、跨进程去重、设置草稿、组合质量和现金流回撤。新增回归必须覆盖这次成功复现的场景。
2. **再贯通 08–09、11–17**：统一时间/质量/解释协议、请求隔离、单条件诊断、能力中心、问股意图和阶段、自选状态、规则 scope。API、历史记录和 Web 使用同一契约。
3. **再补 10、18 并发版**：ETF 规则轮动与新供应商入口分别验收。继续保留国内范围；ETF 回测不接真实自动交易，供应商支持也不作为稳定性或盈利保证。发布新的中文说明与三平台/Docker 产物。
