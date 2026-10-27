// What the user sees while the agent works. Tool names never cross the UI;
// `prose` is the stage-2 phase, not a tool, keyed here so one lookup covers
// both.
const STATUS: Record<'en' | 'ru', Record<string, string>> = {
  en: {
    vector_search: 'Searching postings…',
    sql_query: 'Crunching the numbers…',
    role_detail: 'Reading a posting…',
    prose: 'Writing the answer…',
  },
  ru: {
    vector_search: 'Ищем вакансии…',
    sql_query: 'Считаем показатели…',
    role_detail: 'Читаем вакансию…',
    prose: 'Пишем ответ…',
  },
}

export function statusFor(toolName: string, lang: 'en' | 'ru' = 'en'): string {
  return STATUS[lang][toolName] ?? (lang === 'ru' ? 'Работаем…' : 'Working…')
}
