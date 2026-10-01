// 实盘出入金流水
export default {
  记一笔出入金: 'Record transfer',
  出入金: 'Deposits & withdrawals',
  入金: 'Deposit',
  出金: 'Withdrawal',
  '入金（银行转证券）': 'Deposit (bank → brokerage)',
  '出金（证券转银行）': 'Withdrawal (brokerage → bank)',
  '金额（元）': 'Amount (CNY)',
  净入金: 'Net deposits',
  '净入金 {v}': 'Net deposits {v}',
  累计收益: 'Total return',
  删除出入金: 'Delete transfer',
  '删除 {date} 这笔{dir}？': 'Delete the {dir} on {date}?',
  '暂无出入金记录；只记出入金和成交、不设置可用资金时，按全部流水算出可用资金和累计收益':
    'No transfers yet. If you record transfers and trades without setting available cash, cash and total return are computed from the full history',
  '之后发生的成交和出入金会自动增减；设置之前日期的出入金视为已包含在内':
    'Later trades and transfers adjust it automatically; transfers dated before this are treated as already included',
}
