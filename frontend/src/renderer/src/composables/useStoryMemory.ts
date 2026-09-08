import { computed, ref, type Ref } from 'vue'
import {
  checkContinuity,
  digestBatch,
  digestChapter,
  getBibleHealth,
  getDigest,
  getNextChapterBrief,
  getStoryMemorySettings,
  getStorySoFar,
  listDigests,
  saveStoryMemorySettings,
  type BibleHealth,
  type ChapterDigest,
  type ContinuityReport,
  type DigestListItem,
  type NextChapterBrief,
  type StoryMemorySettings,
  type StorySoFar,
} from '@renderer/api/storyMemory'

export interface StoryMemoryCoverage {
  written: number[]
  digested: number[]
  missing: number[]
  stale: number[]
}

const EMPTY_COVERAGE: StoryMemoryCoverage = { written: [], digested: [], missing: [], stale: [] }

/** Chapters that still need work, in the order a batch would process them. */
export function pendingChapters(coverage: StoryMemoryCoverage): number[] {
  return Array.from(new Set([...coverage.missing, ...coverage.stale])).sort((a, b) => a - b)
}

/** 0..100 share of written chapters with a fresh digest. */
export function coveragePercent(coverage: StoryMemoryCoverage): number {
  if (!coverage.written.length) return 100
  const fresh = coverage.written.length - pendingChapters(coverage).filter(n => coverage.written.includes(n)).length
  return Math.round((100 * Math.max(0, fresh)) / coverage.written.length)
}

export function severityRank(severity: string): number {
  return { critical: 0, high: 1, medium: 2, low: 3, info: 4 }[severity as 'critical'] ?? 5
}

export function healthTagType(score: number): 'success' | 'warning' | 'danger' | 'info' {
  if (score >= 90) return 'success'
  if (score >= 60) return 'warning'
  if (score > 0) return 'danger'
  return 'info'
}

/** Generated types mark list fields optional; make every array present so templates stay simple. */
export type StorySoFarView = StorySoFar & Required<Pick<StorySoFar, 'digested_chapters' | 'missing_chapters' | 'stale_chapters' | 'tiers' | 'carry_forward' | 'dangling_hooks' | 'recent_knowledge'>>
export type BriefView = NextChapterBrief & Required<Pick<NextChapterBrief, 'must_address' | 'should_consider' | 'avoid' | 'rhythm_advice'>>
export type ReportView = ContinuityReport & Required<Pick<ContinuityReport, 'issues' | 'checks_run'>>

export function normalizeStorySoFar(s: StorySoFar): StorySoFarView {
  return {
    ...s,
    digested_chapters: s.digested_chapters || [],
    missing_chapters: s.missing_chapters || [],
    stale_chapters: s.stale_chapters || [],
    tiers: s.tiers || [],
    carry_forward: s.carry_forward || [],
    dangling_hooks: s.dangling_hooks || [],
    recent_knowledge: s.recent_knowledge || [],
  }
}

export function normalizeBrief(b: NextChapterBrief): BriefView {
  return { ...b, must_address: b.must_address || [], should_consider: b.should_consider || [], avoid: b.avoid || [], rhythm_advice: b.rhythm_advice || [] }
}

export function normalizeReport(r: ContinuityReport): ReportView {
  return { ...r, issues: r.issues || [], checks_run: r.checks_run || [] }
}

export function useStoryMemory(projectId: Ref<number | undefined | null>) {
  const loading = ref(false)
  const digests = ref<DigestListItem[]>([])
  const coverage = ref<StoryMemoryCoverage>({ ...EMPTY_COVERAGE })
  const storySoFar = ref<StorySoFarView | null>(null)
  const health = ref<BibleHealth | null>(null)
  const settings = ref<StoryMemorySettings | null>(null)
  const brief = ref<BriefView | null>(null)
  const selectedDigest = ref<ChapterDigest | null>(null)
  const lastReport = ref<ReportView | null>(null)
  const busy = ref<'' | 'digest' | 'batch' | 'check' | 'settings' | 'brief'>('')

  const pending = computed(() => pendingChapters(coverage.value))
  const percent = computed(() => coveragePercent(coverage.value))

  async function refresh(): Promise<void> {
    const pid = projectId.value
    if (!pid) return
    loading.value = true
    try {
      const [list, sofar, hlth, cfg] = await Promise.all([
        listDigests(pid),
        getStorySoFar({ project_id: pid }),
        getBibleHealth(pid),
        getStoryMemorySettings(pid),
      ])
      digests.value = list.items || []
      coverage.value = { ...EMPTY_COVERAGE, ...((list.coverage as unknown) as Partial<StoryMemoryCoverage>) }
      storySoFar.value = normalizeStorySoFar(sofar)
      health.value = hlth
      settings.value = cfg.settings
    } finally {
      loading.value = false
    }
  }

  async function openDigest(chapterNumber: number): Promise<ChapterDigest | null> {
    const pid = projectId.value
    if (!pid) return null
    selectedDigest.value = await getDigest(pid, chapterNumber)
    return selectedDigest.value
  }

  async function digestOne(chapterNumber: number, llmConfigId: number, force = false): Promise<void> {
    const pid = projectId.value
    if (!pid) return
    busy.value = 'digest'
    try {
      await digestChapter({ project_id: pid, llm_config_id: llmConfigId, chapter_number: chapterNumber, force })
      await refresh()
    } finally {
      busy.value = ''
    }
  }

  async function digestPending(llmConfigId: number, force = false) {
    const pid = projectId.value
    if (!pid) return null
    busy.value = 'batch'
    try {
      const res = await digestBatch({ project_id: pid, llm_config_id: llmConfigId, force, max_chapters: 200 })
      await refresh()
      return res
    } finally {
      busy.value = ''
    }
  }

  async function loadBrief(chapterNumber?: number | null, participants: string[] = [], pov?: string | null) {
    const pid = projectId.value
    if (!pid) return null
    busy.value = 'brief'
    try {
      brief.value = normalizeBrief(await getNextChapterBrief({ project_id: pid, chapter_number: chapterNumber ?? null, participants, pov: pov ?? null }))
      return brief.value
    } finally {
      busy.value = ''
    }
  }

  async function runCheck(args: { draft: string; chapterNumber?: number | null; participants?: string[]; pov?: string | null; outline?: Record<string, unknown> | null; llmConfigId?: number | null; useLlm?: boolean; temperature?: number; maxTokens?: number; timeout?: number }) {
    const pid = projectId.value
    if (!pid) return null
    busy.value = 'check'
    try {
      lastReport.value = normalizeReport(await checkContinuity({
        project_id: pid,
        draft: args.draft,
        chapter_number: args.chapterNumber ?? null,
        participants: args.participants || [],
        pov: args.pov ?? null,
        outline: (args.outline as any) ?? null,
        use_llm: !!args.useLlm,
        llm_config_id: args.useLlm ? args.llmConfigId ?? null : null,
        temperature: args.temperature ?? null,
        max_tokens: args.maxTokens ?? null,
        timeout: args.timeout ?? null,
      }))
      return lastReport.value
    } finally {
      busy.value = ''
    }
  }

  async function updateSettings(next: StoryMemorySettings) {
    const pid = projectId.value
    if (!pid) return
    busy.value = 'settings'
    try {
      const res = await saveStoryMemorySettings(pid, next)
      settings.value = res.settings
    } finally {
      busy.value = ''
    }
  }

  return {
    loading, busy, digests, coverage, pending, percent, storySoFar, health, settings, brief, selectedDigest, lastReport,
    refresh, openDigest, digestOne, digestPending, loadBrief, runCheck, updateSettings,
  }
}
