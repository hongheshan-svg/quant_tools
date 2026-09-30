// 设置页各标签的帮助说明：配置项作用、常见错误与获取方式；中文与英文各一份，结构和 items 数量保持一致
import type { Lang } from '@/i18n'

export interface HelpItem {
  label: string
  text: string
}

export interface HelpSection {
  title: string
  summary: string
  items: HelpItem[]
}

export const SETTINGS_HELP: Record<string, HelpSection> = {
  llm: {
    title: 'AI 模型',
    summary: '所有 AI 分析（舆情、涨停预测、大盘复盘、个股诊断、AI 问股）都通过这里配置的大模型完成。主力模型必须配置，其余可选。',
    items: [
      { label: '平台与 Base URL', text: '选择平台后会自动带出 Base URL 和常用模型。DeepSeek、通义千问、智谱、Kimi、硅基流动等按 OpenAI 兼容接口调用；Claude（anthropic）、Gemini 使用原生接口，Base URL 留空即可。' },
      { label: 'API Key', text: '到所选平台的控制台创建。支持填多个（换行或逗号分隔），请求时轮流使用；遇到 401/403/429 的 Key 会冷却 10 分钟并自动换下一个。保存后界面只显示后 4 位，掩码原样保存表示保留原值。常见错误：多复制了空格、Key 属于别的平台、余额用尽。' },
      { label: '模型名', text: '必须是该平台真实存在的模型 ID（例如 deepseek-chat）。可点「获取模型列表」从平台拉取；填错会返回 404 或 model not found。' },
      { label: 'Ollama 本地模型', text: '平台选 ollama，Base URL 填 http://localhost:11434，不需要 API Key，先在本机 ollama pull 对应模型。' },
      { label: '备用模型', text: '主力模型失败（限流、超时、Key 失效）时自动切换。Key 仍是 your- 开头的占位符时会被跳过。' },
      { label: '图片识别模型', text: '截图导入自选股时使用，需要支持图片输入（如 gpt-4o、Claude、Gemini、qwen-vl、glm-4v）。留空则用主模型识别。' },
      { label: '测试按钮', text: '发送一次真实请求验证连通性，会产生极少量费用。失败信息会给出原因，如 Key 无效、网络不通或模型不存在。' },
    ],
  },
  notifier: {
    title: '推送',
    summary: '把日报、盘中提醒、自选股仪表盘和系统错误推送到手机或邮箱。至少启用一个渠道才会推送；没有任何渠道启用时不会生成日报。',
    items: [
      { label: '企业微信 / 钉钉 / 飞书', text: '在群聊里添加自定义机器人，复制 Webhook 地址。钉钉、飞书若开启了「加签」安全设置，需要同时填写 secret。Webhook 仍是 your- 开头的示例值视为未配置。' },
      { label: '邮件', text: '填写 SMTP 服务器、端口、账号和收件人。QQ、163 邮箱需在邮箱设置里开启 SMTP，并把授权码（不是登录密码）填到密码；465 端口勾选 SSL，587 端口取消勾选（使用 STARTTLS）。' },
      { label: 'Telegram / Discord / Slack', text: 'Telegram 用 @BotFather 创建机器人得到 bot_token，并填写 chat_id；Discord、Slack 创建 Incoming Webhook 并复制地址。' },
      { label: 'PushPlus / Server酱 / ntfy 等', text: '到各服务官网获取 token 或 SendKey 填入即可。' },
      { label: '消息路由', text: '可为日报、提醒、自选股、问股等消息类型指定渠道；没有指定路由的类型推送到全部已启用渠道。' },
      { label: '免打扰时段', text: '例如 22:00-08:00，期间只推送紧急提醒（如跌破止损）。' },
      { label: '检查配置与测试', text: '「检查配置」列出缺失项，「测试」向渠道发送一条测试消息。收不到消息时优先检查 Webhook 是否过期、机器人关键词/IP 白名单等安全设置。' },
    ],
  },
  bot: {
    title: '聊天机器人',
    summary: '让钉钉、飞书里的机器人直接查询大盘、持仓、诊断股票并与 AI 问股。修改机器人凭证后需要重启服务才会生效。',
    items: [
      { label: '钉钉', text: '钉钉开放平台创建企业内部应用并添加机器人，消息接收模式选「Stream 模式」，把 AppKey 填到 client_id，AppSecret 填到 client_secret。' },
      { label: '飞书', text: '飞书开放平台创建企业自建应用并开启机器人，事件订阅选「长连接」并订阅 im.message.receive_v1，填写 app_id、app_secret；海外版 Lark 的域名选 lark。' },
      { label: '允许的用户', text: '填写允许使用机器人的用户 ID（钉钉 senderStaffId、飞书 open_id），为空表示不限制。没有权限的用户发消息时会收到自己的 ID，可复制后加入名单。' },
      { label: '常见错误', text: '凭证填错时机器人不会连上，日志中会以逐渐加长的间隔重试；应用未发布或未授予权限也会导致收不到消息。' },
    ],
  },
  search: {
    title: '联网搜索',
    summary: '为个股诊断和 AI 问股补充最新新闻搜索结果。默认关闭，开启后至少要配置一个搜索服务的 Key 或地址。',
    items: [
      { label: '启用与服务顺序', text: '按顺序尝试各搜索服务，前一个失败自动换下一个。博查（bocha）中文新闻效果好，推荐首选。' },
      { label: '博查 / Tavily / SerpAPI / Brave', text: '分别到各自官网注册并获取 API Key，可填多个；Tavily 每月有免费额度。' },
      { label: 'SearXNG', text: '自建实例的地址，例如 http://127.0.0.1:8080；实例的 settings.yml 需要在 search.formats 中开启 json，否则会返回 403。' },
      { label: '结果数量与天数', text: '每次最多返回条数，以及只保留最近多少天的结果（日期未知的保留）。相同搜索词有缓存，缓存时间内不重复请求。' },
      { label: '测试', text: '用一个关键词实际搜索，确认 Key 和网络可用。' },
    ],
  },
  intelligence: {
    title: '资讯源',
    summary: '订阅任意 RSS / Atom 地址，采集后进入资讯流并参与舆情分析，全天按间隔采集，与交易日无关。',
    items: [
      { label: '订阅地址', text: '填写 RSS 2.0、Atom 或 RSS 1.0 地址。没有现成 RSS 的财经媒体可用 RSSHub 生成地址。地址无法访问或不是合法 XML 时测试会报错。' },
      { label: '采集间隔', text: '单位分钟，最少 5 分钟。' },
      { label: '每源条数', text: '每个源每次最多取多少条，避免一次入库过多。' },
      { label: '去重天数', text: '这段时间内相同链接（无链接时相同标题）不重复入库。' },
      { label: '立即采集', text: '不必等定时任务，手动抓取一次并显示新增条数。' },
    ],
  },
  scheduler: {
    title: '定时任务',
    summary: '查看定时任务的下次运行时间，并可立即运行某个任务。时间和间隔在 settings.yaml 的 scheduler 段配置。',
    items: [
      { label: '运行条件', text: '服务需要在运行且未使用 --no-scheduler；同时运行 main.py 与 server.py 的定时任务会重复采集，请二选一。' },
      { label: '间隔任务', text: '热搜、财联社电报、行情、国际新闻按分钟间隔运行；行情采集只在交易时段进行。' },
      { label: '每日任务', text: '默认工作日 15:30 每日分析、16:00 生成信号、16:10 大盘复盘与日报、16:20 自学习、16:30 自选股仪表盘。非交易日会跳过依赖行情的任务。' },
      { label: '立即运行', text: '任务在后台执行，进度可在右上角任务中心查看；同一任务正在运行时不会重复启动。' },
    ],
  },
  backup: {
    title: '备份与恢复',
    summary: '导出当前配置为文件，或从文件恢复配置，方便迁移和备份。',
    items: [
      { label: '导出', text: '默认不包含 API Key、Webhook 等密钥；勾选「包含密钥」后导出文件含明文密钥，请妥善保管，不要发给他人或提交到仓库。' },
      { label: '导入', text: '导入会整体覆盖当前 settings.yaml 并立即生效，操作前请确认。导入内容必须是合法的 YAML，缺少的配置项会用默认值补齐。' },
      { label: '注意', text: '导出的是配置，不包含数据库中的行情、信号和订单数据；数据在数据目录的 data/quant.db 中，需要另行备份。' },
    ],
  },
  security: {
    title: '登录安全',
    summary: '控制访问网页的方式。默认只允许本机访问；需要手机或局域网访问时必须开启登录。',
    items: [
      { label: '开启登录', text: '开启后需要密码登录（首次登录设置密码，至少 6 位），密码以哈希形式保存在 data/web_auth.json。' },
      { label: '对外监听', text: 'web.host 改为 0.0.0.0 可让局域网设备访问，此时务必开启登录，容器外的请求不算本机。' },
      { label: '修改密码', text: '需要输入当前密码，新密码至少 6 位。忘记密码时可停止服务后删除 data/web_auth.json 重新设置。' },
      { label: 'API Token', text: 'web.api_token 用于脚本或机器人以 Authorization: Bearer 方式调用接口，留空表示不启用，需在 settings.yaml 中配置。' },
    ],
  },
  desktop: {
    title: '桌面端',
    summary: '仅在 Electron 桌面端里显示，用于查看版本、打开数据目录和日志目录。',
    items: [
      { label: '数据目录', text: '存放 config、data、logs：配置文件、数据库和日志都在其中，备份或迁移时复制该目录即可。' },
      { label: '日志目录', text: '排查启动失败、采集失败时先看 logs 下的日志，其中 desktop.log 记录桌面端本身的启动情况。' },
      { label: '重试', text: '后台服务启动失败或崩溃后，可在错误页点击重试重新启动。' },
    ],
  },
}

export const SETTINGS_HELP_EN: Record<string, HelpSection> = {
  llm: {
    title: 'AI Models',
    summary: 'All AI analysis (sentiment, limit-up prediction, market review, stock diagnosis, AI chat) runs on the large models configured here. The primary model is required; the others are optional.',
    items: [
      { label: 'Platform and Base URL', text: 'Choosing a platform fills in the Base URL and common models automatically. DeepSeek, Qwen, Zhipu, Kimi, SiliconFlow and similar platforms use the OpenAI-compatible API; Claude (anthropic) and Gemini use their native APIs, so leave Base URL empty.' },
      { label: 'API Key', text: "Create one in the selected platform's console. You can enter several (one per line or comma-separated); they are used in rotation, and a key that returns 401/403/429 cools down for 10 minutes while the next one is used. After saving, only the last 4 characters are shown; saving the mask unchanged keeps the original value. Common mistakes: extra spaces copied, a key from another platform, or an exhausted balance." },
      { label: 'Model name', text: 'Must be a real model ID on that platform (for example deepseek-chat). Click "Fetch model list" to load it from the platform; a wrong name returns 404 or "model not found".' },
      { label: 'Ollama local models', text: 'Choose ollama as the platform, set Base URL to http://localhost:11434, no API key is needed, and run ollama pull for the model on your machine first.' },
      { label: 'Backup model', text: 'Used automatically when the primary model fails (rate limit, timeout, invalid key). It is skipped while its key is still a placeholder starting with your-.' },
      { label: 'Vision model', text: 'Used when importing watchlist stocks from screenshots; it must support image input (such as gpt-4o, Claude, Gemini, qwen-vl, glm-4v). If left empty, the primary model is used.' },
      { label: 'Test buttons', text: 'Send one real request to verify connectivity, which costs a tiny amount. A failure shows the reason, such as an invalid key, network problem or missing model.' },
    ],
  },
  notifier: {
    title: 'Notifications',
    summary: 'Push daily reports, intraday alerts, the watchlist dashboard and system errors to your phone or mailbox. At least one channel must be enabled to push; with no channel enabled, no daily report is generated.',
    items: [
      { label: 'WeCom / DingTalk / Feishu', text: 'Add a custom bot to a group chat and copy its Webhook URL. If DingTalk or Feishu has the "signature" security option enabled, fill in the secret too. A Webhook that is still an example value starting with your- counts as not configured.' },
      { label: 'Email', text: 'Enter the SMTP server, port, account and recipients. For QQ and 163 mail, enable SMTP in the mailbox settings and put the authorization code (not the login password) in the password field; tick SSL for port 465 and untick it for port 587 (STARTTLS).' },
      { label: 'Telegram / Discord / Slack', text: 'For Telegram, create a bot with @BotFather to get the bot_token and fill in the chat_id; for Discord and Slack, create an Incoming Webhook and copy its URL.' },
      { label: 'PushPlus / ServerChan / ntfy, etc.', text: "Get the token or SendKey from each service's website and fill it in." },
      { label: 'Routing', text: 'You can pick channels for each message type (daily report, alerts, watchlist, AI chat, etc.); types without a route are pushed to all enabled channels.' },
      { label: 'Quiet hours', text: 'For example 22:00-08:00; during this period only urgent alerts (such as a stop-loss breach) are pushed.' },
      { label: 'Check config and test', text: '"Check config" lists what is missing, and "Test" sends a test message to the channel. If nothing arrives, first check whether the Webhook has expired and the bot security settings such as keywords or IP allowlist.' },
    ],
  },
  bot: {
    title: 'Chat Bots',
    summary: 'Let the bots in DingTalk and Feishu query the market, positions, diagnose stocks and chat with the AI directly. After changing bot credentials, restart the service for them to take effect.',
    items: [
      { label: 'DingTalk', text: 'Create an internal enterprise app on the DingTalk Open Platform and add a bot, choose "Stream mode" for message receiving, then put the AppKey in client_id and the AppSecret in client_secret.' },
      { label: 'Feishu', text: 'Create a self-built enterprise app on the Feishu Open Platform and enable the bot, choose "long connection" for event subscription and subscribe to im.message.receive_v1, then fill in app_id and app_secret; for the international Lark, set the domain to lark.' },
      { label: 'Allowed users', text: 'Enter the IDs of users allowed to use the bot (DingTalk senderStaffId, Feishu open_id); empty means no restriction. A user without permission receives their own ID when sending a message, which you can copy into the list.' },
      { label: 'Common errors', text: 'With wrong credentials the bot will not connect, and the log shows retries at gradually longer intervals; an unpublished app or missing permissions also prevents messages from arriving.' },
    ],
  },
  search: {
    title: 'Web Search',
    summary: 'Adds the latest news search results to stock diagnosis and AI chat. Off by default; once enabled, configure at least one search service key or address.',
    items: [
      { label: 'Enable and service order', text: 'Search services are tried in order, and if one fails the next is used. Bocha gives good results for Chinese news and is the recommended first choice.' },
      { label: 'Bocha / Tavily / SerpAPI / Brave', text: 'Register on each official website to get an API key; you can enter several. Tavily has a free monthly quota.' },
      { label: 'SearXNG', text: 'The address of a self-hosted instance, for example http://127.0.0.1:8080; the instance must enable json in search.formats in its settings.yml, otherwise it returns 403.' },
      { label: 'Result count and days', text: 'The maximum number of results per search and how many recent days of results to keep (results with unknown dates are kept). Identical queries are cached and not requested again within the cache time.' },
      { label: 'Test', text: 'Runs a real search with a keyword to confirm that the key and network work.' },
    ],
  },
  intelligence: {
    title: 'News Sources',
    summary: 'Subscribe to any RSS / Atom address; collected articles enter the news feed and take part in sentiment analysis. They are collected around the clock at the set interval, regardless of trading days.',
    items: [
      { label: 'Feed address', text: 'Enter an RSS 2.0, Atom or RSS 1.0 address. For financial media without an RSS feed, use RSSHub to generate one. The test reports an error if the address is unreachable or not valid XML.' },
      { label: 'Collection interval', text: 'In minutes, at least 5.' },
      { label: 'Items per source', text: 'The maximum number of items taken from each source each time, to avoid storing too many at once.' },
      { label: 'Dedup days', text: 'Within this period, items with the same link (or the same title when there is no link) are not stored again.' },
      { label: 'Collect now', text: 'Fetch once manually without waiting for the scheduled job, and show the number of new items.' },
    ],
  },
  scheduler: {
    title: 'Scheduled Jobs',
    summary: 'View the next run time of each scheduled job and run any job immediately. Times and intervals are configured in the scheduler section of settings.yaml.',
    items: [
      { label: 'Requirements', text: 'The service must be running without --no-scheduler; running the scheduled jobs of both main.py and server.py collects data twice, so choose one.' },
      { label: 'Interval jobs', text: 'Hot search, Cailianshe telegraph, market data and international news run at minute intervals; market data is collected only during trading hours.' },
      { label: 'Daily jobs', text: 'By default on weekdays: 15:30 daily analysis, 16:00 signal generation, 16:10 market review and daily report, 16:20 self-learning, 16:30 watchlist dashboard. Jobs that depend on market data are skipped on non-trading days.' },
      { label: 'Run now', text: 'The job runs in the background and its progress appears in the task center at the top right; a job that is already running is not started twice.' },
    ],
  },
  backup: {
    title: 'Backup & Restore',
    summary: 'Export the current configuration to a file, or restore it from a file, for easy migration and backup.',
    items: [
      { label: 'Export', text: 'By default API keys, Webhooks and other secrets are not included; if you tick "Include secrets", the exported file contains plaintext secrets, so keep it safe and do not send it to others or commit it to a repository.' },
      { label: 'Import', text: 'Importing overwrites the whole current settings.yaml and takes effect immediately, so confirm before proceeding. The content must be valid YAML, and missing settings are filled with defaults.' },
      { label: 'Note', text: 'The export contains configuration only, not the market data, signals and orders in the database; that data is in data/quant.db in the data directory and must be backed up separately.' },
    ],
  },
  security: {
    title: 'Security',
    summary: 'Controls how the web page can be accessed. By default only this machine is allowed; login must be enabled for access from a phone or the local network.',
    items: [
      { label: 'Enable login', text: 'When enabled, a password is required (set on first login, at least 6 characters), and it is stored as a hash in data/web_auth.json.' },
      { label: 'Listening externally', text: 'Setting web.host to 0.0.0.0 lets devices on the local network connect; always enable login in that case, because requests from outside the container do not count as local.' },
      { label: 'Change password', text: 'Requires the current password, and the new password must be at least 6 characters. If you forget it, stop the service and delete data/web_auth.json to set it again.' },
      { label: 'API Token', text: 'web.api_token lets scripts or bots call the API with Authorization: Bearer; leave it empty to disable it, and configure it in settings.yaml.' },
    ],
  },
  desktop: {
    title: 'Desktop',
    summary: 'Shown only in the Electron desktop app, for viewing the version and opening the data and log directories.',
    items: [
      { label: 'Data directory', text: 'Holds config, data and logs: the configuration files, database and logs are all in it, so copy this directory to back up or migrate.' },
      { label: 'Log directory', text: 'When troubleshooting a startup or collection failure, look at the logs under logs first; desktop.log records how the desktop app itself started.' },
      { label: 'Retry', text: 'If the background service fails to start or crashes, click Retry on the error page to start it again.' },
    ],
  },
}

/** 按界面语言返回设置帮助；英文缺少的标签回退到中文 */
export function getSettingsHelp(lang: Lang): Record<string, HelpSection> {
  return lang === 'en' ? { ...SETTINGS_HELP, ...SETTINGS_HELP_EN } : SETTINGS_HELP
}
