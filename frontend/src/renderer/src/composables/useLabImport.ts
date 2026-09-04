/**
 * State machine behind the Lab import wizard.
 *
 * Kept free of Element Plus so the guards can be unit-tested:
 * - stale preview protection: only the newest preview request may update state
 * - rapid file selection protection: filename and bytes always belong together
 * - corrections addressed by stable section ids
 * - duplicate workflow launch prevention
 */
import { computed, reactive, ref, type Ref } from 'vue'
import type {
  ChapterPreview,
  LabRunStatus,
  ManuscriptImportResponse,
  ManuscriptListResponse,
  ManuscriptPreviewRequest,
  ManuscriptPreviewResponse,
  SectionCorrection,
} from '@renderer/api/lab'

export interface LabApi {
  previewManuscript: (body: ManuscriptPreviewRequest) => Promise<ManuscriptPreviewResponse>
  importManuscript: (body: any) => Promise<ManuscriptImportResponse>
  listManuscript: (projectId: number) => Promise<ManuscriptListResponse>
  startLabWorkflow: (body: { project_id: number; llm_config_id: number; analysis_concurrency?: number }) => Promise<LabRunStatus>
  listLabRuns: (projectId: number, limit?: number) => Promise<LabRunStatus[]>
  getLabRun: (runId: number) => Promise<LabRunStatus>
  cancelLabRun: (runId: number) => Promise<LabRunStatus>
  resumeLabRun: (runId: number) => Promise<LabRunStatus>
  fileToBase64: (file: File) => Promise<string>
}

export interface SelectedFile {
  name: string
  size: number
  base64: string
}

export const ACTIVE_RUN_STATUSES = new Set(['queued', 'running'])
export const RESUMABLE_RUN_STATUSES = new Set(['paused', 'failed', 'cancelled', 'timeout'])

export function useLabImport(api: LabApi, projectId: Ref<number | undefined>) {
  const step = ref(0)
  const file = ref<SelectedFile | null>(null)
  const previewing = ref(false)
  const importing = ref(false)
  const replaceExisting = ref(true)
  const preview = ref<ManuscriptPreviewResponse | null>(null)
  const previewError = ref<string | null>(null)
  const importResult = ref<ManuscriptImportResponse | null>(null)
  const manuscript = ref<ManuscriptListResponse | null>(null)
  const corrections = ref<SectionCorrection[]>([])
  const run = ref<LabRunStatus | null>(null)
  const runs = ref<LabRunStatus[]>([])
  const launching = ref(false)
  const runError = ref<string | null>(null)

  const meta = reactive({ book_title: '', author: '', genre: '', language: '' })
  const detect = reactive({
    encoding: '' as string | undefined,
    chapter_pattern: '' as string | undefined,
    volume_pattern: '' as string | undefined,
    exclude_front_matter: true,
    exclude_afterword: true,
    min_chapter_words: 80,
  })

  // Monotonic counters: a response is applied only if it belongs to the latest request.
  let fileSeq = 0
  let previewSeq = 0

  const analysedCount = computed(() => (manuscript.value?.chapters || []).filter((c: any) => c.analysis_status === 'done').length)
  const includedChapters = computed(() => (preview.value?.chapters || []).filter((c) => c.included))
  const excludedChapters = computed(() => (preview.value?.chapters || []).filter((c) => !c.included))
  const runActive = computed(() => !!run.value && ACTIVE_RUN_STATUSES.has(run.value.status))
  const runResumable = computed(() => !!run.value && RESUMABLE_RUN_STATUSES.has(run.value.status))

  async function setFile(f: File): Promise<boolean> {
    const mySeq = ++fileSeq
    const base64 = await api.fileToBase64(f)
    if (mySeq !== fileSeq) return false // a newer file was chosen while reading
    file.value = { name: f.name, size: f.size, base64 }
    if (!meta.book_title) meta.book_title = f.name.replace(/\.[^.]+$/, '')
    corrections.value = []
    preview.value = null
    previewError.value = null
    importResult.value = null
    return true
  }

  function payload(): ManuscriptPreviewRequest {
    return {
      filename: file.value?.name || 'manuscript.txt',
      content_base64: file.value?.base64 || '',
      encoding: detect.encoding || null,
      chapter_pattern: detect.chapter_pattern || null,
      volume_pattern: detect.volume_pattern || null,
      exclude_front_matter: detect.exclude_front_matter,
      exclude_afterword: detect.exclude_afterword,
      min_chapter_words: detect.min_chapter_words,
      corrections: corrections.value as any,
      preview_chars: 220,
    } as ManuscriptPreviewRequest
  }

  async function runPreview(): Promise<boolean> {
    if (!file.value) return false
    const mySeq = ++previewSeq
    const forFile = fileSeq
    previewing.value = true
    previewError.value = null
    try {
      const result = await api.previewManuscript(payload())
      if (mySeq !== previewSeq || forFile !== fileSeq) return false // stale response
      preview.value = result
      if (step.value < 1) step.value = 1
      return true
    } catch (e: any) {
      if (mySeq === previewSeq) previewError.value = e?.response?.data?.detail || e?.message || String(e)
      return false
    } finally {
      if (mySeq === previewSeq) previewing.value = false
    }
  }

  async function correct(op: SectionCorrection): Promise<boolean> {
    corrections.value = [...corrections.value, op]
    return runPreview()
  }

  function undoLastCorrection() {
    corrections.value = corrections.value.slice(0, -1)
    return runPreview()
  }

  function isExcluded(row: ChapterPreview) {
    return !row.included
  }

  async function runImport(): Promise<boolean> {
    if (!projectId.value || !preview.value || importing.value) return false
    importing.value = true
    try {
      importResult.value = await api.importManuscript({ ...payload(), project_id: projectId.value, ...meta, replace_existing: replaceExisting.value })
      step.value = 2
      await loadManuscript()
      return true
    } finally {
      importing.value = false
    }
  }

  async function loadManuscript() {
    if (!projectId.value) {
      manuscript.value = null
      return
    }
    manuscript.value = await api.listManuscript(projectId.value)
    if (manuscript.value?.chapters?.length && step.value < 2 && !file.value) step.value = 2
  }

  async function loadRuns() {
    if (!projectId.value) return
    runs.value = await api.listLabRuns(projectId.value, 10)
    const active = runs.value.find((r) => ACTIVE_RUN_STATUSES.has(r.status))
    run.value = active || runs.value[0] || null
    if (run.value && step.value < 3 && (run.value.status === 'succeeded' || ACTIVE_RUN_STATUSES.has(run.value.status))) step.value = 3
  }

  async function startRun(llmConfigId: number, concurrency = 2): Promise<LabRunStatus | null> {
    if (!projectId.value || launching.value) return null
    if (runActive.value) return run.value // duplicate launch guard (client side)
    launching.value = true
    runError.value = null
    try {
      run.value = await api.startLabWorkflow({ project_id: projectId.value, llm_config_id: llmConfigId, analysis_concurrency: concurrency })
      step.value = 3
      return run.value
    } catch (e: any) {
      runError.value = e?.response?.data?.detail || e?.message || String(e)
      return null
    } finally {
      launching.value = false
    }
  }

  async function refreshRun() {
    if (!run.value) return
    run.value = await api.getLabRun(run.value.run_id)
    if (run.value.status === 'succeeded') await loadManuscript()
  }

  async function cancelRun() {
    if (!run.value) return
    run.value = await api.cancelLabRun(run.value.run_id)
  }

  async function resumeRun() {
    if (!run.value || !runResumable.value) return
    runError.value = null
    try {
      run.value = await api.resumeLabRun(run.value.run_id)
    } catch (e: any) {
      runError.value = e?.response?.data?.detail || e?.message || String(e)
    }
  }

  return {
    step, file, previewing, importing, replaceExisting, preview, previewError, importResult, manuscript, corrections,
    run, runs, launching, runError, meta, detect, analysedCount, includedChapters, excludedChapters, runActive, runResumable,
    setFile, payload, runPreview, correct, undoLastCorrection, isExcluded, runImport, loadManuscript, loadRuns, startRun, refreshRun, cancelRun, resumeRun,
  }
}
