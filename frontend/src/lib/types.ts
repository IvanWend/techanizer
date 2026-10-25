// The typed answer contract. The agent returns this object and the UI renders
// it and nothing else — no inference from raw tool JSON. Numbers are `string |
// number | null`: the contract lets a salary/count be a number, and null is the
// tool's own "no value" (rendered as an explicit absence, never a guess).

export type Cell = string | number | null

export type Table = {
  kind: 'bars' | 'table'
  title: string
  headers: string[]
  rows: Cell[][]
}

export type Quote = {
  id: number
  company: string | null
  title: string | null
  source: string
  seniority?: string | null
  stack?: string[]
  salary_min?: number | null
  salary_max?: number | null
  salary_currency?: string | null
  text: string
}

export type Answer = {
  headline: string
  key_finding: string
  tables: Table[]
  quotes: Quote[]
}

// The fixed query menu. Query names mirror the backend QueryName; params are
// the typed params each payload builder accepts.
export type MenuQuery =
  | 'skills'
  | 'salary_by_seniority'
  | 'salary_by_currency'
  | 'top_salaries'
  | 'similar_roles'

export type Seniority = 'intern' | 'junior' | 'mid' | 'senior' | 'staff+'
export type Remote = 'remote' | 'hybrid' | 'onsite'
