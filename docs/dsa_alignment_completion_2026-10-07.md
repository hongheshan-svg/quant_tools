# daily_stock_analysis 18 项对齐实施与验收（2026-10-07）

本次实现对应[修改前的 18 项审计](dsa_alignment_audit_2026-10-07.md)，参考上游固定提交 [`ce364e457aab288863a5707e7b3df79786ad07f2`](https://github.com/ZhuLinsen/daily_stock_analysis/commit/ce364e457aab288863a5707e7b3df79786ad07f2)。18 项均已接入本地实际服务；新增界面共用 Web 和 Electron。范围继续为 A 股、国内 ETF/指数，沿用现有数据、配置与任务系统。

## 逐项实现

| 编号 | 实现与行为 | 主要回归证据 |
| --- | --- | --- |
| 01 行情新鲜度 | `data_freshness` 统一检查应有的完整交易日、实际行情日和取得时刻；整库陈旧、未来日期、盘中数据跨收盘、无时间的行情均披露质量。实时选股阻止陈旧/不完整批次替换结果，历史查询保留当时口径；股票、ETF、指数共用检查。 | `test_alignment_data_contracts.py`，已有市场阶段测试 |
| 02 每日任务终态 | SDK 被 `safe_collect` 捕获后的 `partial/fetch_failed` 继续传到定时、CLI 和 API；隔离子进程失败/超时不显示完成，错误报告后仍返回失败。合法禁用/非交易日为跳过，子进程跳过退出码为 20。部分成功的来源保留已取得证据。 | `test_system_alerts.py`、`test_collection_job_outcomes.py`、`test_scheduler_isolation.py`、浏览器任务失败恢复 |
| 03 统一每日入口 | `--once` 的每日步骤与 Web、定时任务共用 `run_job` 和隔离预算，逐步返回状态；Actions 的计划运行使用同一入口。子进程使用权限为 0600 的配置快照，退出后清理。 | `test_run_once.py`、`test_scheduler_isolation.py` |
| 04 跨进程计划认领 | 共享 SQLite 按计划时刻和脱敏工作指纹原子认领；认领时冻结股票列表与配置，失败认领保留，避免部分通知后自动重复执行；手动补跑独立。 | `test_scheduled_claims.py`，双实例、不同工作、失败、冻结空列表 |
| 05 设置草稿 | 访问过的设置分类保留在内存，分类切换不丢编辑；站内离开和关闭/刷新保护，备份替换及重置确认，失败保存继续保留草稿；成功保存只清除与提交内容一致的草稿。干净模板切换直接执行，脏模板切换需确认。 | Web `settings-drafts`、`templates` 测试；真实浏览器切换、取消导航、失败 PUT、再次保存 |
| 06 持仓风险质量 | 实盘分别读取股票/ETF 行情；成本估值、缺价、陈旧、源与日期明确返回。报价/现金未知时限制集中度与暴露，缺价不触发止损，分类覆盖率与回撤不足独立披露。盘中最新报价可用于持仓风险，仍不当作完整日线。 | `test_portfolio_nav_quality.py`、持仓与风险既有测试，盘中/午休/过期报价 |
| 07 出入金与回撤 | 按真实成交、费用、红利/税/送股、现金锚点和日末外部资金流重放单位净值；资金流不当作投资亏损。新现金锚点重启可验证区间，缺行情、未知期初资本、残缺账本不给零回撤。ETF 前复权历史不代替实物账本原始价格。 | `test_portfolio_nav_quality.py`，出金例回撤为 −25%，费用/公司行为、纯现金、未知/复权历史 |
| 08 证据双时间 | ContextPack、研究产物、API 和页面保留 `provider_timestamp` 与 `fetched_at`，兼容 `timestamp`；未知观测时间不拿报告创建时刻代替。SQLite 行情取得时刻统一为上海时间，UTC 部署也能正确检查 15:00 边界。 | `test_alignment_data_contracts.py`、行情时钟回归 |
| 09 超时供应商隔离 | `bounded_call` 保留全局槽位上限，增加 provider-operation 隔离；同一超时工作仍存活时不启动第二份，健康后备源继续可用，工作退出后恢复。 | `test_alignment_data_contracts.py`，阻塞源与健康回退、隔离释放 |
| 10 ETF 双动量 | 固定风险池、防守 ETF/现金、正绝对/相对动量、固定槽位、周/月信号、下一交易日收盘执行、换仓缓冲、双边费用、等权基准、年度收益和共同区间参数扫描。只接受已标记前复权历史，截到完整交易日，缺价不虚构成交。提供 Web 参数/回测、任务 API 与 CLI。 | `test_etf_rotation.py`，设置/API 往返、费用、次日成交、缺价、共同窗口、截止日、有限 JSON；浏览器缺数据状态 |
| 11 入选和时点解释 | `why_selected/why_now` 区分事实、推断、未知，附观测与取得时刻；真实零值保留，旧/未来/无日期新闻不作为当前事实。选股保存与历史读取保留当次解释。 | `test_screening_explanations_snapshot.py` |
| 12 单候选条件诊断 | `/screening/snapshot/check` 只接受最多 50 个白名单有限标量；不联网、不写选股、不执行输入表达式。内置规则逐条件和子谓词返回通过/失败/缺失；YAML 硬条件和多因子最低分也返回具体输入与覆盖率。Web 可以展开检查“不入选”的原因。 | `test_screening_explanations_snapshot.py`，非法输入、缺值、命中、分数门槛；冻结后台与 Docker 实际 POST |
| 13 数据能力中心 | `/system/data-center` 和数据源页面展示 provider × 数据集 × 国内资产/场景、优先级及配置来源，结合本地批次日期、质量和实际使用源。运行健康与能力分开，历史成功不代表当前质量；只读查询不触发在线采集，密钥不进入响应。 | `test_data_center.py`，冷启动/旧数据/环境配置、不联网与脱敏 |
| 14 确定性问股 | 规则解析代码/名称/简称；歧义先确认，完整待执行计划保留；多任务按原文顺序执行，追问可继承近期标的，新问题撤销旧确认。明确限定股票不符时拒绝跨范围；组合工具只读。 | `test_web_intent_stages.py`、真实浏览器“分析平安，然后复盘大盘”确认并完成两任务 |
| 15 流式阶段 | 阶段开始/结束提供稳定 ID、状态、耗时、剩余预算、原因和范围；关闭连接清理活动阶段，失败保留具体脱敏原因。会话持久化后刷新可回放，UI 对未知阶段提供通用显示。 | `test_web_intent_stages.py`、问股流式既有测试、Web 阶段合并测试、浏览器刷新回放 |
| 16 自选行状态 | 聚合今日报告、历史报告、尚无报告、查询未知、进行中和最近任务失败；返回对应查看、更新、重试或等待动作。任务持久化标的集合，工作台与自选页面显示每行状态和下一步。 | `test_watchlist_status.py`，各状态及关联任务；浏览器自选页面 |
| 17 告警作用域 | 兼容单股规则，增加动态自选集合、指定账户持仓、账户级集中度/回撤/报价陈旧和持仓止损。集合每次动态展开，冷却使用父规则与标的身份；自定义止损触发时抑制重复自动止损。显示文件/环境来源、有效/禁用/非法数量，环境覆盖规则只读，增删后保留日志筛选。 | `test_alert_scopes.py`，动态集合、风险质量、ETF 止损、重复抑制、环境脱敏；浏览器账户规则保存与筛选保留 |
| 18 新供应商入口 | 妙想仅声明实际支持的国内个股资金/筹码能力：独立超时隔离、有限缓存、人民币万/亿换算、零值、交易日去重、缺失/部分状态和非法币种校验。接入资金/筹码回退链。Requesty 平台预设保留 vendor/model 前缀，模型由用户指定。 | `test_miaoxiang_requesty.py`，离线接口、单位/日期/零值、缓存、超时及模型路由 |

## 使用入口与约束

- **ETF 研究：**【策略选股 → ETF 双动量轮动】，默认不开在线更新；也可执行 `python scripts/etf_rotation.py --end 2026-09-30 --output /tmp/etf-rotation.json`。需要下载历史时加 `--refresh`，受供应商及总预算限制。候选池、周期、费用和起止日期配置见 `config/settings.yaml.example` 的 `etf_rotation`。
- **候选检查：**【策略选股 → 快照逐条件检查】，贴入扁平数值 JSON。缺字段保留未知，多因子缺失维度沿用既有中性基准，并显示覆盖率。
- **能力与来源：**【数据源状态 → 能力总览 / 来源与优先级】。来源成功探测、声明支持能力、数据库当前批次质量分别展示。
- **告警：**【盘中提醒 → 提醒规则】，选择自选集合、`paper`、`real` 或 `real:账户名`；环境变量覆盖时在部署环境修改并重启。
- **供应商：**【数据源状态 → 来源与优先级】配置妙想；【设置 → AI 模型】选择 Requesty。密钥仍只放在本地配置或 `QUANT__` 环境变量中。

ETF 回测只用于研究，不接入自动实盘下单。历史不满默认 8 年、交易日历覆盖不足、某标的历史不足、历史末日早于截止日均在报告披露。本轮使用离线价格验收，没有完成真实 8 年供应商历史的在线拉取。妙想/Requesty 的付费账户权限和在线响应没有凭据，仍需在使用者配置后验证；离线适配和路由已通过测试。

实盘回撤采用日末外部资金流口径，原始价格与公司行为配合重放；只对可验证区间给出指标。现金锚点不会反推出之前的历史资本。风险现价与完整日线分别校验：盘中本交易日取得的报价允许 30 分钟时效，午休延用上午收盘快照，未知时刻、未来时刻和过期报价降级。

## 本地验收

| 检查 | 结果 |
| --- | --- |
| `.venv/bin/python -m pytest -q -p no:cacheprovider tests/` | 2001 条通过；4 条第三方弃用提示 |
| `cd apps/web && npm run lint && npm test && npm run build` | lint/build 通过，185 条测试通过 |
| `cd apps/desktop && npm test` | 24 条测试通过 |
| `ruff check --select E9,F63,F7,F82 src api scripts main.py server.py` | 通过 |
| `python scripts/check_alignment_assets.py` | 默认策略、ETF 配置与上游 MIT 许可证资源通过 |
| `python scripts/web_e2e.py` | 全部主页面、历史报告身份、ETF 缺数据、快照、草稿、保存失败、账户告警、自选状态、问股确认/回放、任务失败恢复、窄屏、身份错误恢复通过；无页面异常 |
| `python scripts/build_desktop.py --skip-web --backend-only` 与 `scripts/smoke_backend.py` | 本机 macOS arm64 冻结后台构建与实际接口冒烟；检查自检、独立任务入口、GET 新接口及 POST 快照 |
| `docker build -f docker/Dockerfile ...` 与 `scripts/check_alignment_assets.py --url ...` | 最终 Linux 镜像启动和 Web/新接口/POST 快照冒烟；隔离空库、关闭定时和通知 |

浏览器通过隔离服务、假模型和临时 SQLite 运行，屏蔽非本地请求；不是生产数据或真实交易测试。Windows/Linux 桌面安装包仍由已有三平台发布工作流验证和构建，本轮本机只执行 macOS 冻结后台。现有 Release 安装包不会随源码 push 自动改变，发布新版本应创建新标签。

冻结后台额外携带策略源文件，确保逐条件 AST 检查在只有 pyc 的打包环境也可运行；上游 ETF 纯规则引擎保留固定提交说明与 [MIT 许可证](licenses/daily_stock_analysis-MIT.txt)，Docker 和桌面后台均包含许可证。

## 界面证据

截图来自上述隔离验收；“模拟失败”和“隔离回答”是故障/问股测试数据。

- [策略选股：ETF 缺数据与快照诊断](screenshots/dsa-2026-10-07/screening.png)
- [设置草稿：分类切换保留、失败保存不丢失](screenshots/dsa-2026-10-07/settings-draft.png)
- [自选行状态与下一步](screenshots/dsa-2026-10-07/watchlist-status.png)
- [问股歧义确认、多任务与阶段回放](screenshots/dsa-2026-10-07/chat-intent-stages.png)
- [账户告警作用域与规则来源](screenshots/dsa-2026-10-07/alert-scopes.png)
- [告警修改后日志筛选保留](screenshots/dsa-2026-10-07/alert-filters.png)
- [数据源故障和健康回退状态](screenshots/dsa-2026-10-07/sources.png)
- [数据能力矩阵与本地批次质量](screenshots/dsa-2026-10-07/data-center.png)
