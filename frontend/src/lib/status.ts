// What the user sees while a tool runs. Tool names never cross the UI.
const STATUS: Record<'en' | 'ru', Record<string, string>> = {
  en: {
    vector_search: 'Searching postings…',
    sql_query: 'Crunching the numbers…',
    role_detail: 'Reading a posting…',
  },
  ru: {
    vector_search: 'Ищем вакансии…',
    sql_query: 'Считаем показатели…',
    role_detail: 'Читаем вакансию…',
  },
}

export function statusFor(toolName: string, lang: 'en' | 'ru' = 'en'): string {
  return STATUS[lang][toolName] ?? (lang === 'ru' ? 'Работаем…' : 'Working…')
}
