import request from './request'
import type { components } from '@renderer/types/generated'

export type CreateOriginalRequest = Partial<components['schemas']['CreateOriginalRequest']> & { source_project_id: number; name: string }
export type CompileRequest = Partial<components['schemas']['CompileRequest']> & { project_id: number; chapter_number: number }
export type RunChapterRequest = Partial<components['schemas']['RunChapterRequest']> & { project_id: number; chapter_number: number; llm_config_id: number }

export interface SourceStatus {
  project_id: number
  manuscript_id?: string | null
  language?: string | null
  chapters: number
  analysed: number
  failed_chapters: number[]
  completeness: number
  evidence_total: number
  evidence_verified: number
  evidence_coverage: number
  integrity: { ok: boolean; problems?: unknown[] }
  fingerprint: { card_id: number; version?: string | null; dependency_hash?: string | null; stale: boolean; chapters_measured?: number | null } | null
  example_library: { examples: number; functions: Record<string, number>; chapters: number; manuscripts: string[] }
}

export interface Manifest {
  project_id: number
  project_role: string
  source_project_id?: number | null
  source_manuscript_id?: string | null
  canon_revision: number
  outline_revision: number
  fingerprint_revision: number
  latest_committed_chapter: number
  next_allowed_chapter: number
  context_compiler_version?: string | null
  unresolved_errors: unknown[]
  stale_dependency_count: number
  stale_artifacts: Array<{ artifact_kind: string; artifact_key?: string; card_id?: number | null; reason?: string }>
  last_sync_status?: string | null
  last_sync_chapter?: number | null
  updated_at?: string | null
}

export interface IsolationReport { project_id: number; source_project_id?: number | null; isolated: boolean; not_applicable?: boolean; problems: Array<Record<string, unknown>>; firewall: Record<string, unknown> }

export interface PipelineRunSummary {
  run_id: number
  chapter_number: number
  status: string
  stage?: string | null
  chapter_card_id?: number | null
  canon_revision_before?: number | null
  canon_revision_after?: number | null
  validation_passed?: boolean | null
  blocking_issues?: number | null
  style_score?: number | null
  originality_passed?: boolean | null
  craft_score?: number | null
  craft_mode?: string | null
  repair_attempts?: number | null
  model_calls?: number | null
  error?: Record<string, unknown> | null
  created_at?: string | null
}

export interface CompiledContext {
  project_id: number
  chapter_number: number
  pov: string
  participants: string[]
  text: string
  sections: Array<{ key: string; title: string; chars: number; mandatory: boolean; card_ids: number[]; text: string }>
  prohibited: string[]
  retrieved_examples: Array<{ example_id: string; beat_function: string; chapter_number: number }>
  context_hash: string
  manifest: Record<string, unknown>
}

const opts = { showLoading: false }

export function getSourceStatus(projectId: number): Promise<SourceStatus> {
  return request.get('/forge/source/status', { project_id: projectId }, '/api', opts)
}
export function verifySource(projectId: number): Promise<Record<string, unknown>> {
  return (request as any).request({ method: 'POST', url: '/api/forge/source/verify', params: { project_id: projectId }, showLoading: false, timeout: 120_000 })
}
export function buildFingerprint(projectId: number): Promise<Record<string, unknown>> {
  return (request as any).request({ method: 'POST', url: '/api/forge/source/fingerprint', params: { project_id: projectId }, showLoading: false, timeout: 120_000 })
}
export function buildExamples(projectId: number): Promise<Record<string, unknown>> {
  return (request as any).request({ method: 'POST', url: '/api/forge/source/examples', params: { project_id: projectId }, showLoading: false, timeout: 180_000 })
}
export function createOriginalProject(body: CreateOriginalRequest): Promise<{ project_id: number; fingerprint_card_id: number; mechanism_card_ids: number[]; rejected_mechanisms: unknown[]; firewall: { isolated: boolean } }> {
  return (request as any).request({ method: 'POST', url: '/api/forge/original/create', data: body, showLoading: false, timeout: 60_000 })
}
export function getIsolation(projectId: number): Promise<IsolationReport> {
  return request.get('/forge/original/isolation', { project_id: projectId }, '/api', opts)
}
export function seedCanon(projectId: number): Promise<{ facts_seeded: number }> {
  return (request as any).request({ method: 'POST', url: '/api/forge/original/seed-canon', params: { project_id: projectId }, showLoading: false })
}
export function getManifest(projectId: number): Promise<Manifest> {
  return request.get('/forge/manifest', { project_id: projectId }, '/api', opts)
}
export function getAudit(projectId: number): Promise<{ findings: Array<Record<string, unknown>>; counts: Record<string, number> }> {
  return request.get('/forge/audit', { project_id: projectId }, '/api', opts)
}
export function compileChapter(body: CompileRequest): Promise<CompiledContext> {
  return (request as any).request({ method: 'POST', url: '/api/forge/chapters/compile', data: body, showLoading: false, timeout: 60_000 })
}
export function runChapter(body: RunChapterRequest): Promise<Record<string, unknown>> {
  return (request as any).request({ method: 'POST', url: '/api/forge/chapters/run', data: body, showLoading: false, timeout: 900_000 })
}
export function listRuns(projectId: number, limit = 20): Promise<PipelineRunSummary[]> {
  return request.get('/forge/chapters/runs', { project_id: projectId, limit }, '/api', opts)
}
