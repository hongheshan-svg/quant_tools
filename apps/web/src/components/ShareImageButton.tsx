// 图片先准备，再由下一次用户点击触发系统分享，保留浏览器要求的用户激活状态。
import { useState } from 'react'
import { Button } from '@/components/ui'
import { useT } from '@/i18n'
import { toast } from '@/stores/toast'

export function ShareImageButton({ url, filename = 'research.png' }: { url: string; filename?: string }) {
  const t = useT()
  const [prepared, setPrepared] = useState<{ url: string; file: File } | null>(null)
  const [busy, setBusy] = useState(false)
  if (!navigator.share || !navigator.canShare) return <a className="inline-flex rounded-md border border-line px-3 py-1.5 text-sm" href={url} download={filename}>{t('下载分享图')}</a>
  const file = prepared?.url === url ? prepared.file : null
  const click = async () => {
    if (file && navigator.canShare({ files: [file] })) {
      // 必须在任何 await 之前调用 share。
      try { await navigator.share({ files: [file], title: t('研究报告') }) }
      catch (error) { if (!(error instanceof DOMException && error.name === 'AbortError')) toast.error(t('系统分享失败，请下载图片后分享')) }
      return
    }
    setBusy(true)
    try {
      const response = await fetch(url, { credentials: 'include' })
      if (!response.ok) throw new Error(t('生成分享图失败'))
      const blob = await response.blob()
      const image = new File([blob], filename, { type: blob.type || 'image/png' })
      if (navigator.canShare({ files: [image] })) {
        setPrepared({ url, file: image })
        toast.info(t('图片已准备，请再次点击分享'))
      } else {
        const objectUrl = URL.createObjectURL(blob)
        const anchor = document.createElement('a')
        anchor.href = objectUrl
        anchor.download = filename
        anchor.click()
        setTimeout(() => URL.revokeObjectURL(objectUrl), 1000)
      }
    } catch (error) { toast.error(error instanceof Error ? error.message : String(error)) }
    finally { setBusy(false) }
  }
  return <Button loading={busy} onClick={() => void click()}>{file ? t('分享图片') : t('准备分享图')}</Button>
}
