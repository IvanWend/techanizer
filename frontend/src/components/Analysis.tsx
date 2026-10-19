import { useEffect, useRef, useState } from 'react'
import { Icon } from './Icon'
import { ContractAnswer } from './contract'
import { streamAnswer, type StreamEvent } from '../lib/sse'
import { statusFor } from '../lib/status'
import { ui, questions, type Lang } from '../lib/i18n'
import type { Answer } from '../lib/types'

export function Analysis({ lang }: { lang: Lang }) {
  const t = ui[lang]
  const [query, setQuery] = useState(t.defaultQuery)
  const [answer, setAnswer] = useState<Answer | null>(null)
  const [status, setStatus] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [streaming, setStreaming] = useState(false)
  const [railOpen, setRailOpen] = useState(true)
  const controllerRef = useRef<AbortController | null>(null)
  const previousLang = useRef(lang)

  // Keep the pre-filled default query in sync when the language toggles, but
  // only if the user hasn't typed their own question.
  useEffect(() => {
    const old = previousLang.current
    setQuery((current) => {
      if (current === ui[old].defaultQuery) return ui[lang].defaultQuery
      return current
    })
    previousLang.current = lang
  }, [lang])

  function handleEvent(e: StreamEvent) {
    switch (e.event) {
      case 'tool_call':
        setStatus(statusFor(e.data.name, lang))
        break
      case 'answer':
        setAnswer(e.data)
        setStatus(null)
        setStreaming(false)
        break
      case 'error':
        setError(e.data.message)
        setStatus(null)
        setStreaming(false)
        break
    }
  }

  function run(raw: string) {
    const text = raw.trim()
    if (!text || streaming) return
    setQuery(text)
    setAnswer(null)
    setError(null)
    setStatus(t.thinking)
    setStreaming(true)

    const controller = new AbortController()
    controllerRef.current = controller
    streamAnswer(text, lang, handleEvent, controller.signal).catch((err) => {
      if (controller.signal.aborted) return // cancel is a stop, not an error
      setError(err instanceof Error ? err.message : String(err))
      setStatus(null)
      setStreaming(false)
    })
  }

  function cancel() {
    controllerRef.current?.abort()
    controllerRef.current = null
    setStatus(null)
    setStreaming(false)
  }

  const suggestions = questions[lang].flatMap((section) => section.items)

  return (
    <div className="flex h-full flex-col">
      <div className="shrink-0 border-b border-[var(--border)] bg-[var(--surface)] px-3 py-3 sm:px-5">
        <div className="mx-auto flex max-w-[1180px] gap-2">
          <div className="relative flex min-w-0 flex-1 items-center border border-[var(--border)] bg-[var(--surface-raised)] focus-within:border-cyan-400/60">
            <span className="ml-3 text-[var(--text-tertiary)]">
              <Icon name="search" size={16} />
            </span>
            <input
              aria-label={t.ask}
              className="h-11 min-w-0 flex-1 bg-transparent px-3 text-[13px] text-[var(--text-primary)] outline-none placeholder:text-[var(--text-faint)]"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault()
                  run(query)
                }
              }}
              placeholder={t.ask}
            />
          </div>
          {streaming ? (
            <button
              type="button"
              className="flex h-11 items-center gap-2 border border-[var(--border)] px-4 text-[11px] font-semibold text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
              onClick={cancel}
            >
              {t.cancel}
              <Icon name="close" size={13} />
            </button>
          ) : (
            <button
              type="button"
              className="flex h-11 items-center gap-2 bg-cyan-300 px-4 text-[11px] font-semibold text-[var(--accent-contrast)] hover:bg-cyan-200 disabled:opacity-60"
              disabled={!query.trim()}
              onClick={() => run(query)}
            >
              {t.run}
              <Icon name="arrow" size={13} />
            </button>
          )}
        </div>
        <div className="mx-auto mt-2 flex h-4 max-w-[1180px] items-center gap-2 font-mono text-[9px] text-[var(--text-tertiary)]">
          {status && (
            <>
              <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-cyan-400" />
              <span>{status}</span>
            </>
          )}
          {answer && !status && !error && (
            <span className="text-[var(--accent-text)]">{t.complete}</span>
          )}
        </div>
      </div>

      <div className="flex min-h-0 flex-1">
        <aside
          className={`hidden shrink-0 border-r border-[var(--border)] bg-[var(--surface)] transition-all md:block ${
            railOpen ? 'w-[224px]' : 'w-11'
          }`}
        >
          <div className="flex h-10 items-center justify-between border-b border-[var(--border-soft)] px-3">
            {railOpen && (
              <span className="font-mono text-[9px] tracking-[0.12em] text-[var(--text-faint)]">
                {t.suggestions}
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
            <div className="h-[calc(100%-40px)] overflow-y-auto px-3 py-4">
              {questions[lang].map((section) => (
                <section key={section.group} className="mb-6">
                  <h2 className="mb-2 font-mono text-[9px] tracking-[0.13em] text-[var(--text-faint)]">
                    {section.group}
                  </h2>
                  <div className="space-y-0.5">
                    {section.items.map((item) => (
                      <button
                        key={item}
                        type="button"
                        className="group flex w-full items-start gap-2 px-1 py-2 text-left text-[11px] leading-[1.4] text-[var(--text-secondary)] hover:bg-[var(--fill-hover)] hover:text-[var(--text-primary)]"
                        onClick={() => run(item)}
                      >
                        <span className="mt-0.5 text-[var(--text-faint)] group-hover:text-cyan-500">
                          <Icon name="chevron" size={11} />
                        </span>
                        {item}
                      </button>
                    ))}
                  </div>
                </section>
              ))}
            </div>
          )}
        </aside>

        <section className="min-w-0 flex-1 overflow-y-auto">
          <article className="mx-auto max-w-[820px] px-4 py-7 sm:px-8 sm:py-10">
            {error !== null ? (
              <div className="rounded-lg border border-red-500/30 bg-red-500/[0.06] px-4 py-4">
                <h2 className="text-[13px] font-semibold text-[var(--text-primary)]">
                  {t.errorTitle}
                </h2>
                <p className="mt-1.5 text-[12px] leading-5 text-[var(--text-secondary)]">
                  {t.errorHint}
                </p>
                <p className="mt-2 font-mono text-[10px] text-[var(--text-tertiary)]">
                  {error}
                </p>
              </div>
            ) : streaming && answer === null ? (
              <div className="space-y-6">
                <div className="h-5 w-2/3 animate-pulse bg-[var(--fill-soft)]" />
                <div className="h-3 w-full animate-pulse bg-[var(--fill-soft)]" />
                <div className="h-3 w-5/6 animate-pulse bg-[var(--fill-soft)]" />
                <div className="h-3 w-4/6 animate-pulse bg-[var(--fill-soft)]" />
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
                  {suggestions.map((item) => (
                    <button
                      key={item}
                      type="button"
                      className="rounded-full border border-[var(--border)] px-3 py-1.5 text-[11px] text-[var(--text-secondary)] hover:border-cyan-400/40 hover:text-[var(--text-primary)]"
                      onClick={() => run(item)}
                    >
                      {item}
                    </button>
                  ))}
                </div>
              </div>
            )}
          </article>
        </section>
      </div>
    </div>
  )
}
