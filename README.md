# A股舆情驱动量化交易系统

从财经快讯、社交热搜、行情和国际市场等多个来源采集数据，由大语言模型（LLM）分析舆情，再用多因子加权模型给涨停股打分，预测下一交易时段最可能涨停的股票，并生成交易信号和模拟盘订单。系统会根据历史信号的实际结果自动调整因子权重。

> **免责声明：** 本项目仅供学习和研究使用，输出结果不构成任何投资建议。股市有风险，据此操作风险自负。

## 功能

- **多源数据采集：** 并发采集行情、涨停池、龙虎榜、北向资金、财经快讯、社交热搜、美股和国际新闻，单个数据源失败时自动切换备用源。
- **LLM 舆情分析：** 对新闻做情感分析和题材提取，评估国际事件对 A 股板块的影响；财联社重要快讯到达后会立即送入 LLM 分析。
- **综合评分：** 8 个因子加权打分，输出 Top N 选股和买入信号。
- **AI 涨停预测：** 按盘前、早盘、午间、午后、盘后不同时段使用不同的提示词，预测最可能涨停的 10 只股票，并给出买入价、止损价和目标价。
- **技术面分析：** 均线趋势、量价、MACD、RSI、乖离率，输出理由和风险提示；按打板策略调整，乖离率只作为高位风险扣分。
- **自学习：** 每天用信号之后的行情检验历史信号，按各因子的实际表现调整权重，并评估各新闻源的可信度。
- **信号绩效回测：** 统计信号 1/3/5 日胜率、平均收益、涨停命中率，模拟止损止盈离场，按信号类型、来源和 AI 研判分组。
- **风控与模拟交易：** 仓位上限、止损止盈、大盘熔断、ST 与股票池过滤；信号自动转为模拟盘待确认订单，持仓触及止损价或目标价时自动生成卖单，模拟 A 股 T+1。
- **每日推送：** 收盘后把大盘复盘、交易信号、待确认订单、模拟盘账户和信号绩效推送到企业微信、钉钉或飞书。
- **多种运行方式：** PyQt6 桌面端、无界面定时任务、FastAPI Web 仪表盘，以及可打包的 Windows EXE。

## 数据源

| 类别 | 来源 |
|---|---|
| 实时行情 | 腾讯财经 → 东方财富 → AKShare（新浪），依次回退 |
| 涨停池、龙虎榜、北向资金 | 东方财富 |
| 财经资讯 | 财联社电报、雪球、韭研公社、同花顺热股、东方财富热门概念 |
| 社交热搜 | 微博、抖音、今日头条 |
| 国际新闻 | 财联社国际、华尔街见闻、金十数据、东方财富全球 |
| 美股 | 重点公司财报（Mag7、半导体、中概股龙头）、VIX、美元兑人民币汇率 |

东方财富、同花顺等网站有反爬检测，这些数据通过 Playwright 驱动的无头 Chromium 获取。

## 环境要求

- Python 3.10 及以上（已在 Python 3.14 上测试）
- Chromium（通过 Playwright 安装）
- 至少一个兼容 OpenAI 接口的 LLM API Key，支持 DeepSeek、通义千问、智谱 GLM、Kimi、百度文心、豆包、硅基流动、OpenAI 或自定义地址
- Windows、macOS、Linux 均可运行；打包 EXE 仅支持 Windows

## 快速开始

```bash
git clone git@github.com:hongheshan-svg/quant_tools.git
cd quant_tools

python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium

cp config/settings.yaml.example config/settings.yaml
```

编辑 `config/settings.yaml`，至少填入 `llm.primary.api_key`，然后启动桌面端：

```bash
python run_dashboard.py
```

也可以先跳过 API Key 直接启动，再在界面上点【AI设置】切换平台、填写 Key。

数据库（`data/quant.db`）和日志目录（`logs/`）首次运行时自动创建。

## 运行方式

| 命令 | 说明 |
|---|---|
| `python run_dashboard.py` | 桌面端（主界面）：交易决策、实时资讯流、模拟交易、信号绩效、K 线图、AI 涨停预测、推送日报 |
| `python run_dashboard.py --headless` | 不打开界面，执行一次完整流程（采集 → 自学习 → 涨停预测 → 生成订单）后退出 |
| `python run_dashboard.py --warmup-before-ui` | 先执行一次完整流程，再打开界面 |
| `python main.py` | 无界面常驻运行，按下方时间表定时执行 |
| `python run_full.py` | 基于已采集数据一次性执行：舆情分析 → 题材提取 → 国际因子 → 评分，打印 Top 10 |
| `python run_score.py` | 只对已有数据评分 |
| `python run_demo.py` | 演示数据采集与展示 |
| `uvicorn src.dashboard.app:app --port 8000` | 启动 Web 仪表盘，浏览器访问 http://localhost:8000 |
| `python scripts/fetch_history.py --mode daily --start-date 2024-01-01` | 回补历史数据；`--mode` 可选 `all`、`daily`、`limit_up`、`dragon_tiger`；默认断点续传，加 `--force-full` 全量重拉 |

`main.py` 的默认时间表（可在 `scheduler` 配置中修改）。行情采集和每日任务按交易日历运行，周末和法定节假日自动跳过；新闻类采集照常进行。

| 任务 | 频率 |
|---|---|
| 财联社快讯 | 每 5 分钟 |
| 行情数据（采集后检查持仓止损止盈） | 每 15 分钟 |
| 社交热搜、国际新闻 | 每 30 分钟 |
| 每日分析（舆情、涨停、国际因子、评分） | 工作日 15:30 |
| 生成交易信号和待确认订单 | 工作日 16:00 |
| 推送每日报告 | 工作日 16:10 |
| 自学习 | 工作日 16:20 |

## 评分模型

综合评分由 8 个因子加权得出，默认权重如下（在 `strategy.weights` 中配置）：

| 因子 | 配置键 | 权重 |
|---|---|---|
| 舆情 | `sentiment_score` | 25% |
| 连板高度 | `limit_up_score` | 15% |
| 封单强度 | `seal_strength` | 15% |
| 板块效应 | `sector_effect` | 12% |
| 资金流向 | `capital_flow` | 10% |
| 国际因子（美股财报、国际事件） | `global_score` | 10% |
| 技术面 | `technical` | 8% |
| 市场情绪 | `market_emotion` | 5% |

开启自学习（`strategy.learning.enabled`）后，系统每天把学到的权重写入 `strategy.adaptive_weights`，实际权重为 `基础权重 × 65% + 自学习权重 × 35%`，比例由 `strategy.learning.blend_ratio` 控制。

## 模拟交易

信号生成后会自动转为订单，默认需要人工确认：

1. 定时任务每个工作日 16:00 生成交易信号，或在桌面端点击【AI涨停预测】。
2. 系统把买入信号转为「待确认」订单，委托价优先用 AI 给出的买入价，没有时用最新收盘价。评分引擎中评级为「观望」的股票只作关注，不下单。每笔买单都要经过风控和股票池校验，未通过的记为「已拒绝」并写明原因。
3. 在桌面端【模拟交易】页选中订单，点【确认下单】提交到模拟盘，或点【撤销订单】。该页同时显示账户资金、持仓、止损价、目标价和浮动盈亏。
4. 每次采集行情后检查持仓：最新价跌破止损价或达到目标价时生成「待确认」卖单（每只股票每天最多一笔）。止损价和目标价优先用买入信号的价格计划，没有时按 `risk.stop_loss_pct` / `risk.take_profit_pct` 计算。

几点说明：

- 模拟盘按委托价立即全部成交，不模拟滑点；按 A 股 T+1 规则，当天买入的股票次日才能卖出。
- AI 给出的价格会按最新价和各板块涨跌幅限制校验，超出范围的价格会被丢弃。
- 黑名单和股票池只限制买入，止损止盈卖出不受限制。
- 同一只股票同一天同类信号只生成一次订单，重复执行不会重复下单。
- 账户状态由数据库中的成交记录重放得到，程序重启后资金和持仓保持连续。
- 设置 `trading.auto_confirm: true` 后，订单生成即自动确认，只对模拟盘生效；设置 `trading.execution_enabled: false` 可关闭自动生成订单，但仍可在【模拟交易】页手动点【生成订单】。

## 信号绩效

桌面端【信号绩效】页统计近 60 天的交易信号：

- **入场：** 开盘前生成的信号按验证日开盘价入场，盘中生成的按当天收盘价入场。验证日指 AI 预测的目标交易日；评分信号是在收盘后生成的，验证日取下一个交易日。
- **收益与胜率：** 统计入场后第 1、3、5 个交易日收盘时的收益和胜率，以及验证日的涨停命中率。
- **止损止盈模拟：** 从入场次日开始按日线判断止损止盈；开盘就跳空越过止损价或止盈价时，按开盘价离场；同一天同时触及止损和止盈时，按止损处理。
- **分组：** 按信号类型、来源（热点驱动、涨停板、全市场、综合评分）和 AI 研判分组，可以看出哪类信号更可靠。

## 配置

主配置文件为 `config/settings.yaml`（已被 git 忽略，从 `settings.yaml.example` 复制）。本地文件只需写要修改的项，其余自动沿用 example 中的默认值。

| 配置段 | 内容 |
|---|---|
| `llm` | 主模型、备用模型（主模型失败时自动切换）、超时、重试、响应缓存 |
| `scheduler` | `main.py` 的采集间隔和每日任务时间 |
| `trading` | 交易时段；交易执行开关、自动确认、单笔订单预算、每次最多订单数、模拟盘初始资金 |
| `strategy` | 因子权重、自学习参数、Top N 数量 |
| `risk` | 单只仓位上限、单日买入上限、止损止盈、大盘熔断、黑名单关键词 |
| `global_data` | 重点跟踪的美股公司及其对应的 A 股板块 |
| `desktop` | 桌面端刷新间隔、并发线程数、是否实时 AI 分析快讯 |
| `notifier` | 每日报告开关；企业微信、钉钉、飞书机器人 Webhook（钉钉、飞书支持签名） |
| `dashboard` | Web 仪表盘地址和端口 |

股票池过滤规则在 `config/stock_pool.yaml`，下单前由风控模块逐条校验：

- **黑名单：** 指定代码，以及名称中含 ST、\*ST、退 等关键词的股票。
- **上市天数：** 默认上市不足 60 个自然日的次新股不参与。上市日期取自沪深北交易所官方列表，每天首次校验时自动更新到数据库的 `stock_info` 表。
- **关注板块：** 按涨停池中的「所属行业」匹配，为空则不限制。
- **股价范围：** 默认 3–100 元。
- **流通市值范围：** 默认 20–5000 亿元。

> **注意：** 自学习和桌面端【AI设置】会改写 `config/settings.yaml`，文件中的注释会丢失。如果不希望自学习写回文件，设置 `strategy.learning.persist_to_yaml: false`。

## 项目结构

```
├── main.py / run_*.py      入口脚本
├── config/                 配置文件
├── scripts/                历史数据回补、Windows 打包脚本
├── src/
│   ├── collectors/         数据采集器
│   ├── analyzers/          LLM 客户端与舆情、题材、国际因子分析
│   ├── strategy/           因子评分、综合评分、风控
│   ├── services/           流程编排、并发采集、涨停预测、自学习、AI 研判
│   ├── trading/            订单执行与模拟券商
│   ├── database/           SQLAlchemy 模型与 SQLite 会话
│   ├── desktop/            PyQt6 桌面端
│   ├── dashboard/          FastAPI Web 仪表盘
│   ├── notifier/           企业微信、钉钉、飞书推送
│   ├── backtest/           回测引擎
│   └── scheduler.py        定时任务
└── tests/                  测试
```

`backtest/` 目前是独立模块，还没有接入任何入口脚本；信号绩效统计由 `src/services/signal_performance.py` 负责。

## 开发

运行测试（测试不会访问网络；推送到 main 后 GitHub CI 会自动运行）：

```bash
pip install pytest
python -m pytest -q tests/
```

打包 Windows EXE：

```powershell
powershell .\scripts\build_exe.ps1
```

打包脚本依赖 PyInstaller 配置文件 `AStockQuantQt6.spec`，该文件未纳入仓库，需要自行准备。产物为 `dist\AStockQuantQt6.exe`，运行时从 EXE 所在目录读取 `config/`、`data/` 和 `logs/`。

提交信息遵循 Conventional Commits（`feat:`、`fix:`、`docs:` 等）。更多约定见 [AGENTS.md](AGENTS.md)。

## 许可证

[MIT](LICENSE)
