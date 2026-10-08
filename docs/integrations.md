# 扩展接入

本轮按 daily_stock_analysis 固定提交 ce364e457aab288863a5707e7b3df79786ad07f2 的能力边界接入，配置入口为“扩展接入”。仅在用户执行查询、分析或发送测试消息时访问对应服务。

## OpenClaw 与 Grok Bot

两种外部助手共用现有 REST 契约，无需新增模型平台。分别复制 [OpenClaw Skill](../integrations/openclaw/SKILL.md) 或 [Grok Bot Skill](../integrations/grok_bot/SKILL.md) 到外部助手支持的 Skill 目录。应用需要持续运行 `python server.py` 或 Docker。

在外部助手主机设置 `QUANT_BASE_URL` 和 `QUANT_API_TOKEN`；后者对应应用的 `web.api_token`，不是 LLM Key。远程访问需配置应用鉴权并使用 HTTPS。不要把 Token 写进 Skill 正文。

| 用途 | 接口 |
| --- | --- |
| 健康检查 | GET /api/v1/health |
| 名称解析 | GET /api/v1/stocks/search?q=茅台 |
| 新分析 | POST /api/v1/stocks/600519/diagnosis |
| 等待任务 | GET /api/v1/tasks/{id} |
| 已有研究 | GET /api/v1/stocks/600519/profile |
| 当前有效信号 | GET /api/v1/signals?code=600519&status=active&valid_only=true |
| AI 问股 | POST /api/v1/chat/sessions，然后 POST /api/v1/chat/sessions/{id}/ask |

任务状态为 `pending/running/done/error`（以实际接口状态为准），完成报告在 `result`。等待超时不会取消服务器任务；继续查询原 id，不重提分析。可运行 `python integrations/assistant_client.py 600519`；`--task ID` 恢复等待；`--signals` 只读取有效信号。示例不会发送通知或执行订单。

## 海外证券、持仓与数据

“海外研究”支持港、美、日、韩、台代码，交易日历使用各市场的 exchange-calendars，日线依次尝试 Yahoo Finance 和已配置的 Longbridge（港美）。行情缓存与 A 股选股数据库隔离，使用原币、原始不复权日线；只读研究不接自动交易。盘中尚未完成的日线不当成完整日线。

持仓 CSV 支持 symbol/ticker/code/证券代码，quantity/qty/持仓数量，average_cost/cost_price/成本价，currency/币种，market/市场。提供富途、长桥及通用英文导出常用字段别名；带 side / 买卖方向表头的 CSV 作为成交明细处理，另需 trade_date / 成交日期、price / 成交价，可带 fee / 手续费和 trade_id / 成交编号。先预览再确认。相同账户/代码按日期更新、重复导入不叠加，数量 0 表示平仓；不删除 CSV 中未出现的其他证券。成交按日期回放平均成本，买入费用计入成本；成交编号或内容去重，冲突编号、超卖、错误币种会整批拒绝。快照与成交账本必须用不同账户名称，避免重复计仓。只有日期的同日交易按 CSV 行序处理，需按实际成交顺序导出。仅估值持仓，不包含现金及完整已实现损益。人民币合计要求每条价格及 FX 均可用，汇率日期超过 3 天或未来日期不进入合计。

Futu 读取本机 OpenD 的真实 LONG 持仓标的；不解锁交易、不下单，不自动导入未知成本。Longbridge 需要 App Key、App Secret、Access Token。服务未安装、未登录或账户权限不足会给出失败状态。

Linux 桌面包保留 Ubuntu 22.04，Docker 保留 Debian 12；两者使用 Python 3.11 与 Longbridge 0.2.74 的兼容 wheel。Windows/macOS 使用新版 SDK。源码安装若选 Linux Python 3.12+，则要求 glibc 2.39+。适配器同时支持两代配置构造接口，发布自检会加载日历、宏观目录与券商 SDK 资源。

## FXMacroData

固定公开客户端 v0.1.0 的 72 项原生工具保留原参数结构，界面可逐项查询；AI 问股也能调用 `fxmacrodata_*` 工具。公开 USD 数据无需密钥；授权数据填写 API Key。响应保留来源、发布时间及预测类别；不可用时明确标识，凭据不作为模型参数。Codex App Server 的可取消本地工具集合不包含这些联网工具。

## 本地模型

AI 模型平台可选 Codex CLI、Claude Code CLI、OpenCode CLI、Hermes。前三者要求应用主机已安装并登录对应程序。生成在独立临时目录执行，使用固定限制参数、超时和 1 MiB 输出限制；CLI 认证由各自登录管理。版本不支持受限参数时失败，不自动去掉限制重试。

问股界面选择这些本地后端时，等待生成完成后一次显示答复；不向后端发送原生流式或工具参数。报告语言也适用于问股。

Hermes 仅接受 `localhost/127.0.0.1/::1` 的 `/v1` 地址，无 API Key、原生工具、流式或视觉输入，不使用代理环境变量或跟随重定向。

“Codex App Server 只读问股”提供限定本地研究工具、调用次数限制、权限校验、取消和运行记录。会话历史保存在本地数据库；继续会话时将已完成消息注入新隔离会话，失败调用不重放。不加载主机 MCP、插件或仓库指令。需要支持命名 permissions 和动态工具协议的 Codex 版本；不兼容会明确失败。配置检查只确认程序存在，不等于登录、协议和网络调用已通过。

## 通知与韩文

飞书/Lark 支持原 Webhook，以及应用 App ID/Secret + chat_id 主动卡片和图片；区域为 feishu/lark。Slack 支持原 Webhook，或 Bot Token + channel_id（图片必须 Bot 模式）。Bot 需进入目标频道并具备消息/文件发送权限。AstrBot 使用适配器 URL 与可选 HMAC 签名 Token。

“AI 输出语言”可选韩文，自由文本使用韩文，JSON 键和动作/信心枚举保持不变。美股社交证据通过 Adanos 读取 Reddit、X、Polymarket；渠道失败分别标识，舆情不被当作经过核实的新闻或行情。

## 选股近分轮换

浏览器使用匿名本地 seed；每次选股只在合格且分数距离截止分不超过 1.5 的候选中轮换末端名额。前半优先候选、市场适配和模型复核限制保持有效，不修改评分。`screening.selection_variant.rotation_ratio=0` 关闭。同步与任务接口共用相同规则，不传 seed 则使用原排序。
