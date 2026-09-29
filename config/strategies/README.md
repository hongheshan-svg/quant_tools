# 自定义问股策略

把 `.yaml` / `.yml` 文件放在本目录，AI 问股会自动加载（改文件后无需重启）。README 等其他文件不会被加载。
`name` 与内置策略相同时覆盖内置策略；缺少 `name`、`display_name`、`instructions` 或 YAML 写错的文件会被跳过，日志里有 warning。

## 模板

```yaml
name: my_strategy            # 英文标识，唯一
display_name: 我的策略        # 中文名，界面和机器人里显示；会话里保存的就是它
description: 一句话说明这个策略看什么
category: pattern            # trend/pattern/reversal/emotion/framework/fundamental
aliases: [我的, mine]        # 别名，不能和其他策略的名称或别名重复
market_regimes: [进攻, 均衡]  # 适配的大盘环境（进攻/均衡/防守/冰点），可留空
priority: 100                # 越小越靠前
instructions: |
  判断标准：……
  入场条件：……
  出场与止损：……
  可用工具：quote、daily_bars、technical、fund_flow、chips、earnings、news、
  limit_up_history、theme、market、screening、position、diagnosis、watchlist。
  对结论的影响：……
```

## 使用

- Web 问股页的策略下拉框选择；
- 聊天机器人：`策略 我的 宁德时代能买吗`（也可用英文标识或别名）。
