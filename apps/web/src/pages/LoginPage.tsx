import { useState } from 'react'
import { api } from '@/api/endpoints'
import { Button, Input } from '@/components/ui'
import { useLang, useT } from '@/i18n'

export function LoginPage({ passwordSet, onLoggedIn }: { passwordSet: boolean; onLoggedIn: () => void }) {
  const t = useT()
  const [lang, setLang] = useLang()
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const submit = async () => {
    setLoading(true)
    setError('')
    try {
      await api.login(password)
      onLoggedIn()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="flex h-full items-center justify-center p-4">
      <form
        className="w-full max-w-sm rounded-lg border border-line bg-panel p-6"
        onSubmit={(e) => {
          e.preventDefault()
          void submit()
        }}
      >
        <div className="mb-1 flex items-center justify-between">
          <h1 className="text-lg font-semibold">{t('A股量化')}</h1>
          <button
            type="button"
            aria-label={t('切换语言')}
            onClick={() => setLang(lang === 'en' ? 'zh' : 'en')}
            className="rounded-md border border-line px-2 py-1 text-xs text-muted hover:text-text"
          >
            {lang === 'en' ? '中' : 'EN'}
          </button>
        </div>
        <p className="mb-4 text-xs text-muted">{passwordSet ? t('请输入访问密码') : t('首次使用，请设置访问密码（至少 6 位）')}</p>
        <Input type="password" autoFocus value={password} onChange={(e) => setPassword(e.target.value)} placeholder={t('密码')} aria-label={t('密码')} />
        {error && <p className="mt-2 text-sm text-danger">{error}</p>}
        <Button type="submit" variant="primary" className="mt-4 w-full" loading={loading}>
          {passwordSet ? t('登录') : t('设置密码并登录')}
        </Button>
      </form>
    </div>
  )
}
