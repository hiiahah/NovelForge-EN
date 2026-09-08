import { describe, expect, it, vi } from 'vitest'
import { ref } from 'vue'
import { useForgePipeline, type ForgeApi } from '../useForgePipeline'
import type { IsolationReport, Manifest, SourceStatus } from '@renderer/api/forge'

function manifest(over: Partial<Manifest> = {}): Manifest {
  return {
    project_id: 2, project_role: 'original', source_project_id: 1, canon_revision: 3, outline_revision: 1, fingerprint_revision: 1,
    latest_committed_chapter: 3, next_allowed_chapter: 4, unresolved_errors: [], stale_dependency_count: 0, stale_artifacts: [], ...over,
  }
}
function source(over: Partial<SourceStatus> = {}): SourceStatus {
  return {
    project_id: 1, chapters: 24, analysed: 24, failed_chapters: [], completeness: 1, evidence_total: 100, evidence_verified: 95, evidence_coverage: 0.95,
    integrity: { ok: true }, fingerprint: { card_id: 9, version: 'fp-1', stale: false }, example_library: { examples: 60, functions: { chapter_cliffhanger: 20 }, chapters: 24, manuscripts: ['m'] }, ...over,
  }
}
const isolation = (ok = true): IsolationReport => ({ project_id: 2, source_project_id: 1, isolated: ok, problems: ok ? [] : [{ kind: 'entity_overlap' }], firewall: {} })

function makeApi(over: Partial<ForgeApi> = {}): ForgeApi {
  return {
    getSourceStatus: vi.fn(async () => source()),
    verifySource: vi.fn(async () => ({})),
    buildFingerprint: vi.fn(async () => ({})),
    buildExamples: vi.fn(async () => ({})),
    createOriginalProject: vi.fn(async () => ({ project_id: 2 })),
    getIsolation: vi.fn(async () => isolation()),
    seedCanon: vi.fn(async () => ({ facts_seeded: 8 })),
    getManifest: vi.fn(async () => manifest()),
    compileChapter: vi.fn(async () => ({})),
    runChapter: vi.fn(async () => ({ status: 'committed' })),
    listRuns: vi.fn(async () => []),
    ...over,
  }
}

describe('useForgePipeline', () => {
  it('only the next allowed chapter (or a committed one for regeneration) can run', async () => {
    const api = makeApi()
    const forge = useForgePipeline(api, ref(2))
    await forge.refresh()
    expect(forge.chapterBlocker.value).toBeNull()
    expect(forge.canRunChapter(4)).toBe(true)
    expect(forge.canRunChapter(5)).toBe(false)
    expect(forge.canRunChapter(2, true)).toBe(true)
    expect(forge.canRunChapter(4, true)).toBe(false)
    expect(await forge.run(5, 1)).toBeNull()
    expect(api.runChapter).not.toHaveBeenCalled()
    expect(forge.error.value).toBe('chapter_not_allowed')
  })

  it('stale dependencies, contamination and unresolved errors block generation without calling the model', async () => {
    for (const [over, iso, blocker] of [
      [{ stale_dependency_count: 2 }, true, 'stale_dependencies'],
      [{}, false, 'isolation_failed'],
      [{ unresolved_errors: [{ code: 'x' }] }, true, 'unresolved_errors'],
      [{ project_role: 'source' }, true, 'not_original_project'],
    ] as Array<[Partial<Manifest>, boolean, string]>) {
      const api = makeApi({ getManifest: vi.fn(async () => manifest(over)), getIsolation: vi.fn(async () => isolation(iso)) })
      const forge = useForgePipeline(api, ref(2))
      await forge.refresh()
      expect(forge.chapterBlocker.value).toBe(blocker)
      expect(await forge.run(4, 1)).toBeNull()
      expect(api.runChapter).not.toHaveBeenCalled()
    }
  })

  it('a run refreshes the manifest afterwards and refuses a concurrent run', async () => {
    let resolveRun!: (v: Record<string, unknown>) => void
    const api = makeApi({ runChapter: vi.fn(() => new Promise<Record<string, unknown>>((r) => { resolveRun = r })) })
    const forge = useForgePipeline(api, ref(2))
    await forge.refresh()
    const p = forge.run(4, 1)
    expect(forge.activeRun.value).toBe(true)
    expect(forge.canRunChapter(4)).toBe(false)
    resolveRun({ status: 'committed' })
    expect(await p).toEqual({ status: 'committed' })
    expect(api.getManifest).toHaveBeenCalledTimes(2)
    expect(forge.lastRun.value).toEqual({ status: 'committed' })
  })

  it('source-side steps gate on complete, verified analysis and a fresh fingerprint', async () => {
    const api = makeApi({ getManifest: vi.fn(async () => manifest({ project_role: 'source', canon_revision: 0, latest_committed_chapter: 0, next_allowed_chapter: 1 })), getSourceStatus: vi.fn(async () => source({ analysed: 23, failed_chapters: [7], fingerprint: null, example_library: { examples: 0, functions: {}, chapters: 0, manuscripts: [] } })) })
    const forge = useForgePipeline(api, ref(1))
    await forge.refresh()
    expect(forge.analysisComplete.value).toBe(false)
    expect(forge.activeStep.value).toBe(1) // analyse
    ;(api.getSourceStatus as any).mockResolvedValue(source({ fingerprint: { card_id: 9, version: 'fp-1', stale: true } }))
    await forge.refresh()
    expect(forge.analysisComplete.value).toBe(true)
    expect(forge.fingerprintReady.value).toBe(false)
    expect(forge.activeStep.value).toBe(2) // fingerprint
    ;(api.getSourceStatus as any).mockResolvedValue(source())
    await forge.refresh()
    expect(forge.activeStep.value).toBe(3) // foundation: ready to create the original project
  })

  it('surfaces structured backend errors (fail-closed compiler codes)', async () => {
    const api = makeApi({ compileChapter: vi.fn(async () => { throw { response: { data: { detail: { code: 'previous_chapter_not_synchronized', message: 'Chapter 3 has not been committed' } } } } }) })
    const forge = useForgePipeline(api, ref(2))
    await forge.refresh()
    expect(await forge.compile(4)).toBeNull()
    expect(forge.error.value).toBe('Chapter 3 has not been committed')
  })

  it('forwards the Prose Craft preset to the run request and omits it when not chosen', async () => {
    const api = makeApi()
    const forge = useForgePipeline(api, ref(2))
    await forge.refresh()
    await forge.run(4, 7, false, 'full')
    expect(api.runChapter).toHaveBeenLastCalledWith({ project_id: 2, chapter_number: 4, llm_config_id: 7, regenerate: false, craft_preset: 'full' })
    await forge.run(4, 7)
    expect((api.runChapter as any).mock.calls[1][0]).not.toHaveProperty('craft_preset')
  })
})
