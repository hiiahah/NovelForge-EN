import { describe, expect, it, vi } from 'vitest'
import { ref } from 'vue'
import { useLabImport, type LabApi } from '../useLabImport'
import type { LabRunStatus, ManuscriptPreviewResponse } from '@renderer/api/lab'

function deferred<T>() {
  let resolve!: (v: T) => void
  let reject!: (e: unknown) => void
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}

function previewFor(name: string, chapters: Partial<ManuscriptPreviewResponse['chapters'][number]>[] = []): ManuscriptPreviewResponse {
  const rows = chapters.map((c, i) => ({
    index: i + 1, number: i + 1, title: `Chapter ${i + 1}`, volume: 'Volume 1', word_count: 100, flags: [], preview: '', section_id: `s${i + 1}`,
    source_path: `${name}#${i}`, spine_index: i, source_label: `Chapter ${i + 1}`, section_type: 'main_chapter', is_main_story: true, included: true,
    exclusion_reason: '', classification_confidence: 0.9, classification_evidence: [], manually_overridden: false, ...c,
  })) as ManuscriptPreviewResponse['chapters']
  const included = rows.filter((r) => r.included)
  return {
    chapter_pattern: '', pattern_name: name, volume_pattern: '', chapters: rows, volumes: ['Volume 1'], warnings: [],
    total_words: included.length * 100, total_chapters: rows.length, included_chapters: included.length, estimated_input_tokens: 0,
    pattern_candidates: [], supported_extensions: [], section_types: [], min_chapter_words: 80, spine_item_count: rows.length,
    main_story_end_index: rows.length, main_story_end_title: rows[rows.length - 1]?.title ?? null, excluded_side_story_count: rows.filter((r) => r.section_type === 'side_story').length,
    excluded_bonus_extra_count: 0, excluded_other_count: 0, uncertain_count: 0, excluded_word_count: (rows.length - included.length) * 100, excluded_side_story_word_count: 0, book_meta: {},
  }
}

function run(status: string, id = 1): LabRunStatus {
  return { run_id: id, workflow_id: 1, project_id: 7, status, percent: 0, chapters_total: 0, chapters_done: 0, chapters_failed: 0, nodes: [] } as LabRunStatus
}

function makeApi(overrides: Partial<LabApi> = {}): LabApi {
  return {
    previewManuscript: vi.fn(async (body) => previewFor(body.filename, [{}, {}])),
    importManuscript: vi.fn(async () => ({ folder_card_id: 1, chapter_card_ids: [1, 2], chapter_count: 2, total_words: 200, excluded_count: 0, excluded_words: 0 })),
    listManuscript: vi.fn(async () => ({ folder_card_id: 1, meta: {}, chapters: [{ card_id: 1, analysis_status: 'pending' }] })),
    startLabWorkflow: vi.fn(async () => run('running')),
    listLabRuns: vi.fn(async () => []),
    getLabRun: vi.fn(async () => run('running')),
    cancelLabRun: vi.fn(async () => run('cancelled')),
    resumeLabRun: vi.fn(async () => run('queued')),
    fileToBase64: vi.fn(async (f: File) => `b64:${f.name}`),
    ...overrides,
  }
}

const file = (name: string) => ({ name, size: 10 } as unknown as File)

describe('useLabImport', () => {
  it('rapid file selection keeps filename and bytes together', async () => {
    const readers: Record<string, ReturnType<typeof deferred<string>>> = { 'a.epub': deferred<string>(), 'b.epub': deferred<string>() }
    const api = makeApi({ fileToBase64: vi.fn((f: File) => readers[f.name].promise) })
    const lab = useLabImport(api, ref(7))
    const pa = lab.setFile(file('a.epub'))
    const pb = lab.setFile(file('b.epub'))
    readers['b.epub'].resolve('B-BYTES')
    readers['a.epub'].resolve('A-BYTES') // slow first read finishes last
    expect(await pa).toBe(false)
    expect(await pb).toBe(true)
    expect(lab.file.value).toEqual({ name: 'b.epub', size: 10, base64: 'B-BYTES' })
    expect(lab.payload().filename).toBe('b.epub')
    expect(lab.payload().content_base64).toBe('B-BYTES')
  })

  it('stale preview responses never overwrite a newer one', async () => {
    const first = deferred<ManuscriptPreviewResponse>()
    const second = deferred<ManuscriptPreviewResponse>()
    let n = 0
    const api = makeApi({ previewManuscript: vi.fn(() => (++n === 1 ? first.promise : second.promise)) })
    const lab = useLabImport(api, ref(7))
    await lab.setFile(file('a.epub'))
    const p1 = lab.runPreview()
    const p2 = lab.runPreview()
    second.resolve(previewFor('second', [{}]))
    expect(await p2).toBe(true)
    first.resolve(previewFor('first', [{}, {}, {}]))
    expect(await p1).toBe(false)
    expect(lab.preview.value?.pattern_name).toBe('second')
    expect(lab.previewing.value).toBe(false)
  })

  it('a preview belonging to a previous file is discarded', async () => {
    const slow = deferred<ManuscriptPreviewResponse>()
    const api = makeApi({ previewManuscript: vi.fn(() => slow.promise) })
    const lab = useLabImport(api, ref(7))
    await lab.setFile(file('a.epub'))
    const p = lab.runPreview()
    await lab.setFile(file('b.epub'))
    slow.resolve(previewFor('a-preview', [{}]))
    expect(await p).toBe(false)
    expect(lab.preview.value).toBeNull()
  })

  it('corrections are addressed by stable section id and shown as excluded with reasons', async () => {
    const api = makeApi({
      previewManuscript: vi.fn(async (body) => {
        const corr = (body.corrections as any[]) || []
        const includeBonus = corr.some((c) => c.op === 'include' && c.section_id === 's3')
        return previewFor('x', [{}, {}, { section_id: 's3', title: 'Bonus Story: Extra', section_type: 'bonus_story', is_main_story: false, included: includeBonus, exclusion_reason: includeBonus ? '' : 'Bonus story: supplementary fiction outside the main narrative', manually_overridden: includeBonus }])
      }),
    })
    const lab = useLabImport(api, ref(7))
    await lab.setFile(file('a.epub'))
    await lab.runPreview()
    expect(lab.excludedChapters.value.map((c) => c.exclusion_reason)).toEqual(['Bonus story: supplementary fiction outside the main narrative'])
    expect(lab.step.value).toBe(1)
    await lab.correct({ op: 'include', section_id: 's3' })
    expect((api.previewManuscript as any).mock.calls[1][0].corrections).toEqual([{ op: 'include', section_id: 's3' }])
    expect(lab.excludedChapters.value).toHaveLength(0)
    expect(lab.includedChapters.value.map((c) => c.section_id)).toContain('s3')
    await lab.undoLastCorrection()
    expect(lab.corrections.value).toEqual([])
    expect(lab.excludedChapters.value).toHaveLength(1)
  })

  it('walks all four steps: import, launch, monitor, finish', async () => {
    let status = 'running'
    const api = makeApi({ getLabRun: vi.fn(async () => run(status)) })
    const lab = useLabImport(api, ref(7))
    expect(lab.step.value).toBe(0)
    await lab.setFile(file('a.epub'))
    await lab.runPreview()
    expect(lab.step.value).toBe(1)
    expect(await lab.runImport()).toBe(true)
    expect(lab.step.value).toBe(2)
    expect((api.importManuscript as any).mock.calls[0][0]).toMatchObject({ project_id: 7, filename: 'a.epub', replace_existing: true })
    const r = await lab.startRun(3, 4)
    expect(r?.status).toBe('running')
    expect((api.startLabWorkflow as any).mock.calls[0][0]).toEqual({ project_id: 7, llm_config_id: 3, analysis_concurrency: 4 })
    expect(lab.step.value).toBe(3)
    expect(lab.runActive.value).toBe(true)
    status = 'succeeded'
    await lab.refreshRun()
    expect(lab.run.value?.status).toBe('succeeded')
    expect(lab.runActive.value).toBe(false)
    expect((api.listManuscript as any).mock.calls.length).toBeGreaterThanOrEqual(2)
  })

  it('prevents duplicate launches while a run is active', async () => {
    const api = makeApi()
    const lab = useLabImport(api, ref(7))
    await lab.startRun(1)
    await lab.startRun(1)
    expect((api.startLabWorkflow as any).mock.calls).toHaveLength(1)
  })

  it('surfaces launch errors, and supports cancel and resume', async () => {
    const api = makeApi({ startLabWorkflow: vi.fn(async () => { throw { response: { data: { detail: 'No imported manuscript in this project. Import chapters first.' } } } }) })
    const lab = useLabImport(api, ref(7))
    expect(await lab.startRun(1)).toBeNull()
    expect(lab.runError.value).toContain('No imported manuscript')

    const api2 = makeApi()
    const lab2 = useLabImport(api2, ref(7))
    await lab2.startRun(1)
    await lab2.cancelRun()
    expect(lab2.run.value?.status).toBe('cancelled')
    expect(lab2.runResumable.value).toBe(true)
    await lab2.resumeRun()
    expect((api2.resumeLabRun as any).mock.calls[0][0]).toBe(1)
    expect(lab2.run.value?.status).toBe('queued')
    expect(lab2.runActive.value).toBe(true)
  })

  it('loadRuns picks the active run and jumps to the analyse step', async () => {
    const api = makeApi({ listLabRuns: vi.fn(async () => [run('failed', 2), run('running', 3)]) })
    const lab = useLabImport(api, ref(7))
    await lab.loadRuns()
    expect(lab.run.value?.run_id).toBe(3)
    expect(lab.step.value).toBe(3)
  })

  it('import is skipped without a project or preview', async () => {
    const api = makeApi()
    const lab = useLabImport(api, ref(undefined))
    expect(await lab.runImport()).toBe(false)
    expect((api.importManuscript as any).mock.calls).toHaveLength(0)
  })
})
