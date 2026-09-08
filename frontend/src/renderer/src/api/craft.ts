import request from './request'
import type { components } from '@renderer/types/generated'

// Backend-generated types (single source of truth)
export type GradeRequest = components['schemas']['GradeRequest']
export type GradeResponse = components['schemas']['GradeResponse']
export type GradeCardRequest = components['schemas']['GradeCardRequest']
export type CriticReport = components['schemas']['CriticReport']
export type CriticFinding = components['schemas']['CriticFinding']
export type HookAnalysis = components['schemas']['HookAnalysis']
export type ScenePlanRequest = components['schemas']['ScenePlanRequest']
export type ScenePlanResponse = components['schemas']['ScenePlanResponse']
export type ScenePlan = components['schemas']['ScenePlan']
export type SubtextPacket = components['schemas']['SubtextPacket']
export type PresetInfo = components['schemas']['PresetInfo']

export type CraftPreset = 'off' | 'economy' | 'balanced' | 'full'
export const CRAFT_PRESETS: CraftPreset[] = ['off', 'economy', 'balanced', 'full']

const opts = { showLoading: false }

export function gradeText(body: GradeRequest): Promise<GradeResponse> {
  return request.post('/craft/grade', body, '/api', opts)
}

export function gradeCard(body: GradeCardRequest): Promise<GradeResponse> {
  return request.post('/craft/grade/card', body, '/api', opts)
}

export function scenePlan(body: ScenePlanRequest): Promise<ScenePlanResponse> {
  return request.post('/craft/scene-plan', body, '/api', opts)
}

export function listPresets(): Promise<PresetInfo[]> {
  return request.get('/craft/presets', {}, '/api', opts)
}

export function ticCatalogue(): Promise<Array<Record<string, unknown>>> {
  return request.get('/craft/tics/catalogue', {}, '/api', opts)
}
