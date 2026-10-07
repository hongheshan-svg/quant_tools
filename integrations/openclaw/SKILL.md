---
name: quant-tools-research
description: 通过已部署的 quant_tools API 分析证券、读取报告和查询当前有效信号。
---

使用主机环境变量 QUANT_BASE_URL（应用地址）和 QUANT_API_TOKEN（web.api_token）。不要把令牌写进对话、URL、截图或报告。需要长期运行的 server.py / Docker 服务。

1. 用 GET /api/v1/stocks/search?q=... 解析名称。遇到多个匹配先让用户选择，保留同句中已确认的其他标的。
2. 用户要求新分析时，POST /api/v1/stocks/{code}/diagnosis。响应中的 id 是后台任务标识。
3. 每秒 GET /api/v1/tasks/{id}，status=done 时读取 result；status=error 时明确报告 error。超时后继续查询原任务，不重复 POST。
4. 只查询已有结论时，GET /api/v1/stocks/{code}/profile 或 /diagnosis，无需重新消耗模型。
5. 当前有效信号：GET /api/v1/signals?status=active&valid_only=true&code={code}。不要把过期信号表述为当前建议。
6. 每次说明行情日期、币种、数据质量、报告生成时间和限制。海外证券使用 AAPL、00700.HK、7203.T、005930.KS、2330.TW；不将不同币种直接相加。
7. 此 Skill 只允许上述研究接口及 /health，不调用订单、配置写入、通知发送或删除接口。用户另有请求时应使用单独的明确授权工作流。

每个 HTTP 请求携带 Authorization: Bearer $QUANT_API_TOKEN。客户端示例在上级目录 assistant_client.py。首次 401/403 时请用户检查应用令牌或部署访问设置，不尝试绕过鉴权。
