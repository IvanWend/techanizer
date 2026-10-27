import { useEffect, useState } from 'react'
import { Analysis } from './components/Analysis'
import { LanguageToggle, ThemeToggle } from './components/chrome'
import { Icon } from './components/Icon'
import { ui, type Lang } from './lib/i18n'

type Theme = 'dark' | 'light'

function App() {
  const [lang, setLang] = useState<Lang>(() => {
    const saved = localStorage.getItem('techanizer-lang')
    return saved === 'ru' ? 'ru' : 'en'
  })
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
        </div>
        <div className="flex items-center gap-2">
          <LanguageToggle lang={lang} onChange={setLang} />
          <ThemeToggle theme={theme} onToggle={() => setTheme(theme === 'dark' ? 'light' : 'dark')} />
        </div>
      </header>
      <div className="min-h-0 flex-1">
        <Analysis lang={lang} />
      </div>
    </main>
  )
}

export default App
