// UI chrome and the three suggested questions, both locales. Agent prose and
// table labels come from the backend. Each suggestion maps to one sql_query —
// a question the tools cannot answer (geography, trends, role-scoped skills)
// makes the agent loop until it hits the tool-call cap.

export type Lang = 'en' | 'ru'

type Messages = {
  intelligence: string
  analysis: string
  monthly: string
  ask: string
  defaultQuery: string
  run: string
  cancel: string
  complete: string
  thinking: string
  emptyTitle: string
  emptyHint: string
  suggestions: string
  errorTitle: string
  errorHint: string
  reportTitle: string
  generatedMonthly: string
  emptyText: string
  // Contract render chrome.
  keyFinding: string
  evidence: string
  salaryNotStated: string
  perMonth: string
  more: (n: number) => string
}

const en: Messages = {
  intelligence: 'TECH MARKET INTELLIGENCE',
  analysis: 'Analysis',
  monthly: 'Monthly report',
  ask: 'Ask the corpus…',
  defaultQuery: 'Which skills appear in the most roles?',
  run: 'Run query',
  cancel: 'Stop',
  complete: 'ANALYSIS · COMPLETE',
  thinking: 'Thinking…',
  emptyTitle: 'Ask about the job market',
  emptyHint:
    'Questions are answered by an agent over real job postings from four boards.',
  suggestions: 'SUGGESTED',
  errorTitle: 'Something went wrong',
  errorHint: 'The query could not be completed. Try again.',
  reportTitle: 'Monthly Report',
  generatedMonthly: 'Reports are generated monthly',
  emptyText:
    'The first report will appear here when a complete month of market data is available.',
  keyFinding: 'KEY FINDING',
  evidence: 'Evidence',
  salaryNotStated: 'Salary not stated',
  perMonth: '/mo',
  more: (n) => `+${n} more`,
}

const ru: Messages = {
  intelligence: 'АНАЛИТИКА ТЕХ-РЫНКА',
  analysis: 'Аналитика',
  monthly: 'Ежемесячный отчёт',
  ask: 'Задать вопрос по базе…',
  defaultQuery: 'Какие навыки упоминаются в наибольшем числе ролей?',
  run: 'Запустить',
  cancel: 'Стоп',
  complete: 'АНАЛИЗ · ГОТОВО',
  thinking: 'Анализируем…',
  emptyTitle: 'Спросите о рынке труда',
  emptyHint: 'Агент отвечает на вопросы по реальным вакансиям из четырёх источников.',
  suggestions: 'ПРЕДЛОЖЕННЫЕ',
  errorTitle: 'Что-то пошло не так',
  errorHint: 'Не удалось выполнить запрос. Попробуйте ещё раз.',
  reportTitle: 'Ежемесячный отчёт',
  generatedMonthly: 'Отчёты формируются раз в месяц',
  emptyText:
    'Первый отчёт появится здесь, когда будет доступен полный месяц рыночных данных.',
  keyFinding: 'КЛЮЧЕВОЙ ВЫВОД',
  evidence: 'Подтверждения',
  salaryNotStated: 'Зарплата не указана',
  perMonth: '/мес',
  more: (n) => `+${n} ещё`,
}

export const ui: Record<Lang, Messages> = { en, ru }

export const questions: Record<Lang, { group: string; items: string[] }[]> = {
  en: [
    { group: 'SKILLS', items: ['Which skills appear in the most roles?'] },
    { group: 'SALARIES', items: ['Median USD salary by seniority'] },
    { group: 'ROLES', items: ['Highest-paid roles in USD'] },
  ],
  ru: [
    { group: 'НАВЫКИ', items: ['Какие навыки упоминаются в наибольшем числе ролей?'] },
    { group: 'ЗАРПЛАТЫ', items: ['Медианная зарплата в USD по уровню'] },
    { group: 'РОЛИ', items: ['Самые высокооплачиваемые роли в USD'] },
  ],
}
