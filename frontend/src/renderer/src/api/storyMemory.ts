import request from './request'
import type { components } from '@renderer/types/generated'

// Backend-generated types (single source of truth)
export type ChapterDigest = components['schemas']['ChapterDigest']
export type DigestListItem = components['schemas']['DigestListItem']
export type DigestListResponse = components['schemas']['DigestListResponse']
export type DigestChapterRequest = components['schemas']['DigestChapterRequest']
export type DigestBatchRequest = components['schemas']['DigestBatchRequest']
export type DigestBatchResult = components['schemas']['DigestBatchResult']
export type StorySoFar = components['schemas']['StorySoFar']
export type StorySoFarRequest = components['schemas']['StorySoFarRequest']
export type CarryForwardEntity = components['schemas']['CarryForwardEntity']
export type DanglingHook = components['schemas']['DanglingHook']
export type ContinuityCheckRequest = components['schemas']['ContinuityCheckRequest']
export type ContinuityReport = components['schemas']['ContinuityReport']
export type ContinuityIssue = components['schemas']['ContinuityIssue']
export type BriefRequest = components['schemas']['BriefRequest']
export type NextChapterBrief = components['schemas']['NextChapterBrief']
export type BriefItem = components['schemas']['BriefItem']
export type BibleHealth = components['schemas']['BibleHealth']
export type HealthDimension = components['schemas']['HealthDimension']
export type StoryMemorySettings = components['schemas']['StoryMemorySettings']
export type SettingsResponse = components['schemas']['SettingsResponse']
export type CardRead = components['schemas']['CardRead']

const opts = { showLoading: false }

function longCall<T>(method: 'POST' | 'PUT', url: string, data: unknown, timeoutSeconds?: number | null): Promise<T> {
  return (request as any).request({
    method,
    url,
    data,
    showLoading: false,
    timeout: Math.max(300_000, ((timeoutSeconds || 0) * 1000) + 30_000),
  })
}

export function listDigests(projectId: number): Promise<DigestListResponse> {
  return request.get('/story-memory/digests', { project_id: projectId }, '/api', opts)
}

export function getDigest(projectId: number, chapterNumber: number): Promise<ChapterDigest> {
  return request.get(`/story-memory/digests/${chapterNumber}`, { project_id: projectId }, '/api', opts)
}

export function digestChapter(body: DigestChapterRequest): Promise<CardRead> {
  return longCall('POST', '/api/story-memory/digests', body, body.timeout)
}

export function digestBatch(body: DigestBatchRequest): Promise<DigestBatchResult> {
  // Batch runs sequentially on the server; allow ~5 minutes per chapter.
  return longCall('POST', '/api/story-memory/digests/batch', body, Math.max(600, (body.timeout || 240) * Math.min(body.max_chapters || 50, 50)))
}

export function saveDigest(projectId: number, chapterNumber: number, digest: ChapterDigest): Promise<CardRead> {
  return (request as any).request({ method: 'PUT', url: `/api/story-memory/digests/${chapterNumber}`, params: { project_id: projectId }, data: digest, showLoading: false })
}

export function deleteDigest(projectId: number, chapterNumber: number): Promise<{ success: boolean }> {
  return (request as any).request({ method: 'DELETE', url: `/api/story-memory/digests/${chapterNumber}`, params: { project_id: projectId }, showLoading: false })
}

export function getStorySoFar(body: StorySoFarRequest): Promise<StorySoFar> {
  return request.post('/story-memory/story-so-far', body, '/api', opts)
}

export function checkContinuity(body: ContinuityCheckRequest): Promise<ContinuityReport> {
  return longCall('POST', '/api/story-memory/continuity/check', body, body.use_llm ? body.timeout : 60)
}

export function getNextChapterBrief(body: BriefRequest): Promise<NextChapterBrief> {
  return request.post('/story-memory/brief', body, '/api', opts)
}

export function getBibleHealth(projectId: number): Promise<BibleHealth> {
  return request.get('/story-memory/health', { project_id: projectId }, '/api', opts)
}

export function getStoryMemorySettings(projectId: number): Promise<SettingsResponse> {
  return request.get('/story-memory/settings', { project_id: projectId }, '/api', opts)
}

export function saveStoryMemorySettings(projectId: number, settings: StoryMemorySettings): Promise<SettingsResponse> {
  return request.put('/story-memory/settings', { project_id: projectId, settings }, '/api', opts)
}
