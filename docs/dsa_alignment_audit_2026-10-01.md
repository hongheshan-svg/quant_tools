# daily_stock_analysis 对齐审计（2026-10-01）

> 本文记录修改前的源码审计。用户随后授权直接修改并确认聚焦 A 股；实际落地内容见[对齐实施与验收](dsa_alignment_implementation.md)，当前剩余差异以[2026-10-02 全面复核](dsa_alignment_review_2026-10-02.md)为准。

## 审计基线与结论

参考仓库是 [ZhuLinsen/daily_stock_analysis](https://github.com/ZhuLinsen/daily_stock_analysis)，固定到 [`be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f`](https://github.com/ZhuLinsen/daily_stock_analysis/commit/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f)，提交时间为 2026-10-01 15:32:09 +08:00，提交说明为 `fix: recognize current Sina financial metric labels (#2373)`。

本地基线为 `512d14e`，同时纳入审计开始时已有的未提交改动：`api/v1/stocks.py`、`src/services/stock_profile.py`、`tests/test_stock_profile.py`。这些改动没有被修改。

本仓库已覆盖大部分 A 股工作流程。剩余差距主要是：**输入和输出契约、错误与超时语义、证据追溯、策略观点评估，以及同名功能的深度**。港美日韩台股、本地 CLI 模型后端、外币组合属于新增产品范围，不能和现有 A 股功能的正确性问题放在同一个优先级。

本文按源码行为比较，不按文件名、页面数或依赖版本计算“对齐百分比”。两边的 API 路径、目录结构、配置格式不同，本身不构成功能缺口。上游的文档有些段落记录历史阶段，因此以固定提交的代码为准。

优先级含义：

- **P1**：优先处理现有功能的正确性、失败处理或访问保护边界。
- **P2**：补齐 A 股研究工作流的功能深度和可追溯性。
- **P3**：按产品方向选择的范围扩展，不建议为了形式一致全部移植。

## 已有能力：不应再列成缺失

| 领域 | 本地已实现的能力 | 仍需区分的边界 |
| --- | --- | --- |
| 个股诊断 | 技术/情报/风险分析员、决策员、数据完整度、阶段护栏、归因、三种决策风格、事后校准 | 不等同于上游完整 Agent 工具循环、ContextPack 和策略审议 |
| 搜索 | Bocha、Tavily、SerpAPI、Brave、SearXNG、Anspire、MiniMax，多 Key 与相关性过滤 | 缺口集中在统一证据状态和调用诊断，不是搜索平台数量 |
| 自选股 | 搜索、简称/拼音、文本和 CSV/Excel 导入、图片/剪贴板识别、每日仪表盘、逐只推送、邮件分组、部分完成报告 | 图片识别目前限定 A 股，见范围扩展项 |
| ETF/指数 | 独立数据表、搜索、K 线、诊断、问股、机器人、自选股、决策信号、指数身份隔离 | 国内指数已扩充；不得把“上游有指数”再次算作缺失 |
| 决策信号 | 生命周期、价格计划、有效期、手动关闭/作废、反馈、1/3/5 日收益、历史复盘 | 后验引擎版本和独立结果记录仍有差异 |
| 全市场选股 | 六种短线策略、环境适配、结果去重、历史、次日表现、历史回测、回测权重 | 与上游 AlphaSift 风格的选股引擎覆盖范围不同 |
| 盘中告警 | 涨停/炸板、止损止盈、价格/涨跌/量能/均线/MACD/KDJ/RSI、冷却、免打扰、日报 | 持久化规则、冷却和逐渠道尝试记录仍有差异 |
| 实盘记账 | 多账户、交易/券商文件导入、预览、分红/送转、出入金、组合风险 | 当前为人民币 A 股记账，未覆盖多币种组合 |
| 通知 | 14 个渠道、路由、长文本分片、配置诊断、测试、图片和文字回退、Telegram 话题与反代 | 某些渠道的 App Bot/文件能力尚未对齐 |
| 运行方式 | FastAPI + React、Electron、Docker、Actions、CLI、首次配置、备份、模型发现/测试、用量 | 通用任务与运行诊断比上游简化 |
| 调度与发行 | 收盘任务独立子进程、超时终止进程树、系统错误通知、桌面自动更新、打包自检、Docker 双架构 | 调度热更新、macOS 双架构和签名审计见后文 |

这些判断来自本地 [README](../README.md)、[API](../api/v1/)、[服务](../src/services/)、[前端](../apps/web/src/pages/) 和 [工作流](../.github/workflows/)，并逐项与上游实现交叉核对；“已有”不表示与上游全部字段完全相同。

## P1：现有功能先补的六项

### 01. 统一股票身份解析，并拒绝交易所冲突

本地 [stock_code.py](../src/utils/stock_code.py) 的 `bare_code()`（17 行）只去除前缀，`code_candidates()`（41 行）会为同一裸码展开三个交易所。告警另外自行剥离后缀，股票搜索、指数、诊断和 profile 各有解析入口。

离线调用得到：

```text
SH600519  -> 600519
600519.SH -> 600519.sh
SZ600519  -> 600519
600519.SZ -> 600519.sz
code_candidates(SH600519) -> sh600519, 600519, sz600519, bj600519
```

因此，合法后缀和错误交易所前缀得到不同待遇；错误前缀还可能被静默剥离。本地 profile 也沿用这套行为。

上游集中解析 canonical identity，并对显式交易所冲突返回错误，且把已解析身份传到历史、资讯和持仓读取：[身份工具](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/src/services/stock_code_utils.py)、[profile 契约](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/docs/stock-profile-api.md)。

建议先统一 A 股/ETF/指数的单一解析契约，保留指数和个股身份差异；暂不扩展海外市场。验收应覆盖前后缀等价、冲突返回 400、旧数据别名兼容、指数与同形个股不混查。

### 02. 无效策略观点不能参与共识

本地 [skill_consult.py](../src/services/skill_consult.py) 的 `_normalize()`（114 行）会把未知 `stance` 改成“中性”，但保留原始评分和信心；`consensus()`（150 行）继续用该评分计算看多/看空。单个观点也会显示“一致”。[diagnosis_agents.py](../src/services/diagnosis_agents.py) 的观点归一同样会把未知方向回退成中性。

已复现：`stance=UNRECOGNIZED, score=95, confidence=高` 被接受，单独输入共识后得到 `看多、95 分、一致`。这里的“中性”方向和看多共识甚至互相矛盾。

上游明确将 Valid Opinion 和 Diagnostics 分开，合法观点不足两条时共识为 `insufficient`，无效观点不能混入加权和阵营：[协议](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/src/agent/protocols.py)、[合成器](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/src/agent/skills/synthesis.py)。

建议先修有效性判定、有限数值校验、无效观点隔离和共识门槛，再考虑更复杂的多轮审议。

### 03. 运行记录和任务错误缺少统一脱敏

本地 [run_log.py](../src/services/run_log.py) 的 `step()`（38 行）把异常原文写入 `detail`，`to_dict()` 原样返回；[api/tasks.py](../api/tasks.py) 的任务错误也直接截断 `str(e)`。诊断历史会展示运行记录。

使用完全虚构的异常 `api_key=SYNTHETIC_DEMO_ONLY`，结果中仍出现该完整文本。这个探针没有读取真实密钥，但证明“限制长度”不能代替脱敏。

上游在公开诊断文本和 metadata 边界执行统一脱敏：[run_diagnostics.py](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/src/services/run_diagnostics.py#L86)。

建议在运行记录、任务错误、数据源错误等公开出口统一处理 key/token/password/Bearer/webhook 和本地路径；用虚构凭据补回归用例。

### 04. 每日任务隔离已做，但手动诊断/问股缺少总预算

本地 [scheduler.py](../src/scheduler.py) 已有子进程总超时，不能再列为缺失。不过，[diagnosis_agents.py](../src/services/diagnosis_agents.py) 和 [skill_consult.py](../src/services/skill_consult.py) 使用 `ThreadPoolExecutor.map()` 等待所有观点；[stock_chat.py](../src/services/stock_chat.py) 只有工具轮数上限和调用间取消检查。模型请求有超时，但基本面/工具调用和整条手动分析没有统一剩余预算。

上游有整条流水线预算、各分析员预算、工具执行控制，并区分“已超时”和“预算不足跳过下一阶段”：[orchestrator.py](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/src/agent/orchestrator.py#L183)、[工具执行](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/src/agent/tools/execution.py)、[流事件](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/docs/agent-stream-events.md)。

建议把 deadline 向取数、分析员、策略和决策阶段透传，保留已完成证据；取消或超时时让前端获得明确原因。线程无法强制终止的调用，需要可终止的隔离方式。

### 05. 登录失败限流尚未对齐

本地 [登录接口](../api/v1/system.py)（47 行）每次直接校验密码，没有失败次数/时间窗/IP 限流；[认证存储](../api/auth.py) 提供密码哈希与 Cookie 验证。复核原始代码后更正：关闭认证时没有验证当前密码，该缺口也需要修复。

上游登录和认证设置调用 `check_rate_limit()`，过多失败返回 429：[auth.py](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/api/v1/endpoints/auth.py#L367)。

建议补统一的失败节流及成功清除语义，重点覆盖开启登录并允许局域网访问的部署方式。本次没有运行真实密码尝试。

### 06. 业务失败仍可能被后台任务标成 done

本地 [PipelineService.diagnose_stock](../src/services/pipeline_service.py)（211 行）会将异常转换为 `{code, error}`；[TaskManager._run](../api/tasks.py)（66 行）只要函数正常返回就标为 `done`。

已复现：函数返回 `{"code":"600519","error":"行情库中没有该股票的数据"}` 时，任务返回 `status=done`，顶层 `error` 为空。[useTask.ts](../apps/web/src/hooks/useTask.ts) 已识别结果内的 `error` 并提示错误，所以不能说所有页面都会弹成功；但任务状态与 API 客户端语义仍不一致。

上游分析服务/队列对无分析结果走失败分支，CLI 的 no-report 情况也有失败回归：[task_queue.py](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/src/services/task_queue.py#L835)、[no-report 回归](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/tests/test_main_portfolio.py)。

建议由业务任务声明成功/失败的结果契约，或在业务边界抛出类型化错误；避免简单对所有通用字典一律检查 `error` 字段。验收需同时覆盖任务中心、API 和部分成功的批量报告。

## P2：已有功能的深度差异

下列条目均是源码可见差异；建议逐项实现，不建议把整个上游目录直接移入。

| 编号 | 领域 | 本地现状与尚未对齐的内容 | 上游证据 |
| --- | --- | --- | --- |
| 07 | 分析上下文包 | [stock_diagnosis.py](../src/services/stock_diagnosis.py) 用文本段落和块权重计算完整度；[market_context.py](../src/services/market_context.py) 与题材模块已有市场信息，但没有统一版本、字段级 `source/as_of/status`、`fetch_failed/estimated/fallback/stale/not_supported` 状态以及可复用的安全快照。需要让诊断、历史、告警和研究消费同一证据包，市场结构也应有来源与质量字段。 | [ContextPack schema](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/src/schemas/analysis_context_pack.py)、[builder](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/src/services/analysis_context_builder.py)、[市场结构](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/src/schemas/market_structure.py) |
| 08 | 结构化研究产物 | 本地 [research.py](../src/services/research.py) 有主题拆解、证据编号、Markdown 和存储，诊断也有价格计划与失效条件；但没有统一 `ResearchArtifact`，无法直接复用 `thesis/evidence/invalidation_conditions/next_actions/data_quality`。上游已实现 schema/helper，并在 profile 组装结构化产物；不能把文档中的后续持久化计划误写成全面落地。 | [schema](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/api/v1/schemas/research_artifact.py)、[builder](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/src/services/research_artifact_service.py)、[profile 接线](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/src/services/stock_profile_service.py#L116) |
| 09 | 个股 profile | 未提交的 [stock_profile.py](../src/services/stock_profile.py) 已有分块隔离和质量汇总，返回 `quote/research/signals/portfolio/monitors`；缺少 `history`、`intelligence`、`history_days` 参数、近期报告列表和 `structured_report`。quote 为本地日线摘要，上游复用报价服务；portfolio 本地重放账本，上游明确只读缓存身份。需确定自己的延迟与缓存语义，不能只复用端点名称。 | [service](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/src/services/stock_profile_service.py)、[API 文档](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/docs/stock-profile-api.md) |
| 10 | 多策略审议 | [skill_consult.py](../src/services/skill_consult.py) 并发收集观点、加权评分并向决策员追加文本；没有 canonical 的支持/反对阵营、冲突强度、审议轮次、少数观点保留和唯一权威 `strategy_synthesis`。先完成 02，再按需求增加审议，避免输出面各自重新合成。 | [synthesis](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/src/agent/skills/synthesis.py)、[deliberation](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/src/agent/skills/deliberation.py)、[契约](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/docs/multi-strategy-contract.md) |
| 11 | 策略观点后验与权重 | 本地 [SkillOpinionService](../src/services/skill_consult.py) 只评估 5 日涨跌，直接按观点行累计样本，20 个样本即可按命中率线性调整至 0.8–1.2；没有独立样本归因、评估版本、多 horizon、unable 结果。上游为版本化不可变样本/结果，按独立已评估样本的 30 条门槛、Beta 先验和有界权重计算。本地探针显示 20 条全命中即可达到 1.2。 | [sample service](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/src/services/skill_opinion_sample_service.py)、[performance](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/src/services/skill_opinion_performance_service.py)、[weights](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/src/services/skill_opinion_weight_service.py) |
| 12 | 决策信号后验契约 | 本地 [decision_signals.py](../src/services/decision_signals.py) 在信号行更新收益/状态/反馈；上游把 `signal_id + horizon + engine_version` 的结果独立记录，并明确输入数据质量、无法评估原因和分组统计。建议保留现有生命周期，新增可重跑、不覆盖不同版本的结果层，而不是改掉 closed/replaced 等既有状态。 | [outcome service](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/src/services/decision_signal_outcome_service.py)、[data quality](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/src/services/decision_signal_data_quality.py)、[存储](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/src/storage.py) |
| 13 | 基本面深度 | [fundamentals.py](../src/collectors/fundamentals.py) 当前主线是筹码估算、业绩预告/快报；行情有 PE/PB，[shareholders.py](../src/collectors/shareholders.py) 有股东/机构持仓。尚缺季度财报与成长盈利指标、分红事件/TTM 分红收益率，以及分块状态、来源、阶段预算和 fallback。实盘录入分红不等于研究基本面分红数据。 | [fundamental_adapter.py](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/data_provider/fundamental_adapter.py)、[基本面编排](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/data_provider/base.py#L4196) |
| 14 | 问股上下文与工具控制 | [stock_chat.py](../src/services/stock_chat.py) 保留最近 6 轮、每条截取 800 字，工具由提示词 JSON 调用，本轮工具按循环顺序执行；没有 token 预算/滚动摘要、显式 `stock_context` 和工具标的范围护栏、稳定调用去重缓存。Web 单选分析视角，多策略会诊另走诊断流程；上游 Chat 请求支持多 skills。 | [chat context](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/src/agent/chat_context.py)、[stock scope](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/src/agent/stock_scope.py)、[工具 guard/cache](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/src/agent/tools/execution.py)、[Chat API](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/api/v1/endpoints/agent.py) |
| 15 | 任务流与运行诊断 | [TaskManager](../api/tasks.py) 提供去重和轮询；[RunLog](../src/services/run_log.py) 有步骤耗时。缺少通用任务 SSE、trace/query/task/report 关联、逐 provider attempt/fallback/retry、保存与通知节点、运行流 API/视图。服务重启后本地 task ID 失效；上游状态接口可通过 query_id 回读已完成历史，但双方通用队列仍在内存，不能声称上游能恢复所有未完成任务。 | [task SSE/status](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/api/v1/endpoints/analysis.py)、[run diagnostics](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/src/services/run_diagnostics.py)、[run flow](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/src/services/run_flow.py) |
| 16 | 选股引擎覆盖 | [screener.py](../src/strategy/screener.py) 为六种硬编码短线规则，读取本地全市场日线；上游还有 YAML 策略加载、价值/质量/红利/低波等策略、热点列表/详情、候选公告/新闻/资金流上下文、后置分析与重排、last-good 缓存、运行/数据源历史。建议保留本地打板策略，优先补事件风险、源历史和候选到指定策略诊断的交接，再选是否新增长线策略。 | [screening modules](https://github.com/ZhuLinsen/daily_stock_analysis/tree/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/src/services/screening)、[service](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/src/services/screening_service.py)、[说明](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/docs/screening-engine.md) |
| 17 | 告警持久化与可观测性 | 本地 [alert_service.py](../src/services/alert_service.py) 规则来自配置，冷却/交叉状态在内存，`AlertRecord` 记录总体 notified；上游有数据库规则、触发记录、逐渠道通知尝试及持久化冷却依据。需补失败/跳过/降级原因和重启后的去重口径。这里的差异是告警中心；普通通知降噪双方仍有进程内状态。 | [alert service](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/src/services/alert_service.py)、[worker](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/src/services/alert_worker.py)、[API](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/api/v1/endpoints/alerts.py) |
| 18 | 资讯源管理与 scope | 本地 [rss.py](../src/collectors/rss.py) 已支持 RSS/Atom/NewsNow、模板、测试、定时采集和去重，统一写 `FinanceNews`；源设置整体写入 YAML。上游还有源与条目的独立持久化、symbol/market/sector scope、单源拉取、按 scope 查询条目和来源元数据。profile 的 intelligence 块依赖这部分。 | [intelligence service](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/src/services/intelligence_service.py)、[API](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/api/v1/endpoints/intelligence.py) |
| 19 | 回测口径与结果诊断 | 本地已有 [信号绩效](../src/services/signal_performance.py)、[诊断后验](../src/services/diagnosis_outcome.py)、[策略回测](../src/strategy/strategy_backtest.py) 和交易回测；不能算“没有回测”。上游对历史 AI 报告有配置化评估窗口、中性带、评估版本、持久化结果和不足日线/无新结果诊断。建议统一这些口径与 11/12，避免三套收益评估逐渐漂移。 | [BacktestService](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/src/services/backtest_service.py)、[EvaluationConfig](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/src/core/backtest_engine.py)、[Web](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/apps/dsa-web/src/pages/BacktestPage.tsx) |
| 20 | 设置 Schema 与调度热更新 | [system.py](../api/v1/system.py) 有各分区设置和校验，SchedulerPanel 只能查看/立即运行；[scheduler.py](../src/scheduler.py) 启动时注册触发规则。尚缺公共字段 Schema、Web 编辑调度时间及运行中更新触发规则。上游 config registry/schema 与 schedule_times_provider 支持刷新配置。不要因此改掉本地 YAML/QUANT__ 体系。 | [config registry](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/src/core/config_registry.py)、[config API](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/api/v1/endpoints/system_config.py)、[scheduler refresh](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/src/scheduler.py#L252) |
| 21 | Web 操作闭环 | 本地 [HistoryPage](../apps/web/src/pages/HistoryPage.tsx) 支持单条删除、导出/复制；缺少批量选中删除和按代码清理。自选股全量仪表盘已有，选中子集的批量分析交互不等价；[StockPage](../apps/web/src/pages/StockPage.tsx) 还未消费 profile，但上游 profile 文档同样明确 Web 接线是后续阶段，不能把完整个股研究工作台算成上游已实现。分享图已有下载，尚无 Web Share 文件分享和用户激活时序处理。 | [history API](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/api/v1/endpoints/history.py)、[watchlist workspace](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/apps/dsa-web/src/components/watchlist/HomeStockWorkspace.tsx)、[ShareImageButton](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/apps/dsa-web/src/components/report/ShareImageButton.tsx) |
| 22 | 评估与维护文档 | 本地有较多离线回归，尚无 Agent golden 轨迹评估，无法持续测工具命中、重复调用、失败/重试、阶段覆盖和预算消耗。本次检查前没有 docs 专题目录，主要说明集中在 README；README 的指数数量、搜索源名单已落后于实现。建议补契约文档和针对行为的评估；回归测试通过不等于真实 LLM 的研究质量已达标。 | [evals](https://github.com/ZhuLinsen/daily_stock_analysis/tree/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/evals/agent_trajectory)、[轨迹指标](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/docs/agent-trajectory-eval.md)、[文档索引](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/docs/INDEX.md) |
| 23 | macOS 发行与 CI | 本地 [release.yml](../.github/workflows/release.yml) 有 Windows/macOS/Linux、原生双架构 Docker、后台原生库/资源自检；这些已覆盖。尚未显式发布 macOS x64/arm64 两套安装包，也没有上游的 afterPack 签名归一/审计。上游 CI 有路径分流、后端三分片和专门门禁，本地仍是单后端作业；这些是运行效率/发行覆盖差异，依赖版本更高不应被当成未对齐。 | [desktop release](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/.github/workflows/desktop-release.yml)、[afterPack](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/apps/dsa-desktop/scripts/afterPackMacos.js)、[CI](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/.github/workflows/ci.yml) |

## P3：按产品范围选择的六项

| 编号 | 领域 | 未对齐内容及取舍 | 上游证据 |
| --- | --- | --- | --- |
| 24 | 本地模型运行后端 | 本地 [llm_client.py](../src/analyzers/llm_client.py) 为 LiteLLM/OpenAI SDK + primary/backup/vision，已支持 Ollama；尚无 `codex_cli`、`claude_code_cli`、`opencode_cli` 生成后端、Hermes 本地 HTTP 接入约定、`codex_app_server` Agent 后端及其 status/preview/smoke-test。上游明确区分生成后端与 Agent 后端，CLI 文本生成不等于工具 Agent。多渠道路由/API surface/provider prompt-cache hint 也比本地配置丰富。适合单列为一个扩展项目。 | [generation backend registry](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/src/llm/backend_registry.py)、[Agent backend](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/src/agent/agent_backend.py)、[Hermes](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/src/llm/hermes.py) |
| 25 | 多市场个股 | 本仓库定位 A 股；[US earnings](../src/collectors/us_earnings.py) 是国际背景，不能据此认为支持美股个股研究。缺港/美/日/韩/台股的标的路由、报价/日线/基本面、市场日历/时区/币种/Prompt、导入与报告贯通。上游日韩台仍有 suffix-only、列表覆盖和市场宽度限制，不能承诺全部市场全能力。 | [市场边界](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/docs/market-support.md)、[YfinanceFetcher](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/data_provider/yfinance_fetcher.py)、[交易日历](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/src/core/trading_calendar.py) |
| 26 | 可选行情供应商 | A 股免费多源回退已齐；缺 TickFlow、Longbridge、Futu、AlphaVantage/Finnhub 等路径。本地 Tushare 仅 token + `pro_api(token)`，没有上游的自定义兼容 HTTP 网关；YFinance 尚未接入个股主链路。按实际稳定性/市场需求选择，不能以供应商数量判断当前 A 股链路优劣。 | [data_provider](https://github.com/ZhuLinsen/daily_stock_analysis/tree/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/data_provider)、[Tushare](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/data_provider/tushare_fetcher.py) |
| 27 | 海外持仓与汇率 | 本地 [real_portfolio.py](../src/services/real_portfolio.py) 已有多账户、费用、公司行为与出入金；缺 market/currency/base_currency、汇率刷新/陈旧状态、多币种估值汇总，以及 Futu OpenD 只读持仓用于分析列表。与真实下单通道是不同需求，上游只读导入不代表完整实盘交易。 | [portfolio schema](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/api/v1/schemas/portfolio.py)、[service/FX](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/src/services/portfolio_service.py)、[Futu holdings](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/src/brokers/futu/portfolio.py) |
| 28 | 海外社交舆情 | 本地微博/抖音/头条/雪球/韭研等已覆盖 A 股场景；缺 Adanos 的 Reddit/X/Polymarket 美股舆情、trending、watchlist 和缓存/降级集成。只有扩展美股研究时才值得优先投入。 | [SocialSentimentService](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/src/services/social_sentiment_service.py) |
| 29 | 通知传输与报告语言 | 本地通知渠道数量已足够，且 Bark 是独立渠道，Telegram topic/thread 与反代也已实现，不能说比上游少一整套推送。具体差异：飞书通知以 Webhook 为主，缺主动 App Bot/文件与云文档路径和 Lark 国际域配置；Slack 仅 Incoming Webhook，缺 Bot 文本/图片；缺 AstrBot。报告语言本地 zh/en，上游另有 ko。OpenClaw/Grok 外部 REST/Skill 集成示例也未提供；若需要兼容上游客户端，应做 API 适配层，不能把路由改名当成业务对齐。 | [通知能力](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/docs/notifications.md)、[Feishu sender](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/src/notification_sender/feishu_sender.py)、[语言](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/src/report_language.py)、[外部 Skill](https://github.com/ZhuLinsen/daily_stock_analysis/blob/be148f39ce3be8bc7f9c2d5b0ad77cd31655e78f/docs/openclaw-skill-integration.md) |

## 建议的实施顺序

1. **正确性边界**：01/02/03/05/06；为现有诊断、问股和 API 加回归。04 单独处理可终止调用与预算传播。
2. **证据和研究契约**：07 → 08 → 09。profile 应先补后端块与稳定字段；Web 接入作为下一步，避免再次在浏览器拼装多个数据真源。
3. **策略评估闭环**：11/12/19 统一身份、horizon、版本与样本口径；再做 10 的多策略审议。保留本仓库短线策略定位。
4. **运行和工作台**：15/17/18/20/21，补任务事件、告警审计、资讯 scope、可编辑调度、批量历史与原生分享。
5. **覆盖和维护**：13/16/22/23，补基本面、候选风险、轨迹评估、契约文档及 macOS Intel 包。
6. **扩展项目**：24–29 按需求立项。若继续聚焦 A 股，先做本地后端或 A 股供应商稳定性即可，无需一次性覆盖海外全部市场。

本地已有的打板评分、涨停预测、纸面交易、T+1/止损止盈执行和自学习属于本仓库的自身优势；上述对齐不应把它们替换成上游的一般投研实现。

## 验证记录与限制

执行了以下现有离线回归（仓库根目录，使用 `.venv` Python 3.14）：

```bash
.venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_stock_profile.py tests/test_stock_code.py \
  tests/test_skill_consult.py tests/test_chat_stream.py \
  tests/test_decision_signals.py tests/test_scheduler_api.py \
  tests/test_scheduler_isolation.py tests/test_fund_registry.py
```

结果：**136 passed，4 个依赖弃用警告，5.12 秒**。另用纯本地函数探针确认 01/02/03/06，以及策略 20 样本调权行为。没有修改这些行为，也没有新增伪通过的测试。

审计检查了两边的后端服务/数据提供器/存储、API 路由与 Schema、前端页面/组件/类型、桌面端、配置示例、调度、Actions/CI/发行和文档。上游源码在临时目录固定保存；所有外部源码链接都固定到上述 commit。

这是源码和离线行为审计；没有运行两边全部测试、真实 LLM、真实数据源可用性、浏览器视觉验收或桌面安装包验收。涉及 UI 的结论来自源码消费链，不是截图比较。没有把上游已知未实现的日历事件、完整 profile Web 工作台、WebPush/Apprise 或通用未完成任务恢复算成本地必补功能。

本次仅新增此报告，未修改业务代码、配置、数据库或既有未提交文件，未提交/推送代码。
