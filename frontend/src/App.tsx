import { useEffect, useState } from 'react'
import { Analysis } from './components/Analysis'
import { Dashboard } from './components/Dashboard'
import { LanguageToggle, ThemeToggle } from './components/chrome'
import { Icon } from './components/Icon'
import { ui, type Lang } from './lib/i18n'

type Screen = 'analysis' | 'dashboard'
type Theme = 'dark' | 'light'

function App() {
  const [lang, setLang] = useState<Lang>(() => {
    const saved = localStorage.getItem('techanizer-lang')
    return saved === 'ru' ? 'ru' : 'en'
  })
  const [screen, setScreen] = useState<Screen>('analysis')
  const [theme, setTheme] = useState<Theme>(() => {
    const saved = localStorage.getItem('techanizer-theme')
    if (saved === 'dark' || saved === 'light') return saved
    return window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark'
  })
  const t = ui[lang]

  useEffect(() => {
    localStorage.setItem('techanizer-theme', theme)
    document.documentElement.dataset.theme = theme
  }, [theme])

  useEffect(() => {
    localStorage.setItem('techanizer-lang', lang)
  }, [lang])

  return (
    <main
      className={`theme-${theme} flex h-dvh flex-col overflow-hidden bg-[var(--app-bg)] text-[var(--text-primary)]`}
    >
      <header className="flex h-12 shrink-0 items-center justify-between border-b border-[var(--border)] bg-[var(--surface)] px-3 sm:px-5">
        <div className="flex h-full items-center gap-3">
          <div className="flex h-5 w-5 items-center justify-center bg-cyan-300 text-[var(--accent-contrast)]">
            <Icon name="bolt" size={13} />
          </div>
          <span className="text-[13px] font-semibold">Techanizer</span>
          <span className="hidden h-4 w-px bg-[var(--border)] lg:block" />
          <span className="hidden font-mono text-[9px] tracking-[0.1em] text-[var(--text-faint)] xl:block">
            {t.intelligence}
          </span>
          <nav className="ml-1 flex h-full items-center sm:ml-4">
            <button
              type="button"
              className={`h-full border-b px-2 text-[10px] font-medium transition sm:px-3 ${
                screen === 'analysis'
                  ? 'border-cyan-400 text-[var(--text-primary)]'
                  : 'border-transparent text-[var(--text-tertiary)] hover:text-[var(--text-primary)]'
              }`}
              onClick={() => setScreen('analysis')}
            >
              {t.analysis}
            </button>
            <button
              type="button"
              className={`h-full border-b px-2 text-[10px] font-medium transition sm:px-3 ${
                screen === 'dashboard'
                  ? 'border-cyan-400 text-[var(--text-primary)]'
                  : 'border-transparent text-[var(--text-tertiary)] hover:text-[var(--text-primary)]'
              }`}
              onClick={() => setScreen('dashboard')}
            >
              {t.dashboard}
            </button>
          </nav>
        </div>
        <div className="flex items-center gap-2">
          <LanguageToggle lang={lang} onChange={setLang} />
          <ThemeToggle theme={theme} onToggle={() => setTheme(theme === 'dark' ? 'light' : 'dark')} />
        </div>
      </header>
      <div className="min-h-0 flex-1">
        {screen === 'analysis' ? (
          <Analysis lang={lang} />
        ) : (
          <Dashboard lang={lang} />
        )}
      </div>
    </main>
  )
}

export default App
