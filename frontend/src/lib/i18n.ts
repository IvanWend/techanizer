// UI chrome and the query menu, both locales. Agent prose and table labels come
// from the backend. Each menu item maps 1:1 to one analytics/vector_search call,
// so its params are fixed — a query the data can't answer simply isn't on the
// menu (DECISIONS: Agent "Menu-driven queries").

import type { MenuQuery } from './types'

export type Lang = 'en' | 'ru'
export type Option = { value: string; label: string }

type Messages = {
  intelligence: string
  analysis: string
  monthly: string
  run: string
  cancel: string
  complete: string
  thinking: string
  emptyTitle: string
  emptyHint: string
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

export type MenuData = {
  query: string
  scope: string
  seniority: string
  remote: string
  currency: string
  role: string
  describe: string
  any: string
  scopeOptions: Option[]
  seniorityOptions: Option[]
  remoteOptions: Option[]
  currencyOptions: string[]
  queryGroups: { group: string; items: { id: MenuQuery; label: string }[] }[]
  suggestions: { id: MenuQuery; label: string }[]
}

const en: Messages = {
  intelligence: 'TECH MARKET INTELLIGENCE',
  analysis: 'Analysis',
  monthly: 'Monthly report',
  run: 'Run query',
  cancel: 'Stop',
  complete: 'ANALYSIS · COMPLETE',
  thinking: 'Thinking…',
  emptyTitle: 'Ask about the job market',
  emptyHint: 'Choose a query, set its parameters, and run it against real postings from four boards.',
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
  run: 'Запустить',
  cancel: 'Стоп',
  complete: 'АНАЛИЗ · ГОТОВО',
  thinking: 'Анализируем…',
  emptyTitle: 'Спросите о рынке труда',
  emptyHint: 'Выберите запрос, задайте параметры и запустите его по реальным вакансиям из четырёх источников.',
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

export const menu: Record<Lang, MenuData> = {
  en: {
    query: 'QUERY',
    scope: 'Scope',
    seniority: 'Seniority',
    remote: 'Remote policy',
    currency: 'Currency',
    role: 'Role description',
    describe: 'Describe a role…',
    any: 'Any',
    scopeOptions: [
      { value: 'overall', label: 'Overall' },
      { value: 'seniority', label: 'By seniority' },
      { value: 'remote', label: 'By remote' },
    ],
    seniorityOptions: [
      { value: 'intern', label: 'Intern' },
      { value: 'junior', label: 'Junior' },
      { value: 'mid', label: 'Mid' },
      { value: 'senior', label: 'Senior' },
      { value: 'staff+', label: 'Staff+' },
    ],
    remoteOptions: [
      { value: 'remote', label: 'Remote' },
      { value: 'hybrid', label: 'Hybrid' },
      { value: 'onsite', label: 'Onsite' },
    ],
    currencyOptions: ['USD', 'RUB', 'EUR', 'GBP', 'CAD', 'AUD'],
    queryGroups: [
      { group: 'SKILLS', items: [{ id: 'skills', label: 'Most-common skills' }] },
      {
        group: 'SALARIES',
        items: [
          { id: 'salary_by_seniority', label: 'Salary by seniority' },
          { id: 'salary_by_currency', label: 'Salary by currency' },
        ],
      },
      { group: 'ROLES', items: [{ id: 'top_salaries', label: 'Highest-paid roles' }] },
      { group: 'SEARCH', items: [{ id: 'similar_roles', label: 'Similar roles' }] },
    ],
    suggestions: [
      { id: 'skills', label: 'Top skills overall' },
      { id: 'top_salaries', label: 'Highest-paid roles' },
      { id: 'similar_roles', label: 'Find similar roles' },
    ],
  },
  ru: {
    query: 'ЗАПРОС',
    scope: 'Срез',
    seniority: 'Уровень',
    remote: 'Формат работы',
    currency: 'Валюта',
    role: 'Описание роли',
    describe: 'Опишите роль…',
    any: 'Любой',
    scopeOptions: [
      { value: 'overall', label: 'В целом' },
      { value: 'seniority', label: 'По уровню' },
      { value: 'remote', label: 'По формату' },
    ],
    seniorityOptions: [
      { value: 'intern', label: 'Intern' },
      { value: 'junior', label: 'Junior' },
      { value: 'mid', label: 'Mid' },
      { value: 'senior', label: 'Senior' },
      { value: 'staff+', label: 'Staff+' },
    ],
    remoteOptions: [
      { value: 'remote', label: 'Удалённо' },
      { value: 'hybrid', label: 'Гибрид' },
      { value: 'onsite', label: 'Офис' },
    ],
    currencyOptions: ['USD', 'RUB', 'EUR', 'GBP', 'CAD', 'AUD'],
    queryGroups: [
      { group: 'НАВЫКИ', items: [{ id: 'skills', label: 'Самые частые навыки' }] },
      {
        group: 'ЗАРПЛАТЫ',
        items: [
          { id: 'salary_by_seniority', label: 'Зарплата по уровню' },
          { id: 'salary_by_currency', label: 'Зарплата по валютам' },
        ],
      },
      { group: 'РОЛИ', items: [{ id: 'top_salaries', label: 'Самые высокооплачиваемые роли' }] },
      { group: 'ПОИСК', items: [{ id: 'similar_roles', label: 'Похожие роли' }] },
    ],
    suggestions: [
      { id: 'skills', label: 'Популярные навыки' },
      { id: 'top_salaries', label: 'Самые высокие зарплаты' },
      { id: 'similar_roles', label: 'Похожие роли' },
    ],
  },
}
