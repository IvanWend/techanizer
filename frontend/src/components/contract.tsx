import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { ui, type Lang } from '../lib/i18n'
import { makeHighlightPlugin } from '../lib/highlight'
import type { Answer, Cell, Quote, Table } from '../lib/types'

// Display caps are UI law: the model is prompted to cap too, but the render is
// the last line of defence against a 20-row table or a chip wall.
const MAX_BARS = 10
const MAX_QUOTES = 5
const MAX_CHIPS = 8

const CURRENCY_SYMBOL: Record<string, string> = {
  USD: '$',
  EUR: '€',
  RUB: '₽',
  GBP: '£',
  CAD: 'C$',
  AUD: 'A$',
}

function fmtInt(n: number | null | undefined, lang: Lang): string | null {
  if (n === null || n === undefined) return null
  return new Intl.NumberFormat(lang === 'ru' ? 'ru-RU' : 'en-US').format(n)
}

function currencySymbol(code: string | null | undefined): string {
  if (!code) return ''
  return CURRENCY_SYMBOL[code] ?? `${code} `
}

type SalaryFields = {
  salary_min?: number | null
  salary_max?: number | null
  salary_currency?: string | null
}

// The boards store absent enums as the string "unknown", not null, so truthiness
// alone would render an "UNKNOWN" badge.
function isBlank(value: string | null | undefined): boolean {
  return !value || value.trim() === '' || value.toLowerCase() === 'unknown'
}

function salaryLine(fields: SalaryFields, lang: Lang, monthly: string): string | null {
  const sym = currencySymbol(fields.salary_currency)
  const lo = fmtInt(fields.salary_min, lang)
  const hi = fmtInt(fields.salary_max, lang)
  if (lo !== null && hi !== null && lo !== hi) return `${sym}${lo}–${hi} ${monthly}`
  if (hi !== null) return `${sym}${hi} ${monthly}`
  if (lo !== null) return `${sym}${lo}+ ${monthly}`
  return null
}

// The inline-highlight vocabulary: quotes' stacks plus the first column of every
// bars table (the skill names). This is what the prose may mention, expanded
// through the surface-form dictionary in lib/highlight.ts.
function collectSkills(answer: Answer): string[] {
  const skills = new Set<string>()
  for (const q of answer.quotes) {
    if (Array.isArray(q.stack)) {
      for (const s of q.stack) if (typeof s === 'string') skills.add(s)
    }
  }
  for (const t of answer.tables) {
    if (t.kind !== 'bars') continue
    for (const row of t.rows) {
      const first = row[0]
      if (typeof first === 'string') skills.add(first)
    }
  }
  return Array.from(skills)
}

function fmtCell(cell: Cell, lang: Lang): string {
  if (cell === null || cell === undefined) return '—'
  if (typeof cell === 'number') return fmtInt(cell, lang) ?? '—'
  return cell
}

function Bars({ rows, lang }: { rows: Cell[][]; lang: Lang }) {
  const data = rows.slice(0, MAX_BARS).map((row, index) => ({
    key: `${String(row[0] ?? '')}-${index}`,
    label: typeof row[0] === 'string' ? row[0] : String(row[0] ?? '—'),
    value: Number(row[1] ?? 0),
  }))
  const max = Math.max(1, ...data.map((d) => d.value))
  return (
    <div className="space-y-3 p-4">
      {data.map((row, index) => (
        <div key={row.key} className="grid grid-cols-[20px_90px_1fr_44px] items-center gap-2">
          <span className="font-mono text-[9px] text-[var(--text-faint)]">
            {String(index + 1).padStart(2, '0')}
          </span>
          <span className="truncate text-[11px] text-[var(--text-secondary)]">{row.label}</span>
          <div className="h-1.5 overflow-hidden bg-[var(--fill-soft)]">
            <div
              className="h-full bg-cyan-400/80"
              style={{ width: `${Math.round((row.value / max) * 100)}%` }}
            />
          </div>
          <span className="text-right font-mono text-[10px] text-[var(--text-tertiary)]">
            {fmtInt(row.value, lang)}
          </span>
        </div>
      ))}
    </div>
  )
}

function DataTable({ headers, rows, lang }: { headers: string[]; rows: Cell[][]; lang: Lang }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-left">
        {headers.length > 0 && (
          <thead>
            <tr className="border-b border-[var(--border)]">
              {headers.map((header, index) => (
                <th
                  key={`${header}-${index}`}
                  className={`px-3 py-2 font-mono text-[9px] font-medium uppercase tracking-[0.08em] text-[var(--text-faint)] ${
                    index ? 'text-right' : ''
                  }`}
                >
                  {header}
                </th>
              ))}
            </tr>
          </thead>
        )}
        <tbody>
          {rows.map((row, rowIndex) => (
            <tr key={rowIndex} className="border-b border-[var(--border-soft)] last:border-0">
              {row.map((cell, cellIndex) => (
                <td
                  key={cellIndex}
                  className={`whitespace-nowrap px-3 py-2.5 font-mono text-[10px] ${
                    cellIndex ? 'text-right text-[var(--text-secondary)]' : 'text-[var(--text-primary)]'
                  }`}
                >
                  {fmtCell(cell, lang)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function TablePanel({ table, lang }: { table: Table; lang: Lang }) {
  return (
    <section className="border border-[var(--border)] bg-[var(--panel)]">
      <div className="border-b border-[var(--border)] px-4 py-3">
        <p className="text-[12px] font-semibold text-[var(--text-primary)]">{table.title}</p>
      </div>
      {table.kind === 'bars' ? (
        <Bars rows={table.rows} lang={lang} />
      ) : (
        <div className="p-3">
          <DataTable headers={table.headers} rows={table.rows} lang={lang} />
        </div>
      )}
    </section>
  )
}

function QuoteCard({ quote, lang }: { quote: Quote; lang: Lang }) {
  const t = ui[lang]
  const salary = salaryLine(quote, lang, t.perMonth)
  const seniority = isBlank(quote.seniority) ? null : quote.seniority
  const stack = (quote.stack ?? []).filter((s) => typeof s === 'string')
  const shown = stack.slice(0, MAX_CHIPS)
  const hidden = stack.length - shown.length
  return (
    <article id={`quote-${quote.id}`} className="border border-[var(--border)] bg-[var(--surface-raised)]">
      <div className="flex items-start justify-between gap-2 p-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="shrink-0 rounded-[3px] bg-cyan-400/15 px-1.5 py-0.5 font-mono text-[9px] text-[var(--accent-text)]">
              [{quote.id}]
            </span>
            <p className="truncate text-[12px] font-semibold text-[var(--text-primary)]">
              {quote.company ?? '—'}
            </p>
          </div>
          <p className="mt-0.5 truncate text-[11px] text-[var(--text-secondary)]">
            {quote.title ?? ''}
          </p>
        </div>
        {quote.source && (
          <span className="shrink-0 rounded-[3px] border border-[var(--border)] px-1.5 py-0.5 font-mono text-[9px] text-[var(--text-tertiary)]">
            {quote.source}
          </span>
        )}
      </div>
      <div className="space-y-2.5 border-t border-[var(--border-soft)] p-3">
        {seniority && (
          <span className="inline-block rounded-[3px] border border-[var(--border)] px-1.5 py-0.5 font-mono text-[9px] tracking-[0.04em] text-[var(--text-tertiary)]">
            {seniority.toUpperCase()}
          </span>
        )}
        <p className="font-mono text-[13px] font-medium text-[var(--text-primary)]">
          {salary ?? t.salaryNotStated}
        </p>
        {stack.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {shown.map((skill) => (
              <span
                key={skill}
                className="rounded-[3px] bg-[var(--fill-soft)] px-2 py-1 font-mono text-[10px] text-[var(--text-secondary)]"
              >
                {skill}
              </span>
            ))}
            {hidden > 0 && (
              <span className="rounded-[3px] px-1.5 py-1 font-mono text-[10px] text-[var(--text-tertiary)]">
                {t.more(hidden)}
              </span>
            )}
          </div>
        )}
        <blockquote className="border-l-2 border-[var(--border)] pl-3 text-[12px] italic leading-5 text-[var(--text-secondary)]">
          “{quote.text}”
        </blockquote>
      </div>
    </article>
  )
}

function scrollToQuote(id: number) {
  document.getElementById(`quote-${id}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
}

export { Bars, DataTable, TablePanel }

export function ContractAnswer({ answer, lang }: { answer: Answer; lang: Lang }) {
  const t = ui[lang]
  if (answer.refused) {
    // A refusal is a muted scope message, not an answer: no key_finding, tables
    // or quotes. The headline is the code-owned localized refusal text.
    return (
      <div className="mt-6 border border-[var(--border)] bg-[var(--panel)] px-5 py-6">
        <h1 className="text-[15px] font-medium leading-[1.4] text-[var(--text-secondary)]">
          {answer.headline}
        </h1>
      </div>
    )
  }
  const plugin = makeHighlightPlugin(collectSkills(answer))
  const quotes = answer.quotes.slice(0, MAX_QUOTES)
  return (
    <div className="mt-6 space-y-8">
      <h1 className="text-[17px] font-semibold leading-[1.35] text-[var(--text-primary)]">
        {answer.headline}
      </h1>

      {answer.key_finding && (
        <section className="border border-[var(--border)] bg-[var(--quote)]">
          <div className="border-b border-[var(--border-soft)] px-4 py-2">
            <p className="font-mono text-[9px] tracking-[0.12em] text-[var(--text-faint)]">
              {t.keyFinding}
            </p>
          </div>
          <div className="px-4 py-3">
            <div className="prose prose-sm max-w-none">
              <Markdown remarkPlugins={[remarkGfm]} rehypePlugins={[plugin]}>
                {answer.key_finding}
              </Markdown>
            </div>
            {quotes.length > 0 && (
              <div className="mt-3 flex flex-wrap gap-1.5">
                {quotes.map((q) => (
                  <button
                    key={q.id}
                    type="button"
                    onClick={() => scrollToQuote(q.id)}
                    className="rounded-[3px] bg-cyan-400/15 px-1.5 py-0.5 font-mono text-[10px] text-[var(--accent-text)] hover:bg-cyan-400/25"
                  >
                    [{q.id}]
                  </button>
                ))}
              </div>
            )}
          </div>
        </section>
      )}

      {answer.tables.map((table, index) => (
        <TablePanel key={index} table={table} lang={lang} />
      ))}

      {quotes.length > 0 && (
        <section>
          <div className="mb-3">
            <p className="font-mono text-[9px] tracking-[0.12em] text-[var(--text-faint)]">
              {t.evidence}
            </p>
          </div>
          <div className="grid gap-3">
            {quotes.map((q) => (
              <QuoteCard key={q.id} quote={q} lang={lang} />
            ))}
          </div>
        </section>
      )}
    </div>
  )
}
