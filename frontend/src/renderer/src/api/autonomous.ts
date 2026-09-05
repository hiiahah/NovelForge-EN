import request from './request'
import { API_BASE_URL } from './request'

export type AutonomousMode = 'fully_automatic' | 'approval_gates' | 'manual'

export interface CreateJobRequest {
  filename: string
  content_base64: string
  llm_config_id: number
  mode?: AutonomousMode
  role_llm_config_ids?: Record<string, number>
  title?: string
  author?: string
  genre?: string
  genre_intensity?: string
  content_rating?: string
  ending_preference?: string
  romance_level?: string
  words_per_chapter?: number
  total_words?: number
  quality_preset?: 'economy' | 'balanced' | 'quality'
  storyline_count?: number
  fallback_llm_config_id?: number
  notes?: string
}

export interface StageAttempt {
  stage: string
  attempt: number
  status: string
  failure_category?: string | null
  recovery_action?: string | null
  started_at?: string | null
  finished_at?: string | null
}

export interface AutonomousJob {
  id: number
  status: 'queued' | 'running' | 'waiting_for_user' | 'paused' | 'failed' | 'cancelled' | 'completed'
  stage: string
  mode: AutonomousMode
  source_project_id?: number | null
  original_project_id?: number | null
  llm_config_id: number
  source_filename: string
  options: Record<string, unknown>
  selected_storyline_id?: number | null
  chapter_count: number
  chapters_committed: number
  progress_percent: number
  progress_message: string
  stage_results: Record<string, any>
  warnings: Array<Record<string, unknown>>
  error?: { category: string; message: string; detail?: Record<string, unknown> } | null
  model_calls: number
  input_tokens: number
  output_tokens: number
  waiting_for?: 'storyline_selection' | 'plan_approval' | 'manuscript_approval' | 'manual_mode' | 'approval' | null
  created_at?: string | null
  updated_at?: string | null
  started_at?: string | null
  finished_at?: string | null
  attempts: StageAttempt[]
  stages: string[]
}

export interface JobResponse { job: AutonomousJob; active: boolean }

export interface StorylineOption {
  id: number
  job_id: number
  option_index: number
  title: string
  content: Record<string, any>
  originality_score: number
  originality_report: Record<string, any>
  similarity_to_others: Record<string, number>
  recommended_chapters_min: number
  recommended_chapters_max: number
  rejected: boolean
  rejection_reason?: string | null
  selected: boolean
}

export interface ExportArtifactInfo { id: number; kind: string; filename: string; media_type: string; size_bytes: number; content_hash: string; created_at: string }

export interface ChapterPreviewInfo { chapter_number: number; title?: string | null; words: number; summary?: string | null; sync_status?: string | null; validation_passed?: boolean | null; card_id: number; preview: string }

const opts = { showLoading: false }

export function createJob(body: CreateJobRequest): Promise<JobResponse> {
  return (request as any).request({ method: 'POST', url: '/api/autonomous/jobs', data: body, showLoading: false, timeout: 300_000 })
}
export function listJobs(limit = 20): Promise<Array<AutonomousJob & { active: boolean }>> {
  return request.get('/autonomous/jobs', { limit }, '/api', opts)
}
export function getJob(jobId: number): Promise<JobResponse> {
  return request.get(`/autonomous/jobs/${jobId}`, undefined, '/api', opts)
}
export function listStorylines(jobId: number, includeRejected = false): Promise<StorylineOption[]> {
  return request.get(`/autonomous/jobs/${jobId}/storylines`, { include_rejected: includeRejected }, '/api', opts)
}
export function selectStoryline(jobId: number, body: { storyline_id: number; chapter_count: number; words_per_chapter?: number; title?: string }): Promise<JobResponse> {
  return (request as any).request({ method: 'POST', url: `/api/autonomous/jobs/${jobId}/select`, data: body, showLoading: false, timeout: 60_000 })
}
export function approveJob(jobId: number): Promise<JobResponse> {
  return (request as any).request({ method: 'POST', url: `/api/autonomous/jobs/${jobId}/approve`, showLoading: false })
}
export function pauseJob(jobId: number): Promise<JobResponse> {
  return (request as any).request({ method: 'POST', url: `/api/autonomous/jobs/${jobId}/pause`, showLoading: false })
}
export function resumeJob(jobId: number): Promise<JobResponse> {
  return (request as any).request({ method: 'POST', url: `/api/autonomous/jobs/${jobId}/resume`, showLoading: false })
}
export function cancelJob(jobId: number): Promise<JobResponse> {
  return (request as any).request({ method: 'POST', url: `/api/autonomous/jobs/${jobId}/cancel`, showLoading: false })
}
export function listChapters(jobId: number): Promise<ChapterPreviewInfo[]> {
  return request.get(`/autonomous/jobs/${jobId}/chapters`, undefined, '/api', opts)
}
export function listArtifacts(jobId: number): Promise<ExportArtifactInfo[]> {
  return request.get(`/autonomous/jobs/${jobId}/artifacts`, undefined, '/api', opts)
}
export function getReport(jobId: number): Promise<Record<string, any>> {
  return request.get(`/autonomous/jobs/${jobId}/report`, undefined, '/api', opts)
}
export function artifactDownloadUrl(artifactId: number): string {
  return `${API_BASE_URL}/autonomous/artifacts/${artifactId}/download`
}
