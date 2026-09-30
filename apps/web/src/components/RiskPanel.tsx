// 组合风险：总仓位与大盘环境、行业分布、回撤、提示
import type { RiskReport } from '@/api/types'
import { useT } from '@/i18n'
import { fmtNum } from '@/utils/format'

export function RiskPanel({ risk }: { risk: RiskReport }) {
  const t = useT()
  const dd = risk.drawdown ?? { max_drawdown: 0, current_drawdown: 0, max_drawdown_date: '' }
  return (
    <div className="space-y-2 text-sm">
      <div className="flex flex-wrap gap-x-5 gap-y-1">
        {risk.cash_known && <span>{t('总仓位')} <b className="num">{fmtNum(risk.exposure, 0)}%</b>{risk.suggested_exposure != null && <span className="text-muted">{t('（大盘「{regime}」建议不超过 {pct}%）', { regime: t(risk.regime), pct: risk.suggested_exposure })}</span>}</span>}
        <span>{t('最大回撤')} <b className="num text-down">{fmtNum(dd.max_drawdown)}%</b>{dd.max_drawdown_date && <span className="text-muted">（{dd.max_drawdown_date}）</span>}</span>
        <span>{t('当前回撤')} <b className="num">{fmtNum(dd.current_drawdown)}%</b></span>
        {risk.sectors.length > 0 && <span className="text-muted">{t('行业：')}{risk.sectors.slice(0, 5).map((s) => `${s.sector} ${s.weight.toFixed(0)}%`).join('、')}</span>}
      </div>
      {risk.warnings.length > 0 && (
        <ul className="space-y-0.5 rounded-md border border-warn/40 bg-warn/10 px-3 py-2 text-warn">
          {risk.warnings.map((w) => <li key={w}>⚠ {w}</li>)}
        </ul>
      )}
    </div>
  )
}
