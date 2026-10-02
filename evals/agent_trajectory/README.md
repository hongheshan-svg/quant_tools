# 问股工具轨迹离线评估

`cases.json` 定义 A 股、国内指数、大盘与资讯核查四类执行案例。它约束工具覆盖、最大调用量、重复/失败上限和可选耗时预算，不对模型答案的投资价值作自动判断。

将授权模型环境或离线替身产生的完整 ChatTurn 按案例 ID 保存到 JSON：

```json
{
  "single_stock": {
    "question": "检查贵州茅台的趋势",
    "answer": "示例答复",
    "tools": [
      {"name":"quote","args":{"code":"600519"},"result":"示例行情"},
      {"name":"daily_bars","args":{"code":"600519"},"result":"示例日线"}
    ],
    "run_log": {"total_ms":100},
    "error":""
  }
}
```

执行：

```bash
python scripts/eval_trajectories.py trajectories.json
```

输出包含 tool_coverage、requested_calls、unique_calls、duplicate_calls、failed_calls、scope_violations、elapsed_ms、budget_observed 和 passed。缺案例或无有效答复不会通过；越界调用即使失败数上限放宽也不能通过；案例设置 max_ms 后，缺 runtime 不能通过。默认案例不设置未经真实测量的时延门槛。

重复指标统计模型请求的重复调用，成功缓存可以减少实际执行，但不会掩盖模型重复请求。工具覆盖反映请求覆盖，错误前缀单独计入失败。运行脚本不会联网采集轨迹，也不会调用真实模型；本次没有伪造真实 LLM 基准成绩。
