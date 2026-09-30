import { Link } from 'react-router-dom'
import { useT } from '@/i18n'

export function NotFoundPage() {
  const t = useT()
  return (
    <div className="py-20 text-center">
      <p className="text-muted">{t('页面不存在')}</p>
      <Link to="/" className="mt-2 inline-block text-accent">{t('返回交易决策')}</Link>
    </div>
  )
}
