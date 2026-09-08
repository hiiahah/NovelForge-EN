/**
 * State behind the Forge automated-flow panel (Import -> Analyse -> Fingerprint ->
 * Foundation -> Bible -> Outlines -> Chapters).
 *
 * Kept free of Element Plus so the gating rules are unit-testable:
 * - the source side gates on evidence coverage and a non-stale fingerprint
 * - the original side gates on isolation, canon revision and stale dependencies
 * - a chapter run is only allowed for the manifest's next_allowed_chapter
 *   (or a regeneration of a committed chapter) and never while another run is active
 */
import { computed, ref, watch, type Ref } from 'vue'
import type { CompiledContext, CompileRequest, CreateOriginalRequest, IsolationReport, Manifest, PipelineRunSummary, RunChapterRequest, SourceStatus } from '@renderer/api/forge'

export interface ForgeApi {
  getSourceStatus: (projectId: number) => Promise<SourceStatus>
  verifySource: (projectId: number) => Promise<unknown>
  buildFingerprint: (projectId: number) => Promise<unknown>
  buildExamples: (projectId: number) => Promise<unknown>
  createOriginalProject: (body: CreateOriginalRequest) => Promise<{ project_id: number }>
  getIsolation: (projectId: number) => Promise<IsolationReport>
  seedCanon: (projectId: number) => Promise<{ facts_seeded: number }>
  getManifest: (projectId: number) => Promise<Manifest>
  compileChapter: (body: CompileRequest) => Promise<CompiledContext>
  runChapter: (body: RunChapterRequest) => Promise<Record<string, unknown>>
  listRuns: (projectId: number, limit?: number) => Promise<PipelineRunSummary[]>
}

export type ForgeStepKey = 'import' | 'analyze' | 'fingerprint' | 'foundation' | 'bible' | 'outlines' | 'chapters'
export const FORGE_STEPS: ForgeStepKey[] = ['import', 'analyze', 'fingerprint', 'foundation', 'bible', 'outlines', 'chapters']

function errorMessage(e: unknown): string {
  const anyE = e as any
  const detail = anyE?.response?.data?.detail
  if (detail && typeof detail === 'object') return detail.message || detail.code || JSON.stringify(detail)
  return detail || anyE?.message || String(e)
}

export function useForgePipeline(api: ForgeApi, projectId: Ref<number | undefined>) {
  const source = ref<SourceStatus | null>(null)
  const manifest = ref<Manifest | null>(null)
  const isolation = ref<IsolationReport | null>(null)
  const runs = ref<PipelineRunSummary[]>([])
  const busy = ref<string | null>(null)
  const error = ref<string | null>(null)
  const lastRun = ref<Record<string, unknown> | null>(null)
  const compiledContext = ref<CompiledContext | null>(null)
  let compileSequence = 0

  const isOriginal = computed(() => manifest.value?.project_role === 'original')
  const analysisComplete = computed(() => !!source.value && source.value.chapters > 0 && source.value.failed_chapters.length === 0 && source.value.analysed === source.value.chapters)
  const fingerprintReady = computed(() => !!source.value?.fingerprint && !source.value.fingerprint.stale)
  const examplesReady = computed(() => (source.value?.example_library?.examples || 0) > 0)
  const isolationOk = computed(() => isolation.value?.isolated === true)
  const staleCount = computed(() => manifest.value?.stale_dependency_count || 0)
  const nextChapter = computed(() => manifest.value?.next_allowed_chapter || 1)
  const lastSynced = computed(() => manifest.value?.latest_committed_chapter || 0)
  const activeRun = computed(() => busy.value === 'run')

  /** Highest step reached; steps stay reachable so status stays visible, but actions are gated. */
  const activeStep = computed(() => {
    if (isOriginal.value) {
      if (lastSynced.value > 0) return FORGE_STEPS.indexOf('chapters')
      if ((manifest.value?.canon_revision || 0) > 0) return FORGE_STEPS.indexOf('outlines')
      return FORGE_STEPS.indexOf('bible')
    }
    if (fingerprintReady.value && examplesReady.value) return FORGE_STEPS.indexOf('foundation')
    if (analysisComplete.value) return FORGE_STEPS.indexOf('fingerprint')
    if ((source.value?.chapters || 0) > 0) return FORGE_STEPS.indexOf('analyze')
    return FORGE_STEPS.indexOf('import')
  })

  /** Why a chapter cannot be generated right now; null when it can. */
  const chapterBlocker = computed<string | null>(() => {
    if (!isOriginal.value) return 'not_original_project'
    if (!isolationOk.value) return 'isolation_failed'
    if (staleCount.value > 0) return 'stale_dependencies'
    if (manifest.value?.unresolved_errors?.length) return 'unresolved_errors'
    if (activeRun.value) return 'run_active'
    return null
  })

  function canRunChapter(chapter: number, regenerate = false): boolean {
    if (chapterBlocker.value) return false
    if (regenerate) return chapter >= 1 && chapter <= lastSynced.value
    return chapter === nextChapter.value
  }

  async function refresh(): Promise<void> {
    compiledContext.value = null
    const pid = projectId.value
    if (!pid) return
    const [m, s, iso, r] = await Promise.allSettled([api.getManifest(pid), api.getSourceStatus(pid), api.getIsolation(pid), api.listRuns(pid, 20)])
    manifest.value = m.status === 'fulfilled' ? m.value : null
    source.value = s.status === 'fulfilled' ? s.value : null
    isolation.value = iso.status === 'fulfilled' ? iso.value : null
    runs.value = r.status === 'fulfilled' ? r.value : []
  }

  async function guarded<T>(label: string, fn: () => Promise<T>): Promise<T | null> {
    if (busy.value) return null
    busy.value = label
    error.value = null
    try {
      return await fn()
    } catch (e) {
      error.value = errorMessage(e)
      return null
    } finally {
      busy.value = null
      await refresh().catch(() => undefined)
    }
  }

  const verify = () => guarded('verify', () => api.verifySource(projectId.value!))
  const buildFingerprint = () => guarded('fingerprint', () => api.buildFingerprint(projectId.value!))
  const buildExamples = () => guarded('examples', () => api.buildExamples(projectId.value!))
  const seedCanon = () => guarded('seed', () => api.seedCanon(projectId.value!))
  const createOriginal = (name: string) => guarded('create', () => api.createOriginalProject({ source_project_id: projectId.value!, name, template: 'bible' }))
  function clearCompiledContext() {
    compileSequence += 1
    compiledContext.value = null
  }

  async function compile(chapter: number, regenerate = false): Promise<CompiledContext | null> {
    const pid = projectId.value
    if (!pid || busy.value) return null
    clearCompiledContext()
    const sequence = compileSequence
    const result = await guarded('compile', () => api.compileChapter({ project_id: pid, chapter_number: chapter, regenerate }))
    if (result && pid === projectId.value && sequence === compileSequence) compiledContext.value = result
    return result
  }

  watch(projectId, clearCompiledContext, { flush: 'sync' })

  async function run(chapter: number, llmConfigId: number, regenerate = false, craftPreset?: string): Promise<Record<string, unknown> | null> {
    if (!canRunChapter(chapter, regenerate)) {
      error.value = chapterBlocker.value || 'chapter_not_allowed'
      return null
    }
    const body: RunChapterRequest = { project_id: projectId.value!, chapter_number: chapter, llm_config_id: llmConfigId, regenerate }
    if (craftPreset) body.craft_preset = craftPreset
    const res = await guarded('run', () => api.runChapter(body))
    if (res) lastRun.value = res
    return res
  }

  return {
    source, manifest, isolation, runs, busy, error, lastRun, compiledContext,
    isOriginal, analysisComplete, fingerprintReady, examplesReady, isolationOk, staleCount, nextChapter, lastSynced, activeRun, activeStep, chapterBlocker,
    canRunChapter, refresh, verify, buildFingerprint, buildExamples, seedCanon, createOriginal, compile, clearCompiledContext, run,
  }
}
