// 设置 → 备份与恢复：检查配置（未知键、类型、格式、语义），只提示不修改
import { useState } from 'react'
import { api } from '@/api/endpoints'
import type { ConfigCheckResult } from '@/api/types'
import { Button } from '@/components/ui'
import { useT } from '@/i18n'
import { toast } from '@/stores/toast'

export function ConfigCheckSection() {
  const t = useT()
  const [result, setResult] = useState<ConfigCheckResult | null>(null)
  const [busy, setBusy] = useState(false)

  async function run() {
    setBusy(true)
    try {
      setResult(await api.configCheck())
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="space-y-2">
      <h3 className="text-sm font-medium">{t('检查配置')}</h3>
      <p className="text-xs text-muted">{t('检查 settings.yaml 的拼写、类型、时间格式和必填项，只提示不修改。')}</p>
      <Button loading={busy} onClick={() => void run()}>
        {t('检查配置')}
      </Button>
      {result && (
        <div className="space-y-1 text-sm">
          {result.issues.length === 0 ? (
            <div className="text-down">{t('配置检查通过')}</div>
          ) : (
            <>
              <div>{t('{e} 个错误，{w} 个警告', { e: result.errors, w: result.warnings })}</div>
              <ul className="space-y-1">
                {result.issues.map((i) => (
                  <li key={`${i.level}:${i.path}:${i.message}`} className={i.level === 'error' ? 'text-danger' : 'text-warn'}>
                    <span className="font-mono">{i.path}</span>：{i.message}
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      )}
    </section>
  )
}
