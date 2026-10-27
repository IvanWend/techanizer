import type { ReactNode } from 'react'

export type IconName =
  | 'arrow'
  | 'bolt'
  | 'chevron'
  | 'close'
  | 'external'
  | 'menu'
  | 'moon'
  | 'panel'
  | 'search'
  | 'sun'

const paths: Record<IconName, ReactNode> = {
  arrow: <path d="m5 12 7-7 7 7M12 5v14" />,
  bolt: <path d="m13 2-8 11h7l-1 9 8-12h-7l1-8Z" />,
  chevron: <path d="m9 18 6-6-6-6" />,
  close: <path d="m6 6 12 12M18 6 6 18" />,
  external: <path d="M14 5h5v5M10 14 19 5M19 14v5H5V5h5" />,
  menu: <path d="M4 7h16M4 12h16M4 17h16" />,
  moon: <path d="M20.2 15.1A8.5 8.5 0 0 1 8.9 3.8 8.5 8.5 0 1 0 20.2 15.1Z" />,
  panel: <path d="M4 5h16v14H4zM9 5v14" />,
  search: <path d="m20 20-4.5-4.5M18 11a7 7 0 1 1-14 0 7 7 0 0 1 14 0Z" />,
  sun: (
    <>
      <circle cx="12" cy="12" r="3.5" />
      <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
    </>
  ),
}

export function Icon({ name, size = 16 }: { name: IconName; size?: number }) {
  return (
    <svg
      aria-hidden="true"
      fill="none"
      height={size}
      viewBox="0 0 24 24"
      width={size}
      stroke="currentColor"
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeWidth="1.7"
    >
      {paths[name]}
    </svg>
  )
}
