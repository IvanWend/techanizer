import { useEffect, useRef, useState } from 'react'
import { Icon } from './Icon'
import { ContractAnswer } from './contract'
import { streamAnswer, type StreamEvent } from '../lib/sse'
import { statusFor } from '../lib/status'
import { ui, questions, type Lang } from '../lib/i18n'
import type { Answer } from '../lib/types'

// UsageLimitExceeded's message includes a pydantic docs URL. The card already
// shows errorHint; don't also print the raw dump. Matches the sanitized frame
// and a stale server that still sends the URL.
function showRawError(message: string): boolean {
  return !/tool_calls_limit|tool call limit exceeded|UsageLimitExceeded|ai\.pydantic\.dev/i.test(
    message,
  )
}

export function Analysis({ lang }: { lang: Lang }) {
  const t = ui[lang]
  const [query, setQuery] = useState(t.defaultQuery)
  const [answer, setAnswer] = useState<Answer | null>(null)
  const [status, setStatus] = useState<string | null>(null)
  // One entry per tool_call, marked done by its tool_return — the run's
  // step checklist. `summary` is the human-readable phrase of what the
  // agent asked (e.g. "looking for 'remote MLOps engineer'"). Cleared when
  // the run ends.
  const [steps, setSteps] = useState<{ name: string; summary: string; done: boolean }[]>([])
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
        setSteps((prev) => [
          ...prev,
          { name: e.data.name, done: false, summary: e.data.summary },
        ])
        setStatus(e.data.summary)
        break
      case 'tool_return':
        // Calls pair FIFO: mark the first unresolved step with this tool name.
        setSteps((prev) => {
          const named = prev.findIndex((s) => !s.done && s.name === e.data.name)
          const idx = named >= 0 ? named : prev.findIndex((s) => !s.done)
          if (idx < 0) return prev
          return prev.map((s, i) => (i === idx ? { ...s, done: true } : s))
        })
        break
      case 'status':
        if (e.data.phase === 'prose') setStatus(statusFor('prose', lang))
        break
      case 'answer':
        setAnswer(e.data)
        setStatus(null)
        setSteps([])
        setStreaming(false)
        break
      case 'error':
        setError(e.data.message)
        setStatus(null)
        setSteps([])
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
    setSteps([])
    setStreaming(true)

    const controller = new AbortController()
    controllerRef.current = controller
    streamAnswer(text, lang, handleEvent, controller.signal).catch((err) => {
      if (controller.signal.aborted) return // cancel is a stop, not an error
      setError(err instanceof Error ? err.message : String(err))
      setStatus(null)
      setSteps([])
      setStreaming(false)
    })
  }

  function cancel() {
    controllerRef.current?.abort()
    controllerRef.current = null
    setStatus(null)
    setSteps([])
    setStreaming(false)
  }

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
          {streaming && answer === null && error === null ? (
            <div className="flex h-full flex-col items-center justify-center gap-4 px-4 text-center">
              <span className="h-2.5 w-2.5 animate-pulse rounded-full bg-cyan-400" />
              <p className="text-[15px] font-medium tracking-[-0.01em] text-[var(--text-primary)]">
                {status ?? t.thinking}
              </p>
              {steps.length > 0 && (
                <ol className="mt-1 space-y-1.5 text-left">
                  {steps.map((step, i) => (
                    <li
                      key={i}
                      className={`flex items-center gap-2 text-[12px] ${
                        step.done ? 'text-[var(--text-faint)]' : 'text-[var(--text-secondary)]'
                      }`}
                    >
                      {step.done ? (
                        <span className="text-cyan-500">
                          <Icon name="check" size={12} />
                        </span>
                      ) : (
                        <span className="h-2 w-2 animate-pulse rounded-full bg-cyan-400" />
                      )}
                      {step.summary || statusFor(step.name, lang)}
                    </li>
                  ))}
                </ol>
              )}
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
                </div>
              )}
            </article>
          )}
        </section>
      </div>
    </div>
  )
}
