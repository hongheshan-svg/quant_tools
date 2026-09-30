// 英文词典覆盖率静态检查：源码里所有 t('中文') 的 key 都必须在 EN 中，且词典条目本身合规
import { describe, expect, it } from 'vitest'
import { EN } from '@/i18n'

const sources = import.meta.glob('../**/*.{ts,tsx}', { query: '?raw', import: 'default', eager: true }) as Record<string, string>

const files = Object.entries(sources).filter(([p]) => !p.includes('/__tests__/') && !p.includes('/i18n/en/'))

// t( 前不能是标识符字符、点或 $（排除 split(、set( 等）；参数为单引号、双引号或不含 ${} 的反引号字面量
const CALL = /(?<![\w.$])t\(\s*(?:'((?:[^'\\\n]|\\.)*)'|"((?:[^"\\\n]|\\.)*)"|`((?:[^`\\$]|\\.|\$(?!\{))*)`)/g

function unescape(s: string): string {
  return s.replace(/\\(u[0-9a-fA-F]{4}|.)/g, (_, c: string) => {
    if (c.length === 5) return String.fromCharCode(parseInt(c.slice(1), 16))
    return c === 'n' ? '\n' : c === 't' ? '\t' : c
  })
}

function placeholders(s: string): string[] {
  return [...new Set([...s.matchAll(/\{(\w+)\}/g)].map((m) => m[1]))].sort()
}

function collectKeys(): { key: string; file: string }[] {
  const out: { key: string; file: string }[] = []
  for (const [file, code] of files) {
    for (const m of code.matchAll(CALL)) {
      const raw = m[1] ?? m[2] ?? m[3]
      // 只关心含中文的 key；纯英文/符号的调用（如其他名为 t 的函数）不要求翻译
      if (raw !== undefined && /[一-鿿]/.test(raw)) out.push({ key: unescape(raw), file })
    }
  }
  return out
}

describe('i18n coverage', () => {
  it('scans source files', () => {
    expect(files.length).toBeGreaterThan(10)
    expect(files.some(([p]) => p.includes('/i18n/en/'))).toBe(false)
  })

  it('every t() key has an English entry', () => {
    const missing = new Map<string, Set<string>>()
    for (const { key, file } of collectKeys()) {
      if (!(key in EN)) {
        if (!missing.has(key)) missing.set(key, new Set())
        missing.get(key)!.add(file.replace('../', 'src/'))
      }
    }
    const report = [...missing].map(([k, f]) => `${JSON.stringify(k)}  <- ${[...f].join(', ')}`)
    expect(report, `缺少英文翻译 ${report.length} 条：\n${report.join('\n')}`).toEqual([])
  })

  it('EN values are non-empty and contain no Chinese', () => {
    const bad = Object.entries(EN).filter(([, v]) => typeof v !== 'string' || !v.trim() || /[一-鿿]/.test(v)).map(([k]) => k)
    expect(bad, `以下条目为空或含中文：\n${bad.join('\n')}`).toEqual([])
  })

  it('EN placeholders match the key', () => {
    const bad = Object.entries(EN)
      .filter(([k, v]) => placeholders(k).join(',') !== placeholders(v).join(','))
      .map(([k, v]) => `${k} => ${v}`)
    expect(bad, `占位符不一致：\n${bad.join('\n')}`).toEqual([])
  })
})
