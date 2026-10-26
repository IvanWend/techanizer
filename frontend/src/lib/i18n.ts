// UI chrome and the suggested queries, both locales. Agent prose and table
// labels come from the backend. Every suggestion is a free-text query that fills
// the input and maps to one sql_query / vector_search — a question the tools
// cannot answer (geography, trends, role-scoped skills) makes the agent loop
// until it hits the tool-call cap.

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
    'Ask about skills, salaries and roles — answered from real postings on HN "Who is Hiring", Remotive, Web3.career and Habr Career over a rolling 90-day window.',
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
  emptyHint:
    'Спрашивайте о навыках, зарплатах и ролях — ответы строятся на реальных вакансиях HN "Who is Hiring", Remotive, Web3.career и Habr Career за последние 90 дней.',
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
    {
      group: 'SKILLS',
      items: [
        'How many postings mention Python, and how many SQL?',
        'How many postings mention PyTorch, and how many TensorFlow?',
        'Which appears most often — Airflow, Docker, Kubernetes or MLflow?',
      ],
    },
    {
      group: 'SALARIES',
      items: [
        'Median USD salary by level (intern, junior, mid, senior, staff+)',
        'Median USD salary for senior roles',
        'Which currency has the most roles with a stated salary?',
        'How many roles state a salary in USD?',
      ],
    },
    {
      group: 'ROLES',
      items: [
        'Highest-paid roles in USD',
        'Which posting has the highest salary in USD?',
        'Find a remote MLOps engineer role and name the company',
        'Which company posted the closest match to "LLM engineer"?',
      ],
    },
  ],
  ru: [
    {
      group: 'НАВЫКИ',
      items: [
        'Сколько вакансий упоминают Python, а сколько SQL?',
        'Сколько вакансий упоминают PyTorch, а сколько TensorFlow?',
        'Какой из инструментов — Airflow, Docker, Kubernetes или MLflow — упоминается чаще всего?',
      ],
    },
    {
      group: 'ЗАРПЛАТЫ',
      items: [
        'Медианная зарплата в RUB по уровню (intern, junior, mid, senior, staff+)',
        'Какая медианная зарплата у senior-ролей в RUB?',
        'В какой валюте указано больше всего ролей с зарплатой?',
        'Сколько ролей указывают зарплату в RUB?',
      ],
    },
    {
      group: 'РОЛИ',
      items: [
        'Самые высокооплачиваемые роли в RUB',
        'У какой вакансии самая высокая зарплата в RUB?',
        'Найди удалённую вакансию MLOps-инженера и назови компанию',
        'Какая компания разместила ближайшее совпадение с "LLM engineer"?',
      ],
    },
  ],
}
