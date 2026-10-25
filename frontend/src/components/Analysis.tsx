import { useRef, useState } from 'react'
import { Icon } from './Icon'
import { ContractAnswer } from './contract'
import { streamAnswer, type StreamEvent } from '../lib/sse'
import { menu, ui, type Lang, type Option } from '../lib/i18n'
import type { Answer, MenuQuery, Remote, Seniority } from '../lib/types'

// UsageLimitExceeded's message includes a pydantic docs URL. The card already
// shows errorHint; don't also print the raw dump. Matches the sanitized frame
// and a stale server that still sends the URL.
function showRawError(message: string): boolean {
  return !/tool_calls_limit|tool call limit exceeded|UsageLimitExceeded|ai\.pydantic\.dev/i.test(
    message,
  )
}

// A bordered dropdown: a micro-label over the selected label, a chevron, and a
// panel of options. `value` is the canonical token the backend expects; labels
// are localized by the caller.
function Dropdown({
  label,
  value,
  options,
  onChange,
}: {
  label: string
  value: string
  options: Option[]
  onChange: (value: string) => void
}) {
  const [open, setOpen] = useState(false)
  const selected = options.find((o) => o.value === value)
  return (
    <div className="relative min-w-[152px]">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
        className={`flex h-11 w-full items-center justify-between border bg-[var(--surface-raised)] px-3 text-left ${
          open ? 'border-cyan-400' : 'border-[var(--border)] hover:border-[var(--text-faint)]'
        }`}
      >
        <span>
          <span className="block font-mono text-[8px] uppercase tracking-[0.12em] text-[var(--text-faint)]">
            {label}
          </span>
          <span className="mt-0.5 block font-mono text-[10px] text-[var(--text-primary)]">
            {selected?.label ?? value}
          </span>
        </span>
        <span className={`text-[var(--text-tertiary)] transition ${open ? 'rotate-90' : ''}`}>
          <Icon name="chevron" size={12} />
        </span>
      </button>
      {open && (
        <div className="absolute left-0 top-[46px] z-30 w-full border border-[var(--border)] bg-[var(--surface-raised)] shadow-[0_12px_30px_rgba(0,0,0,.28)]">
          {options.map((option) => (
            <button
              key={option.value}
              type="button"
              onClick={() => {
                onChange(option.value)
                setOpen(false)
              }}
              className={`block w-full border-b border-[var(--border-soft)] px-3 py-2 text-left font-mono text-[10px] last:border-0 hover:bg-[var(--fill-hover)] hover:text-[var(--accent-text)] ${
                option.value === value ? 'text-[var(--accent-text)]' : 'text-[var(--text-secondary)]'
              }`}
            >
              {option.label}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

function QueryControls({
  query,
  lang,
  streaming,
  onRun,
  onCancel,
}: {
  query: MenuQuery
  lang: Lang
  streaming: boolean
  onRun: (params: Record<string, unknown>) => void
  onCancel: () => void
}) {
  const m = menu[lang]
  const t = ui[lang]
  const [scope, setScope] = useState<'overall' | 'seniority' | 'remote'>('overall')
  const [level, setLevel] = useState<Seniority>('senior')
  const [remote, setRemote] = useState<Remote>('remote')
  const [currency, setCurrency] = useState('USD')
  const [phrase, setPhrase] = useState('')
  const [sLevel, setSLevel] = useState<Seniority | ''>('')
  const [sRemote, setSRemote] = useState<Remote | ''>('')

  // The "Any" option renders as an empty value that the caller omits from params.
  const anyOption: Option = { value: '', label: m.any }

  function assemble(): Record<string, unknown> {
    if (query === 'skills') {
      const params: Record<string, unknown> = { scope }
      if (scope === 'seniority') params.seniority = level
      if (scope === 'remote') params.remote = remote
      return params
    }
    if (query === 'salary_by_seniority') return { currency }
    if (query === 'salary_by_currency') return {}
    if (query === 'top_salaries') return { currency }
    const params: Record<string, unknown> = { phrase }
    if (sLevel) params.seniority = [sLevel]
    if (sRemote) params.remote = [sRemote]
    return params
  }

  const canRun = query !== 'similar_roles' || phrase.trim().length > 0

  return (
    <div className="relative z-20 flex min-h-14 flex-wrap items-center gap-2 border-b border-[var(--border)] bg-[var(--surface)] px-3 py-2 sm:px-4">
      {query === 'skills' && (
        <>
          <Dropdown
            label={m.scope}
            value={scope}
            options={m.scopeOptions}
            onChange={(v) => setScope(v as 'overall' | 'seniority' | 'remote')}
          />
          {scope === 'seniority' && (
            <Dropdown label={m.seniority} value={level} options={m.seniorityOptions} onChange={(v) => setLevel(v as Seniority)} />
          )}
          {scope === 'remote' && (
            <Dropdown label={m.remote} value={remote} options={m.remoteOptions} onChange={(v) => setRemote(v as Remote)} />
          )}
        </>
      )}
      {(query === 'salary_by_seniority' || query === 'top_salaries') && (
        <Dropdown
          label={m.currency}
          value={currency}
          options={m.currencyOptions.map((c) => ({ value: c, label: c }))}
          onChange={setCurrency}
        />
      )}
      {query === 'similar_roles' && (
        <>
          <label className="flex h-11 min-w-[240px] flex-1 items-center border border-cyan-400 bg-[var(--surface-raised)] px-3 sm:max-w-[420px]">
            <span className="text-[var(--text-tertiary)]">
              <Icon name="search" size={15} />
            </span>
            <span className="ml-3 flex-1">
              <span className="block font-mono text-[8px] uppercase tracking-[0.12em] text-[var(--text-faint)]">
                {m.role}
              </span>
              <input
                autoFocus
                className="mt-0.5 w-full bg-transparent font-mono text-[10px] text-[var(--text-primary)] outline-none placeholder:text-[var(--text-faint)]"
                value={phrase}
                onChange={(e) => setPhrase(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && canRun) onRun(assemble())
                }}
                placeholder={m.describe}
              />
            </span>
          </label>
          <Dropdown label={m.seniority} value={sLevel} options={[anyOption, ...m.seniorityOptions]} onChange={(v) => setSLevel(v as Seniority | '')} />
          <Dropdown label={m.remote} value={sRemote} options={[anyOption, ...m.remoteOptions]} onChange={(v) => setSRemote(v as Remote | '')} />
        </>
      )}
      {streaming ? (
        <button
          type="button"
          className="ml-auto flex h-11 items-center gap-2 border border-[var(--border)] px-4 text-[11px] font-semibold text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
          onClick={onCancel}
        >
          {t.cancel}
          <Icon name="close" size={13} />
        </button>
      ) : (
        <button
          type="button"
          className="ml-auto flex h-11 items-center gap-2 bg-cyan-300 px-4 text-[11px] font-semibold text-[var(--accent-contrast)] hover:bg-cyan-200 disabled:opacity-60"
          disabled={!canRun}
          onClick={() => onRun(assemble())}
        >
          {t.run}
          <Icon name="arrow" size={13} />
        </button>
      )}
    </div>
  )
}

export function Analysis({ lang }: { lang: Lang }) {
  const t = ui[lang]
  const m = menu[lang]
  const [query, setQuery] = useState<MenuQuery>('skills')
  const [answer, setAnswer] = useState<Answer | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [streaming, setStreaming] = useState(false)
  const [railOpen, setRailOpen] = useState(true)
  const controllerRef = useRef<AbortController | null>(null)

  function handleEvent(e: StreamEvent) {
    if (e.event === 'answer') {
      setAnswer(e.data)
      setError(null)
      setStreaming(false)
    } else {
      setError(e.data.message)
      setAnswer(null)
      setStreaming(false)
    }
  }

  function run(params: Record<string, unknown>) {
    if (streaming) return
    setAnswer(null)
    setError(null)
    setStreaming(true)
    const controller = new AbortController()
    controllerRef.current = controller
    streamAnswer(query, params, lang, handleEvent, controller.signal).catch((err) => {
      if (controller.signal.aborted) return // cancel is a stop, not an error
      setError(err instanceof Error ? err.message : String(err))
      setStreaming(false)
    })
  }

  function cancel() {
    controllerRef.current?.abort()
    controllerRef.current = null
    setStreaming(false)
  }

  function choose(id: MenuQuery) {
    controllerRef.current?.abort()
    setQuery(id)
    setAnswer(null)
    setError(null)
    setStreaming(false)
  }

  return (
    <div className="flex h-full flex-col">
      <QueryControls
        key={`${query}-${lang}`}
        query={query}
        lang={lang}
        streaming={streaming}
        onRun={run}
        onCancel={cancel}
      />

      <div className="flex min-h-0 flex-1">
        <aside
          className={`hidden shrink-0 border-r border-[var(--border)] bg-[var(--surface)] transition-all md:block ${
            railOpen ? 'w-[224px]' : 'w-11'
          }`}
        >
          <div className="flex h-10 items-center justify-between border-b border-[var(--border-soft)] px-3">
            {railOpen && (
              <span className="font-mono text-[9px] tracking-[0.12em] text-[var(--text-faint)]">
                {m.query}
              </span>
            )}
            <button
              type="button"
              className="ml-auto text-[var(--text-tertiary)] hover:text-cyan-500"
              onClick={() => setRailOpen(!railOpen)}
            >
              <Icon name={railOpen ? 'panel' : 'menu'} size={15} />
            </button>
          </div>
          {railOpen && (
            <nav className="px-3 py-4">
              {m.queryGroups.map((group) => (
                <section key={group.group} className="mb-6">
                  <h2 className="mb-2 font-mono text-[9px] tracking-[0.13em] text-[var(--text-faint)]">
                    {group.group}
                  </h2>
                  <div className="space-y-0.5">
                    {group.items.map((item) => (
                      <button
                        key={item.id}
                        type="button"
                        className={`group flex w-full items-center gap-2 px-1 py-2 text-left text-[11px] leading-[1.4] hover:bg-[var(--fill-hover)] ${
                          query === item.id ? 'text-[var(--text-primary)]' : 'text-[var(--text-secondary)]'
                        }`}
                        onClick={() => choose(item.id)}
                      >
                        <span className={query === item.id ? 'text-[var(--accent-text)]' : 'text-[var(--text-faint)]'}>
                          <Icon name="chevron" size={11} />
                        </span>
                        {item.label}
                      </button>
                    ))}
                  </div>
                </section>
              ))}
            </nav>
          )}
        </aside>

        <section className="min-w-0 flex-1 overflow-y-auto">
          {streaming && answer === null && error === null ? (
            <div className="flex h-full flex-col items-center justify-center gap-4 px-4 text-center">
              <span className="h-2.5 w-2.5 animate-pulse rounded-full bg-cyan-400" />
              <p className="text-[15px] font-medium tracking-[-0.01em] text-[var(--text-primary)]">
                {t.thinking}
              </p>
            </div>
          ) : (
            <article className="mx-auto max-w-[820px] px-4 py-7 sm:px-8 sm:py-10">
              {error !== null ? (
                <div className="rounded-lg border border-red-500/30 bg-red-500/[0.06] px-4 py-4">
                  <h2 className="text-[13px] font-semibold text-[var(--text-primary)]">
                    {t.errorTitle}
                  </h2>
                  <p className="mt-1.5 text-[12px] leading-5 text-[var(--text-secondary)]">
                    {t.errorHint}
                  </p>
                  {showRawError(error) && (
                    <p className="mt-2 font-mono text-[10px] text-[var(--text-tertiary)]">
                      {error}
                    </p>
                  )}
                </div>
              ) : answer !== null ? (
                <>
                  <div className="mb-6 flex items-center justify-between border-b border-[var(--border)] pb-4">
                    <p className="font-mono text-[9px] tracking-[0.12em] text-[var(--accent-text)]">
                      {t.complete}
                    </p>
                  </div>
                  <ContractAnswer answer={answer} lang={lang} />
                </>
              ) : (
                <div className="mx-auto flex max-w-xl flex-col items-center justify-center gap-6 py-16 text-center">
                  <div className="flex h-12 w-12 items-center justify-center border border-[var(--border)] text-cyan-500">
                    <Icon name="bolt" size={20} />
                  </div>
                  <div>
                    <h2 className="text-2xl font-semibold tracking-[-0.02em] text-[var(--text-primary)]">
                      {t.emptyTitle}
                    </h2>
                    <p className="mt-2 text-[13px] text-[var(--text-secondary)]">
                      {t.emptyHint}
                    </p>
                  </div>
                  <div className="flex flex-wrap justify-center gap-2">
                    {m.suggestions.map((item) => (
                      <button
                        key={item.id}
                        type="button"
                        className="rounded-full border border-[var(--border)] px-3 py-1.5 text-[11px] text-[var(--text-secondary)] hover:border-cyan-400/40 hover:text-[var(--text-primary)]"
                        onClick={() => choose(item.id)}
                      >
                        {item.label}
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </article>
          )}
        </section>
      </div>
    </div>
  )
}
