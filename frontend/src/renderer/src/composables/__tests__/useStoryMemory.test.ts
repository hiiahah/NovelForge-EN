import { describe, expect, it, vi } from 'vitest'

// The composable imports the HTTP client, which reads localStorage at import time; tests run under node.
vi.mock('@renderer/api/storyMemory', () => ({}))

import {
  coveragePercent,
  healthTagType,
  normalizeBrief,
  normalizeReport,
  normalizeStorySoFar,
  pendingChapters,
  severityRank,
} from '../useStoryMemory'

describe('useStoryMemory helpers', () => {
  it('pendingChapters merges missing and stale, sorted and unique', () => {
    expect(pendingChapters({ written: [1, 2, 3, 4], digested: [1, 2], missing: [4, 3], stale: [2, 3] })).toEqual([2, 3, 4])
    expect(pendingChapters({ written: [], digested: [], missing: [], stale: [] })).toEqual([])
  })

  it('coveragePercent counts only written chapters and treats empty books as complete', () => {
    expect(coveragePercent({ written: [], digested: [], missing: [], stale: [] })).toBe(100)
    expect(coveragePercent({ written: [1, 2, 3, 4], digested: [1, 2, 3, 4], missing: [], stale: [] })).toBe(100)
    expect(coveragePercent({ written: [1, 2, 3, 4], digested: [1, 2], missing: [3, 4], stale: [] })).toBe(50)
    expect(coveragePercent({ written: [1, 2, 3, 4], digested: [1, 2, 3, 4], missing: [], stale: [4] })).toBe(75)
    // A stale digest for a chapter that is no longer written does not reduce coverage.
    expect(coveragePercent({ written: [1, 2], digested: [1, 2, 9], missing: [], stale: [9] })).toBe(100)
  })

  it('severityRank orders critical first and unknown last', () => {
    const order = ['info', 'critical', 'medium', 'weird', 'high', 'low'].sort((a, b) => severityRank(a) - severityRank(b))
    expect(order).toEqual(['critical', 'high', 'medium', 'low', 'info', 'weird'])
  })

  it('healthTagType maps score bands', () => {
    expect(healthTagType(95)).toBe('success')
    expect(healthTagType(60)).toBe('warning')
    expect(healthTagType(10)).toBe('danger')
    expect(healthTagType(0)).toBe('info')
  })

  it('normalizers fill optional arrays so templates can index safely', () => {
    const s = normalizeStorySoFar({ project_id: 1, through_chapter: 3, next_chapter: 4 } as any)
    expect(s.tiers).toEqual([])
    expect(s.dangling_hooks).toEqual([])
    expect(s.missing_chapters).toEqual([])
    const b = normalizeBrief({ project_id: 1, chapter_number: 4, must_address: [{ kind: 'x', priority: 1, text: 't' }] } as any)
    expect(b.must_address).toHaveLength(1)
    expect(b.avoid).toEqual([])
    const r = normalizeReport({ project_id: 1, chapter_number: 4, checked_chars: 10, score: 100, verdict: 'clean' } as any)
    expect(r.issues).toEqual([])
    expect(r.checks_run).toEqual([])
  })
})
