// 界面文字的中英文切换：以中文原文作为 key，英文词典按页面分组放在 ./en/*.ts（每个文件 export default { 中文: English }）。
// 默认中文；英文词典里找不到的文字原样显示中文。只翻译界面文字，AI 回答、报告、新闻等后端生成的内容不翻译。
import { useCallback } from 'react'
import { useLangStore, type Lang } from '@/stores/lang'

export type { Lang }
export type Vars = Record<string, string | number>

const modules = import.meta.glob<{ default: Record<string, string> }>('./en/*.ts', { eager: true })

/** 全部英文词典合并后的结果（后加载的文件覆盖同名 key） */
export const EN: Record<string, string> = Object.assign({}, ...Object.values(modules).map((m) => m.default))

function fill(text: string, vars?: Vars): string {
  return vars ? text.replace(/\{(\w+)\}/g, (all, name: string) => (name in vars ? String(vars[name]) : all)) : text
}

/** 按指定语言翻译；{name} 占位符用 vars 替换 */
export function translate(lang: Lang, text: string, vars?: Vars): string {
  return fill(lang === 'en' ? (EN[text] ?? text) : text, vars)
}

/** 非组件代码使用（读取当前语言，但不会随语言切换重新渲染） */
export function t(text: string, vars?: Vars): string {
  return translate(useLangStore.getState().lang, text, vars)
}

/** 组件内使用：const t = useT()，切换语言时组件重新渲染 */
export function useT(): (text: string, vars?: Vars) => string {
  const lang = useLangStore((s) => s.lang)
  return useCallback((text: string, vars?: Vars) => translate(lang, text, vars), [lang])
}

export function useLang(): [Lang, (lang: Lang) => void] {
  return [useLangStore((s) => s.lang), useLangStore((s) => s.setLang)]
}
