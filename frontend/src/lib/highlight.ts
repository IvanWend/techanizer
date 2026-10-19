// Rehype plugin that wraps vocabulary terms in <span class="kw"> so skill
// names stand out inline in the prose answer. It runs on parsed text nodes, so
// markdown syntax (tables, links, code) is already resolved and only real prose
// is matched. `code`/`pre` are skipped so `inline code` stays untouched.
//
// The vocabulary arrives as the DB's canonical (casefolded) skill ids while the
// prose uses surface forms, so each canonical is expanded to the surface
// variants worth matching: case-insensitive for safe terms (postgres also
// matches "PostgreSQL"), case-sensitive for ones that collide with common
// words (Go/React as verbs), which is why those two need their own pass.

type HastNode = {
  type: string
  value?: string
  tagName?: string
  properties?: Record<string, unknown>
  children?: HastNode[]
}

// Short English words that also happen to be skill names; highlighting them
// everywhere would be noisy even case-insensitively.
const STOPWORDS = new Set(['next'])

const SKIP_TAGS = new Set(['code', 'pre', 'kbd', 'samp'])

// canonical → extra surface forms, matched case-insensitively.
const SURFACE_CI: Record<string, string[]> = {
  postgres: ['postgresql'],
  kubernetes: ['k8s'],
  'ci-cd': ['ci/cd', 'cicd'],
  'rest-api': ['rest api', 'restapi'],
  'gitlab-ci': ['gitlab ci'],
  'github-actions': ['github actions'],
  mongodb: ['mongo'],
  elasticsearch: ['elastic'],
}

// canonical → surface forms matched case-sensitively: the lowercase forms are
// common English words, so only the capitalized surface form may highlight.
const SURFACE_CS: Record<string, string[]> = {
  go: ['Go', 'Golang'],
  react: ['React'],
}

function escapeRe(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

function buildRegex(variants: string[], flags: string): RegExp | null {
  if (variants.length === 0) return null
  const sorted = [...variants].sort((a, b) => b.length - a.length)
  return new RegExp(`\\b(${sorted.map(escapeRe).join('|')})\\b`, flags)
}

function wrap(
  node: HastNode,
  re: RegExp,
  set: Set<string>,
  lower: boolean,
  skipKw: boolean,
): void {
  if (node.tagName && SKIP_TAGS.has(node.tagName)) return
  if (
    skipKw &&
    node.tagName === 'span' &&
    Array.isArray(node.properties?.className) &&
    (node.properties.className as string[]).includes('kw')
  ) {
    return
  }

  if (node.type === 'text' && typeof node.value === 'string') {
    const parts = node.value.split(re)
    if (parts.length === 1) return
    node.type = 'element'
    node.tagName = 'span'
    node.properties = {}
    node.children = parts
      .filter((p) => p !== '')
      .map((p) =>
        set.has(lower ? p.toLowerCase() : p)
          ? {
              type: 'element',
              tagName: 'span',
              properties: { className: ['kw'] },
              children: [{ type: 'text', value: p }],
            }
          : { type: 'text', value: p },
      )
    return
  }

  if (node.children) for (const child of node.children) wrap(child, re, set, lower, skipKw)
}

export function makeHighlightPlugin(terms: string[]): () => (tree: HastNode) => void {
  const raw = Array.from(
    new Set(
      terms
        .map((t) => t.trim())
        .filter((t) => t !== '' && !STOPWORDS.has(t.toLowerCase())),
    ),
  )
  const ciVariants = raw
    .flatMap((t) => {
      // Hyphenated canonicals ("react-native") are written with a space in
      // prose ("React Native"), so emit the space-joined form too.
      const spaced = t.includes('-') ? t.replaceAll('-', ' ') : null
      return [t, ...(SURFACE_CI[t] ?? []), ...(spaced ? [spaced] : [])]
    })
    .filter((v) => v.length >= 3)
  const csVariants = raw.flatMap((t) => SURFACE_CS[t] ?? [])

  const ciRe = buildRegex(ciVariants, 'gi')
  const csRe = buildRegex(csVariants, 'g')
  const ciSet = new Set(ciVariants.map((v) => v.toLowerCase()))
  const csSet = new Set(csVariants)

  if (ciRe === null && csRe === null) return () => () => undefined

  return () => (tree) => {
    if (ciRe !== null) wrap(tree, ciRe, ciSet, true, false)
    if (csRe !== null) wrap(tree, csRe, csSet, false, true)
  }
}
