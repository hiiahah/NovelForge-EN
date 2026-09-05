import { describe, expect, it, vi } from 'vitest'
import type { AutonomousJob, StorylineOption } from '@renderer/api/autonomous'
import { screenFor, useAutonomousNovel, type AutonomousApi } from '../useAutonomousNovel'

function job(over: Partial<AutonomousJob> = {}): AutonomousJob {
  return {
    id: 1, status: 'queued', stage: 'INGEST', mode: 'fully_automatic', llm_config_id: 1, source_filename: 'book.epub', options: { words_per_chapter: 2000 }, chapter_count: 0, chapters_committed: 0,
    progress_percent: 0, progress_message: '', stage_results: {}, warnings: [], model_calls: 0, input_tokens: 0, output_tokens: 0, attempts: [], stages: ['INGEST', 'STORYLINE_SELECTION', 'CHAPTER_GENERATION_LOOP', 'EXPORT', 'DONE'], ...over,
  }
}
function option(id: number, over: Partial<StorylineOption> = {}): StorylineOption {
  return { id, job_id: 1, option_index: id, title: `Option ${id}`, content: {}, originality_score: 0.95, originality_report: {}, similarity_to_others: {}, recommended_chapters_min: 10, recommended_chapters_max: 30, rejected: false, selected: false, ...over }
}
function makeApi(over: Partial<AutonomousApi> = {}): AutonomousApi {
  return {
    createJob: vi.fn(async () => ({ job: job(), active: true })),
    getJob: vi.fn(async () => ({ job: job(), active: true })),
    listJobs: vi.fn(async () => []),
    listStorylines: vi.fn(async () => [option(1), option(2, { rejected: true, rejection_reason: 'too similar' })]),
    selectStoryline: vi.fn(async () => ({ job: job({ stage: 'NOVEL_ARCHITECTURE', status: 'queued', chapter_count: 20 }), active: true })),
    approveJob: vi.fn(async () => ({ job: job(), active: true })),
    pauseJob: vi.fn(async () => ({ job: job({ status: 'paused' }), active: false })),
    resumeJob: vi.fn(async () => ({ job: job(), active: true })),
    cancelJob: vi.fn(async () => ({ job: job({ status: 'cancelled' }), active: false })),
    listChapters: vi.fn(async () => []),
    listArtifacts: vi.fn(async () => []),
    getReport: vi.fn(async () => ({})),
    fileToBase64: vi.fn(async () => 'QUJD'),
    ...over,
  }
}

describe('screenFor', () => {
  it('derives the five screens from the job', () => {
    expect(screenFor(null)).toBe('upload')
    expect(screenFor(job({ stage: 'SOURCE_ANALYSIS', status: 'running' }))).toBe('analysis')
    expect(screenFor(job({ stage: 'STORYLINE_SELECTION', status: 'waiting_for_user' }))).toBe('choose')
    expect(screenFor(job({ stage: 'CHAPTER_GENERATION_LOOP', status: 'running' }))).toBe('generating')
    expect(screenFor(job({ stage: 'DONE', status: 'completed' }))).toBe('finished')
    expect(screenFor(job({ stage: 'STORYLINE_GENERATION', status: 'waiting_for_user', mode: 'manual' }))).toBe('choose')
  })
})

describe('useAutonomousNovel', () => {
  it('starts a job from the picked file and loads storylines once the job waits for selection', async () => {
    const api = makeApi({ createJob: vi.fn(async () => ({ job: job({ stage: 'STORYLINE_SELECTION', status: 'waiting_for_user' }), active: false })) })
    const auto = useAutonomousNovel(api, { pollMs: 1 })
    await auto.pickFile(new File(['abc'], 'book.epub'))
    expect(auto.file.value?.name).toBe('book.epub')
    await auto.start({ llm_config_id: 1, mode: 'fully_automatic' })
    expect(api.createJob).toHaveBeenCalledWith(expect.objectContaining({ filename: 'book.epub', content_base64: 'QUJD', llm_config_id: 1 }))
    expect(auto.screen.value).toBe('choose')
    expect(api.listStorylines).toHaveBeenCalledWith(1, true)
    expect(auto.acceptedOptions.value.map((o) => o.id)).toEqual([1])
    expect(auto.isActive.value).toBe(false)
    auto.stopPolling()
  })

  it('gates selection on a non-rejected option and a valid chapter count, and warns outside the recommended range', async () => {
    const api = makeApi({ createJob: vi.fn(async () => ({ job: job({ stage: 'STORYLINE_SELECTION', status: 'waiting_for_user' }), active: false })) })
    const auto = useAutonomousNovel(api, { pollMs: 1 })
    await auto.pickFile(new File(['abc'], 'book.epub'))
    await auto.start({ llm_config_id: 1 })
    expect(auto.canSelect.value).toBe(false)
    auto.selectedStorylineId.value = 2 // rejected
    expect(auto.canSelect.value).toBe(false)
    auto.selectedStorylineId.value = 1
    auto.chapterCount.value = 40
    expect(auto.canSelect.value).toBe(true)
    expect(auto.chapterCountWarning.value).toBe('above')
    expect(auto.estimatedWords.value).toBe(80_000)
    auto.chapterCount.value = 20
    expect(auto.chapterCountWarning.value).toBeNull()
    await auto.confirmSelection()
    expect(api.selectStoryline).toHaveBeenCalledWith(1, { storyline_id: 1, chapter_count: 20 })
    expect(auto.screen.value).toBe('generating')
    auto.stopPolling()
  })

  it('polls only while the job is active and surfaces API errors without breaking state', async () => {
    let calls = 0
    const api = makeApi({
      createJob: vi.fn(async () => ({ job: job({ stage: 'SOURCE_ANALYSIS', status: 'running' }), active: true })),
      getJob: vi.fn(async () => {
        calls += 1
        if (calls === 1) throw { response: { data: { detail: 'boom' } } }
        return { job: job({ stage: 'STORYLINE_SELECTION', status: 'waiting_for_user' }), active: false }
      }),
    })
    const auto = useAutonomousNovel(api, { pollMs: 1 })
    await auto.pickFile(new File(['abc'], 'book.epub'))
    await auto.start({ llm_config_id: 1 })
    expect(auto.screen.value).toBe('analysis')
    await new Promise((r) => setTimeout(r, 30))
    expect(auto.error.value).toBe('boom')
    await new Promise((r) => setTimeout(r, 30))
    expect(auto.screen.value).toBe('choose')
    const n = calls
    await new Promise((r) => setTimeout(r, 30))
    expect(calls).toBe(n) // stopped polling once waiting for the user
    auto.stopPolling()
  })
})
