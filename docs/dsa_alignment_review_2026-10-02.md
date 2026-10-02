# daily_stock_analysis 全面复核（2026-10-02）

> 本文保留该阶段的审计/实施快照。后续代码补齐及最新验证请看[全面补齐实施与最终验收](dsa_alignment_implementation_2026-10-02.md)，下文的待修复状态和测试数字不代表最终版本。


## 基线、范围与结论

本次重新读取并拉取了 [ZhuLinsen/daily_stock_analysis](https://github.com/ZhuLinsen/daily_stock_analysis) 的 `main`，本次获取的远端提交仍是 [`be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f`](https://github.com/ZhuLinsen/daily_stock_analysis/commit/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f)，提交时间为 2026-10-01 15:32:09 +08:00。本地比较对象是当前工作区，包含此前已经完成的三轮修改和本次部署修复，不能用原始审计中的修改前状态代替。

结论：**核心技术栈与主要 A 股页面、研究流程已经接近；剩余差距集中在数据服务的一致性、完整的选股排序链路、研究协议深度和界面中的证据对应关系。** 还有几处本地正确性问题，不能因为有同名模块或测试数量较多而认定全部对齐。

范围继续采用用户确认的现有 A 股功能，包括国内 ETF/指数。海外市场、多币种持仓、本地 CLI 模型后端属于暂不纳入的范围扩展。本文不计算“对齐百分比”，不以依赖版本、页面数量或策略数量代替行为比较。

证据来自双方源码、配置、构建和工作流，以及本次离线复现；UI 比较针对组件、路由、数据绑定与已有验收截图。本次没有重新运行所有页面的浏览器验收，也没有验证所有供应商的在线权限或长期可用率。上游实现同样不代表数据源永不失败或策略已经具有稳定超额收益。

优先级：P1 为现有正确性、稳定性及关键风控；P2 为功能深度与工程验收；“待验证”表示不能由现有证据确认，并不自动等于代码缺失。

## 技术栈：主体已经对齐

下表前端和桌面版本为依赖清单的版本约束，非对每个构建环境的安装版本承诺。

| 层面 | 上游 | 当前本地 | 判断 |
| --- | --- | --- | --- |
| Web 框架 | React 19.2、Router 7.13、TypeScript 5.9 | React 19.3、Router 7.18、TypeScript 5.9 | 同一架构；本地无需降版本 |
| 构建和样式 | Vite 7、Tailwind 4、Zustand 5 | Vite 8、Tailwind 4、Zustand 5 | 主体一致 |
| 图表和报告 | Recharts 3、React Markdown 10、GFM | 同类依赖与主要版本 | 已覆盖；差距在报告内容和交互 |
| Web 配套 | Axios、Motion、next-themes、React Compiler | fetch、现有主题 store、CSS 交互 | 实现选择；不要求全部换库 |
| 后端 | FastAPI、Pydantic、SQLAlchemy、SQLite | 同类框架与存储 | 主体一致；接口路径和目录名不必相同 |
| LLM | LiteLLM、OpenAI SDK，多供应商及回退 | 已有 LiteLLM、原生供应商、主备模型、多 Key、冷却、参数兼容、缓存和用量 | 已有能力不能重复列为缺失；协议和预算仍有差距 |
| 数据 | AkShare、Tushare、Pytdx、Baostock、TickFlow 等 | 已接入上述 A 股路径，并有腾讯、新浪、东方财富、efinance | 主要 A 股源已覆盖；不能以数量判断稳定性 |
| 桌面 | Electron 31、electron-builder 24、updater 6 | Electron 44、builder 26、updater 6 | 架构一致；本地版本较新 |
| 配置和调度 | 主要使用环境变量；有定时及 CLI 入口 | YAML 默认值、本地覆盖、`QUANT__` 环境变量覆盖、APScheduler | 本地设计可保留，不需要统一为 `.env` |

来源：[本地 Web 清单](../apps/web/package.json)、[上游 Web 清单](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/apps/dsa-web/package.json)、[本地 Python 依赖](../requirements.txt)、[上游 Python 依赖](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/requirements.txt)、[本地桌面清单](../apps/desktop/package.json)、[上游桌面清单](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/apps/dsa-desktop/package.json)。

依赖维护仍有一项具体差异：本地 `litellm>=1.80,<1.99` 没有同步上游明确排除的 `1.82.7/1.82.8`。本机实际安装为 **1.98.1**，本次未发现安装了被排除版本。应按部署 Python 版本维护经过验证的依赖约束和升级门禁；没有完整锁文件也是双方共同的工程边界，不能只算成本地独有缺口。Python CI 的语法与关键错误检查，本地也比上游简化。

## 数据源：优先处理的差距

### D01 · P1 · 采集失败还没有全部准确传递到任务与界面

已修复的实时行情路径会在所有源失败或写库失败时抛错，但其他路径未全部采用同样契约：

- [stock_data.py](../src/collectors/stock_data.py) 的 `_collect_limit_up_pool()`（905 行）在所有源无数据时直接返回，写库异常只记日志；`_collect_dragon_tiger()`（991 行）同样捕获异常后返回；`_collect_northbound_flow()`（1028 行）在全源失败或入库失败时也不向上层报错。
- [collector_orchestrator.py](../src/services/collector_orchestrator.py) 的 `collect_market_parallel()`（101 行）将没有抛异常的任务保留为 `ok`；缺失检查只识别 `error` 前缀。底层失败可能不会触发重试。
- [market.py](../api/v1/market.py) 的告警检查任务没有启用 `business_result_error`，而 `PipelineService.check_alerts()` 可以返回包含 `error` 的对象；任务终态仍可能显示完成。

需要统一返回 `available / empty / not_supported / stale / fetch_failed` 等状态，分别定义哪些是可接受空结果、哪些应重试。尤其要区分“当天确实无人上龙虎榜”和“接口失败”，不能简单地将所有零条结果视为失败。告警等单项任务应准确显示业务失败。

验收：全源异常、合法空表、无权限、写库异常各有离线用例；接口返回、任务终态、重试决策、数据源状态与界面提示一致。上游的分块状态和逐次来源记录可参考 [DataFetcherManager](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/data_provider/base.py)。

### D02 · P1 · 批量采集和基本面还缺少完整的阶段预算

个股诊断和问股已使用总期限及独立进程终止，收盘流水线也有外层进程保护，这些已经完成。剩余是 [CollectorOrchestrator](../src/services/collector_orchestrator.py) 的线程池等待与 [QuarterlyFundamentals](../src/collectors/quarterly_fundamentals.py) 的第三方接口调用，没有为每个数据阶段提供一致的总预算、并发上限和超时后资源占用控制。[source_chain.py](../src/collectors/source_chain.py) 直接调用来源函数，配置中的请求超时主要由适配器落实，不能推导出所有 AkShare 调用都受同一超时控制。

上游基本面管理器有阶段预算、单次预算、缓存容量限制及受限的超时工作槽；可参考 [base.py](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/data_provider/base.py)。这也不表示上游所有网络调用都有可强制终止的超时。

验收：模拟一个持续阻塞的供应商，后备源或降级结果在规定期限内返回；连续重试不会无限新增工作线程，取消后不会继续占用采集资源。

### D03 · P1 · 复权历史修订与后验价格口径仍需统一

本地已在日线保存来源与复权标签，选股特征和新版策略回测也会检查已知价格口径，不能再列为“完全没有复权处理”。但 [daily_history.py](../src/collectors/daily_history.py) 的补齐路径（350 行）只插入不存在的日期，不更新供应商因分红送转而修订的旧前复权价格。单一 `forward` 标签不能证明前后两次下载使用同一复权基准。

另外，[OutcomeEngine.price_path()](../src/services/outcome_engine.py)（31 行）只读取日期与收盘价，尚未复用选股回测的价格可比性检查，也没有在后验结果中记录输入价格版本和官方日历覆盖情况。混用旧前复权历史与新不复权行情时，方向命中率和技能权重仍可能受影响。

这是本地质量增补项，**不声称上游已彻底解决复权修订**。需要建立价格口径、修订时间或因子版本，安全刷新受影响区间，并将同一检查用于诊断、信号和技能后验。对已经冻结的结果采用新引擎版本重评，保留旧结果。

验收：分红送转、来源切换、历史重新下载和官方日历缺失场景；不能把口径跳变解释成真实价格收益。

### D04 · P2 · 财务策略的数据覆盖仍依赖先前个股诊断

当前季度财报、现金分红、金额和比例单位、缓存陈旧状态均已实现；选股也已批量读取缓存并拒绝后来取得的快照。实际限制是 [screener.py](../src/strategy/screener.py) 的 `_financial_payloads()`（452 行）只读取 `ResearchCache`，缺少选股候选的受控财务补全和覆盖率门槛。因此首次部署或未诊断过的股票，价值、成长和股息规则可能基本没有财务输入。

上游 [候选上下文采集](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/src/services/screening/candidate_context.py) 和 [基本面管理器](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/data_provider/base.py) 提供按候选取证的参考。应先对候选批次补全财务，展示可用率、缺失原因和来源，再允许财务策略参与排序。

进一步的历史财务快照、实际公告时间与修订版本需要另行建立。当前覆盖同一缓存键时会失去旧快照；“报告期在过去”也不等于“当时已公告”。目前避开后来缓存的做法有助于防止未来数据，但会让可回测的财务样本稀疏。不要把这项历史能力宣称为上游已完整具备。

### D05 · P2 · 缺少可用于排序的逐标的数据质量分数

当前已检查有限数值、OHLC、单位、日期、身份、覆盖数量与有效历史，部分结果不会覆盖最后一次成功批次。上游还将历史长度、OHLC 完整性、负成交量、陈旧缓存、失败回退等汇总为 `daily_quality_score/flags`，输入因子评分与风险扣分。[daily.py](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/src/services/screening/daily.py) 的 `_compute_daily_quality()` 是明确实现。

需要让每个候选携带质量分数和具体原因，区分整批缺数据与个别标的低质量；严重问题继续拒绝，轻度缺失可降权，界面可解释扣分来源。

### D06 · P2 · 数据源健康、缓存和诊断尚未形成同一服务契约

已完成配置化行情回退、付费来源适配、熔断、成功清错、来源字段和健康页面。当前 [SourceHealthRegistry](../src/collectors/source_chain.py) 与最后成功结果缓存仍是进程级对象；隔离进程内的健康记录没有统一汇回 API 服务。部分诊断确实成功取数后，主进程的来源页面仍可能没有对应记录。数据集的来源命名和配置入口也分散，行情之外仍有固定回退或直接调用路径。

上游 [管理器](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/data_provider/base.py)、[ProviderRun](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/src/services/run_diagnostics.py) 和 [快照缓存](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/src/services/screening/snapshot.py) 在能力路由、并发保护、回退、缓存及逐次诊断上更统一。应保留本地采集器，将公共契约抽出来，并传递缓存命中、数据年龄、记录数、实际来源和失败原因。上游也使用进程内缓存；持久化健康历史是本地稳定性目标的后续建设，不应虚构为上游已有保证。

### D07 · 待验证 · 所有来源的真实稳定性与付费权限

腾讯报价和日线在上一轮联网检查中可用；本次仅重新获取上游代码，没有重复进行所有数据源实测。TickFlow、Tushare 网关已经接入，但没有现成证据证明当前部署的 Key、额度和相应接口权限可用。

后续验收应包括全市场覆盖、沪深北及 ST 样本、盘中与盘后时点、额度耗尽、错误单位、接口字段变化、主源失败后的实际切换，以及连续多日的成功率和延迟。当前联网检查已存在，但只能证明某次检查结果。上游 [README](https://github.com/ZhuLinsen/daily_stock_analysis) 也明确提醒免费行情源可能受限；增加付费源不等于已完成稳定性验收。

## 策略：缺口在完整排序流程

参考：[上游 pipeline](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/src/services/screening/pipeline.py)、[scorer](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/src/services/screening/scorer.py)、[ranker](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/src/services/screening/ranker.py)、[risk](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/src/services/screening/risk.py)。

| 编号 / 优先级 | 当前本地 | 尚未对齐的行为 | 可验收结果 |
| --- | --- | --- | --- |
| S01 / P2 | 六个短线评分规则及五个 YAML 条件规则；合并分为最高策略分加多策略奖励 | 上游的多因子评分、分策略权重与 scoring profile。当前 YAML 命中后使用固定分数，不等于完整价值、质量、低波或均衡多因子模型 | 拆出估值、动量、活跃度、流动性、稳定性等分量；每项权重和贡献可解释；规则和评分可独立配置 |
| S02 / P2 | 选股读取本地行情、主题和财务缓存；事件风险读取本地新闻标题；盘前预测另行使用候选 | 上游候选新闻、公告、资金流取证 → LLM 比较重排 → 后分析的完整链路 | 候选上下文有固定预算和证据；LLM 失败回退原排序；不能添加原候选外的股票；保留每层分数与排序理由 |
| S03 / P1 | 全市场规则与诊断护栏已有；事件风险追加到理由，但不独立改变选股排序 | 上游有独立风险扣分及可配置高风险否决，包括追涨、异常量能、换手、估值、低质量输入等 | 风险计算独立于模型结论；严重风险可否决；风险分、扣分和最终排序可追溯，缺失指标不能默认无风险 |
| S04 / P2 | 多策略去重、市场环境适配、有限输出；持仓风险另有模块 | 最终候选的行业/主题集中度限制与风险桶惩罚，没有完整进入选股排序 | 一次选股不会被相同风险桶占满；阈值配置化；展示被降权或替补的原因 |
| S05 / P2 | 新版策略回测已明确为次日开盘入场、T+1、固定交易日、不可入场计数、版本和样本门槛 | 上游报告级评估包含更丰富的模拟退出和分组指标；本地仍有观察回测、诊断后验和旧回测模块等多个口径。诊断已有止损止盈先到统计，不能算作完全缺失 | 区分信号预测准确率、样本收益、模拟成交和实际组合；统一说明基准、区间风险及目标触发；旧引擎明确标旧，避免混用 |

本地代码入口：[screener.py](../src/strategy/screener.py) 的 `_rank()`（413 行）、[screening_rules.py](../src/strategy/screening_rules.py)、[premarket_predictor.py](../src/services/premarket_predictor.py)、[strategy_backtest.py](../src/strategy/strategy_backtest.py)、[diagnosis_outcome.py](../src/services/diagnosis_outcome.py)。

上游的 `balanced_alpha / momentum_quality / low_volatility_quality / quality_value / capital_heat / blue_chip_income / dual_low` 等采用更丰富的过滤、评分、风险和组合配置。本地已有相近名称的策略不能视为同一算法，也不应为了凑数量复制名称。

前面补好的价格可比性检查、固定交易日、样本门槛、策略签名及官方日历权重门槛应保留。当前样本复利和回撤不是共享资金组合净值，手续费、滑点、退出成交受限也没有完整建模；上游报告模拟同样不能证明实际成交或盈利。若迁移 AlphaSift 衍生代码，应保留上游 Apache-2.0 的来源和许可声明。

## UI 与交互：页面已有，仍有证据错位和信息深度差距

### U01 · P1 · 历史报告与研究概览可能指向不同分析

[WorkspacePage.tsx](../apps/web/src/pages/WorkspacePage.tsx) 的 `ReportPanel()`（25 行）会按 `recordId` 拉取历史诊断；但研究标签（39 行）只向 `ResearchOverview` 传入股票代码。[StockPage.tsx](../apps/web/src/pages/StockPage.tsx) 的 `ResearchOverview()`（134 行）再读取最新 profile；[stock_profile.py](../src/services/stock_profile.py) 的 `_research()` 始终读取最新诊断。

结果：选择旧报告后，诊断标签可显示旧结论，研究标签却显示后来报告的结论和证据。应让研究产物绑定 `source_report_id`，在历史上下文里按同一报告读取；最新行情、持仓和监控可另行标明实时日期。

验收：同一股票两份结论相反的报告，切换时研究结论、证据、失效条件、导出和报告日期一致；新分析完成后更新对应报告。

### U02–U05 · P2 · 上游仍更完整的界面能力

| 编号 | 当前已有 | 仍需补齐 |
| --- | --- | --- |
| U02 · 运行流程 | 全局任务中心、刷新恢复、SSE、运行步骤、模型和耗时 | 上游 [RunFlowGraph](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/apps/dsa-web/src/components/run-flow/RunFlowGraph.tsx) 将取数、缓存、模型回退、保存、推送组织成可展开流程；本地仍是简化步骤列表，部分来源切换无完整记录 |
| U03 · 选股工作区 | 策略筛选、历史日期、运行与回测、最新运行说明、数据不完整提示 | 上游 [选股页](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/apps/dsa-web/src/pages/StockScreeningPage.tsx) 展示候选多维分数、风险、来源降级、热点、过滤解释及深度分析入口；本地还需要与 S01–S04 同步补字段 |
| U04 · 告警审计 | 规则编辑/试算、记录、设置；后端已保存逐渠道结果 | 上游 [告警页](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/apps/dsa-web/src/pages/AlertsPage.tsx) 有分页、规则筛选、逐标的试算状态和通知尝试记录。本地 API 返回的 `channels` 尚未纳入 `AlertRow` 类型和页面展示，不能在页面判断哪个渠道失败及为何降噪 |
| U05 · 启动和加载 | 页面错误边界、可切换主题、窄屏布局已经存在 | [App.tsx](../apps/web/src/App.tsx) 的认证状态请求失败时直接采用“认证关闭”的前端默认状态，掩盖连接故障；应显示明确错误与重试。所有页面仍同步导入，缺少上游懒加载。此项不意味着后端认证可被绕过 |

整体 UI 已有首页研究工作台、自选/今日/近期报告、诊断与研究概览、历史批量选择、股票范围问股、行情源优先级和配置编辑，不能再列为从零缺失。还有现有的错误边界和主题 store，不能因未采用上游 `next-themes` 就认为没有主题或页面容错。

视觉上不要求逐像素复制品牌、图标、字体和所有动画。下一步更有价值的是先让历史报告、风险来源与数据状态对应正确，再补齐密度、响应式交互及浏览器验收。

## Agent、研究协议与告警领域

| 编号 / 优先级 | 当前已有 | 差距与完成条件 |
| --- | --- | --- |
| A01 / P1 · 告警身份 | 规则表、持久化冷却、事件及逐渠道结果 | `_sync_rules()` 使用显式 ID 或规则哈希，`_rule_events()` 没有显式 ID 时使用列表序号；同一规则身份不一致。必须在配置保存、运行、冷却、记录与 UI 中统一稳定 ID；重排及停用其他规则不能改变当前规则身份 |
| A02 / P2 · 策略审议 | 有效观点校验、支持/反对、冲突、少数意见和低信心护栏 | [strategy_synthesis.py](../src/services/strategy_synthesis.py) 采用一次确定性审议，不调用模型回应冲突。上游 [deliberation.py](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/src/agent/skills/deliberation.py) 有 LLM 审议和多轮受限修订；如启用，应限制轮次与预算，并保留原观点与修订理由 |
| A03 / P2 · 统一研究事实 | ContextPack、ResearchArtifact、字段状态、来源、失效条件和后验已经实现 | ContextPack 目前主要为诊断上下文包装，许多内部消费者仍读取文本段或旧字典，深度研究仍有独立证据簿。需要让选股、问股、深度研究和报告消费同一事实契约。上游字段有更严格的时间与 metadata 验证，研究产物还有目标价复核、新闻不足及数据质量变化等失效条件 |
| A04 / P2 · 问股上下文与协议 | 三轮工具调用、每轮调用上限、股票范围、取消、历史、主备模型、用量 | [stock_chat.py](../src/services/stock_chat.py)（493 行）按字符截断、摘取旧回答；上游 [chat_context.py](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/src/agent/chat_context.py) 按模型估算 tokens、摘要并保留消息锚点，还有 [provider_trace.py](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/src/agent/provider_trace.py) 的供应商工具协议回传。本地 JSON 文本工具协议易移植，但没有等价的原生协议轨迹能力 |

A01 的离线复现只构造规则和报价，没有访问网络、数据库或发送通知：同一规则的持久化哈希为 `8a2afef15edc580cdd07e60a`，事件 ID 为 `0`，反转两条规则的顺序后事件 ID 变为 `1`。对应 [alert_service.py](../src/services/alert_service.py) 的 337、516 行，属于当前仍待修复的问题。

A03 参考：[上游 ContextPack](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/src/schemas/analysis_context_pack.py)、[上游研究产物服务](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/src/services/research_artifact_service.py)；本地 [research.py](../src/schemas/research.py)、[research_artifact.py](../src/services/research_artifact.py)、[深度研究服务](../src/services/research.py)。

## 测试、CI 与部署

| 编号 / 优先级 | 判断 | 后续验收 |
| --- | --- | --- |
| O01 / P2 · 浏览器回归 | 本地已有大量 Vitest/Testing Library 测试和人工浏览器截图，上游另有 Playwright 页面冒烟与报告/市场结构测试；本地没有同等的 Web 端到端测试入口 | 使用隔离模拟服务，覆盖登录失败重试、历史报告切换、任务刷新恢复、数据源降级、窄屏与导出 |
| O02 / P2 · 构建门禁 | 本地已有后端分片、Web lint/test/build、桌面测试、发行构建及安装包自检。上游 PR CI 还有 Docker 构建与导入冒烟、Python 语法与关键错误、AI 资产检查 | 本地 PR 增加容器启动和默认规则加载检查；验证实际容器和打包服务，不只验证源代码。源码级部署回归已在本次补充 |
| O03 / 待验证 · 运行与发行 | 双架构 Docker、macOS 双架构和签名审计已补齐；双方 macOS 清单都未启用完整签名/公证配置 | 不把 Developer ID 公证缺失列为本地独有差距。源检查工作流本地有 `ENABLE_NETWORK_SMOKE` 开关，需要在真实仓库确认启用和查看运行结果；不能因存在工作流文件就认定已持续执行 |

参考：[上游 CI](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/.github/workflows/ci.yml)、[上游 Playwright 测试](https://github.com/ZhuLinsen/daily_stock_analysis/tree/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/apps/dsa-web/e2e)、[本地 CI](../.github/workflows/ci.yml)、[本地发行](../.github/workflows/release.yml)、[本地源检查](../.github/workflows/network-smoke.yml)。

### 本次已经修复：默认 YAML 策略没有完整进入部署环境

发现的实际行为：Docker 镜像没有复制 `screening_rules.yaml.example`，入口也不会复制它；桌面打包脚本已打入该文件，但 `prepare_workdir()` 没有将其放入用户目录。`load_rules()` 在正式规则与示例都缺失时返回空列表，因此这两种新部署会少加载五个 YAML 策略。

本次修改：

- [Dockerfile](../docker/Dockerfile) 带入规则示例，[entrypoint.sh](../docker/entrypoint.sh) 启动时更新示例。
- [server.py](../server.py) 在准备桌面用户目录时同步示例。
- 用户的正式 `screening_rules.yaml` 保留，升级只更新默认示例；缺少正式规则时继续使用示例。
- [ci_changes.py](../scripts/ci_changes.py) 将仅修改 `docker/` 的变更也纳入后端部署回归。
- [test_deployment_defaults.py](../tests/test_deployment_defaults.py) 实际构造桌面目录及按 Docker COPY 清单组装的默认目录，再运行配置初始化、加载五个规则，并验证升级不覆盖自定义规则。

本次验证：

```text
.venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_deployment_defaults.py tests/test_server.py \
  tests/test_research_alignment.py::test_ci_changes_route_relevant_paths
8 passed，4 个现有第三方弃用警告
```

这次没有重新构建 Docker 镜像或桌面安装包，也没有重跑全量前后端测试；本次未修改 Web 代码。此前全量验证为后端 1887 通过、前端 177 通过及 lint/build 通过，详见[上一轮稳定性验收](data_strategy_reliability.md)，不要把它记为本次新验证。

## 暂不纳入的差异与已有优势

港、美、日、韩、台股、相应 YFinance/Longbridge/Futu 路径、交易日历与多币种组合，以及 Codex/Claude Code/OpenCode 的本地 CLI 生成后端，继续按已确认范围排除。新增海外源不能代替现有 A 股源的可靠性建设。

通知和机器人方面，本地已有 14 个通知渠道、降噪、路由、图片与文字回退、Telegram 话题、多个聊天机器人。Slack 等具体路径仍主要是 webhook，与上游部分 App Bot/文件上传能力不同；这些扩展继续放在后续，不作为当前 A 股分析的首要缺口。通知记录在页面中的可追溯性则已列入 U04。

本地对 A 股主线、涨停/连板、盘前预测、模拟执行、券商记账及自学习有较多专门功能。应保留这些能力；上游通用研究架构可以补足数据和研究质量，不需要替换为完全相同的产品。

## 建议落地顺序

1. **数据结果可信**：D01 失败与空数据状态 → D02 阶段预算 → D03 价格口径及修订；同时处理 A01 告警身份和 U01 历史证据错位。
2. **策略输入充分**：D04 候选财务覆盖 → D05 逐标的数据质量 → D06 统一来源追踪；完成 D07 真实来源验证。
3. **策略决策可解释**：S01 因子分量 → S03 独立风险 → S04 集中度 → S02 候选取证与可回退的 LLM 重排；保持现有 T+1 与样本门槛。
4. **界面与验收闭环**：U02–U05 展示对应事实和失败原因，补 O01 浏览器回归及 O02 容器门禁；之后再深化 A02–A04。

每项完成以具体行为和回归证据为准，不以复制模块、添加供应商或增加策略名称作为完成标准。
