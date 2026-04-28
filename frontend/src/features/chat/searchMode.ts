import type { SearchMode } from '../../types'

export type SearchToggle = 'local' | 'web' | null

export function toSearchMode(toggle: SearchToggle): SearchMode {
  if (toggle === 'local') return 'local'
  if (toggle === 'web') return 'web'
  return 'none'
}

export function toggleSearchMode(current: SearchToggle, next: 'local' | 'web'): SearchToggle {
  return current === next ? null : next
}
