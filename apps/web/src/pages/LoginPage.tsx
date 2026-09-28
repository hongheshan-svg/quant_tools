import { useState } from 'react'
import { api } from '@/api/endpoints'
import { Button, Input } from '@/components/ui'

export function LoginPage({ passwordSet, onLoggedIn }: { passwordSet: boolean; onLoggedIn: () => void }) {
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
        <h1 className="mb-1 text-lg font-semibold">A股量化</h1>
        <p className="mb-4 text-xs text-muted">{passwordSet ? '请输入访问密码' : '首次使用，请设置访问密码（至少 6 位）'}</p>
        <Input type="password" autoFocus value={password} onChange={(e) => setPassword(e.target.value)} placeholder="密码" aria-label="密码" />
        {error && <p className="mt-2 text-sm text-danger">{error}</p>}
        <Button type="submit" variant="primary" className="mt-4 w-full" loading={loading}>
          {passwordSet ? '登录' : '设置密码并登录'}
        </Button>
      </form>
    </div>
  )
}
