"""
聊天机器人：在钉钉、飞书里用命令或自然语言使用系统（参考 daily_stock_analysis 的 bot/）。

- router.CommandRouter：与平台无关的命令分发（诊断、大盘、自选、持仓、预测、状态、AI 问股）
- dingtalk / feishu：钉钉 Stream、飞书长连接适配器，不需要公网 IP
- manager.start_bots()：按 bot 配置在后台启动，由 server.py 和 main.py 调用
"""
