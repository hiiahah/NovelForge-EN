/**
 * State behind the "Create Novel from EPUB" wizard.
 *
 * Kept free of Element Plus so the screen logic is unit-testable:
 * - the screen is derived from the job (upload -> analysis -> choose -> generating -> finished)
 * - polling runs only while the job is active and stops on terminal / waiting states
 * - selection requires a non-rejected option and a chapter count in a sane range
 */
import { computed, getCurrentInstance, onBeforeUnmount, ref } from 'vue'
import type { AutonomousJob, ChapterPreviewInfo, CreateJobRequest, ExportArtifactInfo, JobResponse, StorylineOption } from '@renderer/api/autonomous'

export interface AutonomousApi {
  createJob: (body: CreateJobRequest) => Promise<JobResponse>
  getJob: (jobId: number) => Promise<JobResponse>
  listJobs: (limit?: number) => Promise<Array<AutonomousJob & { active: boolean }>>
  listStorylines: (jobId: number, includeRejected?: boolean) => Promise<StorylineOption[]>
  selectStoryline: (jobId: number, body: { storyline_id: number; chapter_count: number; words_per_chapter?: number; title?: string }) => Promise<JobResponse>
  approveJob: (jobId: number) => Promise<JobResponse>
  pauseJob: (jobId: number) => Promise<JobResponse>
  resumeJob: (jobId: number) => Promise<JobResponse>
  cancelJob: (jobId: number) => Promise<JobResponse>
  listChapters: (jobId: number) => Promise<ChapterPreviewInfo[]>
  listArtifacts: (jobId: number) => Promise<ExportArtifactInfo[]>
  getReport: (jobId: number) => Promise<Record<string, any>>
  fileToBase64: (file: File) => Promise<string>
}

export type Screen = 'upload' | 'analysis' | 'choose' | 'generating' | 'finished'

export const ANALYSIS_STAGES = ['INGEST', 'SOURCE_ANALYSIS', 'ANALYSIS_VERIFICATION', 'BOOK_STRUCTURE', 'FINGERPRINT_BUILD', 'EXAMPLE_LIBRARY_BUILD', 'STORYLINE_GENERATION']
export const GENERATION_STAGES = ['NOVEL_ARCHITECTURE', 'BIBLE_BUILD', 'CHAPTER_PLAN_BUILD', 'NOVEL_PREFLIGHT', 'CHAPTER_GENERATION_LOOP', 'WHOLE_NOVEL_AUDIT', 'GLOBAL_REPAIR', 'EXPORT']
export const ACTIVE_STATUSES = new Set(['queued', 'running'])

export function screenFor(job: AutonomousJob | null): Screen {
  if (!job) return 'upload'
  if (job.status === 'completed') return 'finished'
  if (job.stage === 'STORYLINE_SELECTION' || (job.stage === 'STORYLINE_GENERATION' && job.status === 'waiting_for_user')) return 'choose'
  if (ANALYSIS_STAGES.includes(job.stage)) return 'analysis'
  if (job.stage === 'DONE') return 'finished'
  return 'generating'
}

function errorMessage(e: unknown): string {
  const anyE = e as any
  const detail = anyE?.response?.data?.detail
  if (detail && typeof detail === 'object') return detail.message || detail.code || JSON.stringify(detail)
  return detail || anyE?.message || String(e)
}

export function useAutonomousNovel(api: AutonomousApi, opts: { pollMs?: number } = {}) {
  const pollMs = opts.pollMs ?? 3000
  const job = ref<AutonomousJob | null>(null)
  const active = ref(false)
  const storylines = ref<StorylineOption[]>([])
  const chapters = ref<ChapterPreviewInfo[]>([])
  const artifacts = ref<ExportArtifactInfo[]>([])
  const report = ref<Record<string, any> | null>(null)
  const jobs = ref<Array<AutonomousJob & { active: boolean }>>([])
  const busy = ref<string | null>(null)
  const error = ref<string | null>(null)
  const file = ref<{ name: string; size: number; base64: string } | null>(null)
  const selectedStorylineId = ref<number | null>(null)
  const chapterCount = ref<number>(24)
  let timer: ReturnType<typeof setTimeout> | null = null

  const screen = computed<Screen>(() => screenFor(job.value))
  const isActive = computed(() => !!job.value && (active.value || ACTIVE_STATUSES.has(job.value.status)))
  const selectedOption = computed(() => storylines.value.find((o) => o.id === selectedStorylineId.value) || null)
  const acceptedOptions = computed(() => storylines.value.filter((o) => !o.rejected))
  const recommendedRange = computed(() => (selectedOption.value ? [selectedOption.value.recommended_chapters_min, selectedOption.value.recommended_chapters_max] : null))
  const chapterCountWarning = computed<string | null>(() => {
    const r = recommendedRange.value
    if (!r) return null
    if (chapterCount.value < r[0]) return 'below'
    if (chapterCount.value > r[1]) return 'above'
    return null
  })
  const canSelect = computed(() => !!selectedOption.value && !selectedOption.value.rejected && chapterCount.value >= 1 && chapterCount.value <= 400 && !busy.value)
  const estimatedWords = computed(() => chapterCount.value * Number((job.value?.options as any)?.words_per_chapter || 2500))

  function stopPolling() {
    if (timer) clearTimeout(timer)
    timer = null
  }

  function schedulePoll() {
    stopPolling()
    if (!job.value) return
    if (!isActive.value) return
    timer = setTimeout(() => void refresh(), pollMs)
  }

  async function applyResponse(res: JobResponse) {
    job.value = res.job
    active.value = res.active
    const s = screenFor(res.job)
    if (s === 'choose' && storylines.value.length === 0) storylines.value = await api.listStorylines(res.job.id, true)
    if (s === 'generating' || s === 'finished') {
      try { chapters.value = await api.listChapters(res.job.id) } catch { /* preview is optional */ }
    }
    if (s === 'finished') {
      try { artifacts.value = await api.listArtifacts(res.job.id) } catch { /* handled by refresh */ }
      try { report.value = await api.getReport(res.job.id) } catch { /* handled by refresh */ }
    }
    schedulePoll()
  }

  async function refresh() {
    if (!job.value) return
    try {
      await applyResponse(await api.getJob(job.value.id))
    } catch (e) {
      error.value = errorMessage(e)
      schedulePoll()
    }
  }

  async function loadJobs() {
    try { jobs.value = await api.listJobs(20) } catch (e) { error.value = errorMessage(e) }
  }

  async function open(jobId: number) {
    storylines.value = []
    chapters.value = []
    artifacts.value = []
    report.value = null
    error.value = null
    await applyResponse(await api.getJob(jobId))
    if (job.value?.selected_storyline_id) selectedStorylineId.value = job.value.selected_storyline_id
    if (job.value?.chapter_count) chapterCount.value = job.value.chapter_count
  }

  async function pickFile(f: File) {
    const base64 = await api.fileToBase64(f)
    file.value = { name: f.name, size: f.size, base64 }
  }

  async function start(params: Omit<CreateJobRequest, 'filename' | 'content_base64'>) {
    if (!file.value || busy.value) return
    busy.value = 'start'
    error.value = null
    try {
      storylines.value = []
      await applyResponse(await api.createJob({ ...params, filename: file.value.name, content_base64: file.value.base64 }))
    } catch (e) {
      error.value = errorMessage(e)
    } finally {
      busy.value = null
    }
  }

  async function reloadStorylines(includeRejected = true) {
    if (!job.value) return
    storylines.value = await api.listStorylines(job.value.id, includeRejected)
  }

  async function confirmSelection(extra: { words_per_chapter?: number; title?: string } = {}) {
    if (!job.value || !canSelect.value || !selectedStorylineId.value) return
    busy.value = 'select'
    error.value = null
    try {
      await applyResponse(await api.selectStoryline(job.value.id, { storyline_id: selectedStorylineId.value, chapter_count: chapterCount.value, ...extra }))
    } catch (e) {
      error.value = errorMessage(e)
    } finally {
      busy.value = null
    }
  }

  async function action(kind: 'approve' | 'pause' | 'resume' | 'cancel') {
    if (!job.value || busy.value) return
    busy.value = kind
    error.value = null
    try {
      const fn = { approve: api.approveJob, pause: api.pauseJob, resume: api.resumeJob, cancel: api.cancelJob }[kind]
      await applyResponse(await fn(job.value.id))
    } catch (e) {
      error.value = errorMessage(e)
    } finally {
      busy.value = null
    }
  }

  function reset() {
    stopPolling()
    job.value = null
    storylines.value = []
    chapters.value = []
    artifacts.value = []
    report.value = null
    selectedStorylineId.value = null
    error.value = null
  }

  if (getCurrentInstance()) onBeforeUnmount(stopPolling)

  return {
    job, active, storylines, chapters, artifacts, report, jobs, busy, error, file, selectedStorylineId, chapterCount,
    screen, isActive, selectedOption, acceptedOptions, recommendedRange, chapterCountWarning, canSelect, estimatedWords,
    pickFile, start, refresh, open, loadJobs, reloadStorylines, confirmSelection, action, reset, stopPolling,
  }
}
