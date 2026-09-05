import request from './request'
import type { components } from '@renderer/types/generated'

export type ManuscriptPreviewRequest = components['schemas']['ManuscriptPreviewRequest']
export type ManuscriptPreviewResponse = components['schemas']['ManuscriptPreviewResponse']
export type ManuscriptImportRequest = components['schemas']['ManuscriptImportRequest']
export type ManuscriptImportResponse = components['schemas']['ManuscriptImportResponse']
export type ManuscriptListResponse = components['schemas']['ManuscriptListResponse']
export type ChapterPreview = components['schemas']['ChapterPreview']
export type LabRunRequest = Partial<components['schemas']['LabRunRequest']> & { project_id: number; llm_config_id: number }
export interface LabRunNode { node_id?: string | null; status?: string | null; progress?: number | null; error?: string | null }
export type LabRunStatus = Omit<components['schemas']['LabRunStatus'], 'nodes'> & { nodes?: LabRunNode[] }
export type LabRunPlan = components['schemas']['LabRunPlan']

export type SectionCorrection =
  | { op: 'exclude' | 'include' | 'merge_with_next'; section_id: string; reason?: string }
  | { op: 'rename'; section_id: string; title: string }
  | { op: 'split'; section_id: string; at_text: string; new_title?: string }
  | { op: 'set_type'; section_id: string; section_type: string }

const opts = { showLoading: false }

export function previewManuscript(body: ManuscriptPreviewRequest): Promise<ManuscriptPreviewResponse> {
  return (request as any).request({ method: 'POST', url: '/api/lab/manuscript/preview', data: body, showLoading: false, timeout: 180_000 })
}

export function importManuscript(body: ManuscriptImportRequest): Promise<ManuscriptImportResponse> {
  return (request as any).request({ method: 'POST', url: '/api/lab/manuscript/import', data: body, showLoading: false, timeout: 300_000 })
}

export function listManuscript(projectId: number): Promise<ManuscriptListResponse> {
  return request.get('/lab/manuscript', { project_id: projectId }, '/api', opts)
}

export function getManuscriptDefaults(): Promise<{ volume_pattern: string; pattern_candidates: Array<{ name: string; pattern: string }>; supported_extensions: string[]; section_types: string[]; main_story_types: string[]; min_chapter_words: number }> {
  return request.get('/lab/manuscript/defaults', undefined, '/api', opts)
}

export function startLabWorkflow(body: LabRunRequest): Promise<LabRunStatus> {
  return (request as any).request({ method: 'POST', url: '/api/lab/workflow/run', data: body, showLoading: false, timeout: 60_000 })
}

export function listLabRuns(projectId: number, limit = 10): Promise<LabRunStatus[]> {
  return request.get('/lab/workflow/runs', { project_id: projectId, limit }, '/api', opts)
}

export function getLabRun(runId: number): Promise<LabRunStatus> {
  return request.get(`/lab/workflow/runs/${runId}`, undefined, '/api', opts)
}

export function cancelLabRun(runId: number): Promise<LabRunStatus> {
  return (request as any).request({ method: 'POST', url: `/api/lab/workflow/runs/${runId}/cancel`, showLoading: false })
}

export function resumeLabRun(runId: number): Promise<LabRunStatus> {
  return (request as any).request({ method: 'POST', url: `/api/lab/workflow/runs/${runId}/resume`, showLoading: false })
}

export function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => {
      const result = String(reader.result || '')
      const idx = result.indexOf(',')
      resolve(idx >= 0 ? result.slice(idx + 1) : result)
    }
    reader.onerror = () => reject(reader.error)
    reader.readAsDataURL(file)
  })
}

export function planLabWorkflow(body: LabRunRequest): Promise<LabRunPlan> {
  return (request as any).request({ method: 'POST', url: '/api/lab/workflow/plan', data: body, showLoading: false, timeout: 60_000 })
}
