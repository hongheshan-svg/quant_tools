import { Link } from 'react-router-dom'

export function NotFoundPage() {
  return (
    <div className="py-20 text-center">
      <p className="text-muted">页面不存在</p>
      <Link to="/" className="mt-2 inline-block text-accent">返回交易决策</Link>
    </div>
  )
}
