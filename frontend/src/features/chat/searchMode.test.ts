import { toSearchMode, toggleSearchMode } from './searchMode'

test('maps empty search toggle to pure llm mode', () => {
  expect(toSearchMode(null)).toBe('none')
})

test('maps local and web toggles to backend search modes', () => {
  expect(toSearchMode('local')).toBe('local')
  expect(toSearchMode('web')).toBe('web')
})

test('keeps local and web mutually exclusive', () => {
  expect(toggleSearchMode(null, 'local')).toBe('local')
  expect(toggleSearchMode('local', 'local')).toBeNull()
  expect(toggleSearchMode('local', 'web')).toBe('web')
  expect(toggleSearchMode('web', 'local')).toBe('local')
})
