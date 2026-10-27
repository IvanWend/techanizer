import { useEffect, useState } from 'react'
import { TablePanel } from './contract'
import { Icon } from './Icon'
import { ui, type Lang } from '../lib/i18n'
import type { Table } from '../lib/types'

type DashboardData = {
  total_roles: number
  total_postings: number
  window_days: number
  tables: Table[]
}

function formatInt(n: number, lang: Lang): string {
  return new Intl.NumberFormat(lang === 'ru' ? 'ru-RU' : 'en-US').format(n)
}

function SummaryCard({ label, value, lang }: { label: string; value: number; lang: Lang }) {
  return (
    <div className="flex flex-col items-center border border-[var(--border)] bg-[var(--panel)] px-6 py-4">
      <span className="text-[26px] font-semibold tracking-[-0.02em] text-[var(--text-primary)]">
        {formatInt(value, lang)}
      </span>
      <span className="mt-1 font-mono text-[9px] tracking-[0.08em] text-[var(--text-faint)]">
        {label}
      </span>
    </div>
  )
}

function Skeleton() {
  return (
    <div className="space-y-6">
      <div className="grid grid-cols-3 gap-3">
        {[1, 2, 3].map((i) => (
          <div key={i} className="h-[88px] animate-pulse border border-[var(--border)] bg-[var(--panel)]" />
        ))}
      </div>
      <div className="h-[320px] animate-pulse border border-[var(--border)] bg-[var(--panel)]" />
      <div className="h-[200px] animate-pulse border border-[var(--border)] bg-[var(--panel)]" />
      <div className="h-[200px] animate-pulse border border-[var(--border)] bg-[var(--panel)]" />
      <div className="h-[200px] animate-pulse border border-[var(--border)] bg-[var(--panel)]" />
    </div>
  )
}

export function Dashboard({ lang }: { lang: Lang }) {
  const t = ui[lang]
  const [data, setData] = useState<DashboardData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    async function load() {
      setLoading(true)
      setError(null)
      try {
        const resp = await fetch(`/dashboard?lang=${lang}`)
        if (!resp.ok) throw new Error(`/dashboard: ${resp.status}`)
        const json = (await resp.json()) as DashboardData
        if (!cancelled) setData(json)
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err))
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    load()
    return () => {
      cancelled = true
    }
  }, [lang])

  return (
    <section className="h-full overflow-y-auto">
      <div className="mx-auto max-w-[1180px] px-4 py-7 sm:px-8 sm:py-10">
        <div className="mb-8 border-b border-[var(--border)] pb-5">
          <p className="mb-1.5 font-mono text-[9px] tracking-[0.12em] text-[var(--accent-text)]">
            {t.intelligence}
          </p>
          <h1 className="text-[26px] font-medium tracking-[-0.035em] text-[var(--text-primary)]">
            {t.dashboardTitle}
          </h1>
        </div>

        {loading ? (
          <Skeleton />
        ) : error !== null ? (
          <div className="flex min-h-[440px] items-center justify-center border border-[var(--border)] bg-[var(--panel)] text-center">
            <div className="max-w-sm px-6">
              <div className="mx-auto mb-5 flex h-10 w-10 items-center justify-center border border-[var(--border)] text-red-500">
                <Icon name="bolt" size={17} />
              </div>
              <h2 className="text-[17px] font-semibold text-[var(--text-primary)]">
                {t.errorTitle}
              </h2>
              <p className="mt-2 text-[12px] leading-5 text-[var(--text-secondary)]">{error}</p>
            </div>
          </div>
        ) : data !== null ? (
          <>
            <div className="mb-8 grid grid-cols-3 gap-3">
              <SummaryCard label={t.totalRoles} value={data.total_roles} lang={lang} />
              <SummaryCard label={t.totalPostings} value={data.total_postings} lang={lang} />
              <SummaryCard label={t.windowDays} value={data.window_days} lang={lang} />
            </div>
            <div className="space-y-6">
              {data.tables.map((table, index) => (
                <TablePanel key={index} table={table} lang={lang} />
              ))}
            </div>
          </>
        ) : null}
      </div>
    </section>
  )
}
