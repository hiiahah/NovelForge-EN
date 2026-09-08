<template>
  <div class="bible-studio">
    <el-empty v-if="!projectId" :description="t('bible.noProject')" />
    <template v-else>
      <div class="studio-header">
        <div>
          <h2 class="studio-title">{{ t('bible.studioTitle') }}</h2>
          <p class="muted">{{ t('bible.studioSubtitle') }}</p>
        </div>
        <div class="studio-actions">
          <el-radio-group v-model="mode" size="small">
            <el-radio-button value="create">{{ t('bible.createBible') }}</el-radio-button>
            <el-radio-button value="extract">{{ t('bible.extractBible') }}</el-radio-button>
          </el-radio-group>
          <el-tag v-if="dashboard" size="small" effect="plain" type="info">{{ t('bible.currentChapter', { n: dashboard.current_chapter }) }}</el-tag>
          <el-badge :value="pendingReviews" :hidden="pendingReviews === 0" type="warning">
            <el-button size="small" :loading="loading" @click="refresh">{{ t('bible.refresh') }}</el-button>
          </el-badge>
        </div>
      </div>

      <div class="studio-body">
        <el-menu :default-active="section" class="studio-nav" @select="(k: string) => (section = k)">
          <el-menu-item v-for="s in visibleSections" :key="s" :index="s">
            <span>{{ t('bible.sections.' + s) }}</span>
            <el-tag v-if="sectionCount(s) !== null" size="small" effect="plain" class="nav-count">{{ sectionCount(s) }}</el-tag>
            <el-tag v-if="s === 'audits' && warnings.length" size="small" type="warning" effect="dark" class="nav-count">{{ warnings.length }}</el-tag>
            <el-tag v-if="s === 'updates' && pendingReviews" size="small" type="warning" effect="dark" class="nav-count">{{ pendingReviews }}</el-tag>
          </el-menu-item>
        </el-menu>

        <el-scrollbar class="studio-content">
          <div class="content-inner" v-loading="loading">
            <!-- Ledger-like sections -->
            <template v-if="isCardSection(section)">
              <div v-if="entriesFor(section).length === 0" class="empty-hint">
                <el-empty :description="emptyHint(section)" :image-size="70" />
              </div>
              <div v-else class="entry-grid">
                <el-card v-for="e in entriesFor(section)" :key="e.card_id" shadow="hover" class="entry-card" @click="emit('open-card', e.card_id)">
                  <div class="entry-head">
                    <el-tag size="small" effect="plain">{{ e.card_type }}</el-tag>
                    <span class="entry-title">{{ e.title }}</span>
                  </div>
                  <div class="entry-meta">
                    <el-tag v-if="e.truth_status" size="small" effect="dark" :type="truthType(e.truth_status)">{{ t('bible.truth.' + e.truth_status, e.truth_status) }}</el-tag>
                    <el-tag v-if="e.status" size="small" effect="plain">{{ e.status }}</el-tag>
                    <el-tag v-if="e.urgency" size="small" effect="plain" :type="urgencyType(e.urgency)">{{ e.urgency }}</el-tag>
                    <span v-if="typeof e.confidence === 'number'" class="muted">{{ Math.round(e.confidence * 100) }}%</span>
                    <span v-if="e.evidence_count" class="muted">{{ t('bible.evidenceCount', { n: e.evidence_count }) }}</span>
                    <el-tag v-if="!e.has_content" size="small" type="info" effect="plain">empty</el-tag>
                  </div>
                  <div v-if="section === 'characters'" class="entry-actions" @click.stop>
                    <el-button size="small" text type="primary" :loading="deepeningId === e.card_id" @click="deepen(e)">{{ t('bible.deepen') }}</el-button>
                  </div>
                </el-card>
              </div>
            </template>

            <RelationshipMatrix v-else-if="section === 'relationships'" :arcs="relationships" @open-card="(id) => emit('open-card', id)" />
            <KnowledgeMatrix v-else-if="section === 'knowledge'" :entities="knowledge.entities" :rows="knowledge.rows" @open-card="(id) => emit('open-card', id)" />

            <div v-else-if="section === 'audits'" class="audits">
              <el-empty v-if="!warnings.length" :description="t('bible.empty.auditsOk')" :image-size="70" />
              <el-alert v-for="(w, i) in warnings" :key="i" :type="w.severity === 'high' ? 'error' : w.severity === 'medium' ? 'warning' : 'info'" :closable="false" show-icon class="audit-alert">
                <template #title>
                  <b>{{ t('bible.audit.' + w.kind, w.kind) }}</b> — {{ w.message }}
                  <el-button v-if="w.card_id" size="small" text type="primary" @click="emit('open-card', w.card_id)">{{ t('bible.openCard') }}</el-button>
                </template>
              </el-alert>
            </div>

            <BibleUpdateReview v-else-if="section === 'updates'" ref="updateReviewRef" :project-id="projectId" :open-review-id="openReviewId" @applied="refresh" />
            <LabImportWizard v-else-if="section === 'lab'" :project-id="projectId" @open-card="(id) => emit('open-card', id)" @imported="refresh" />
            <ForgePipelinePanel v-else-if="section === 'forge'" :project-id="projectId" @open-card="(id) => emit('open-card', id)" />
            <StoryMemoryPanel v-else-if="section === 'memory'" :project-id="projectId" :refresh-seq="refreshSeq" @open-card="(id) => emit('open-card', id)" />
          </div>
        </el-scrollbar>
      </div>
    </template>

    <el-dialog v-model="deepenDialog.visible" :title="t('bible.deepen')" width="520px">
      <el-form label-position="top" size="small">
        <el-form-item :label="t('bible.model')">
          <el-select v-model="deepenDialog.llm_config_id" filterable style="width: 100%" :placeholder="t('bible.selectModel')">
            <el-option v-for="llm in llmConfigs" :key="llm.id" :label="llm.display_name" :value="Number(llm.id)" />
          </el-select>
        </el-form-item>
        <el-form-item :label="t('bible.deepenNotes')">
          <el-input v-model="deepenDialog.notes" type="textarea" :autosize="{ minRows: 2, maxRows: 6 }" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="deepenDialog.visible = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" :disabled="!deepenDialog.llm_config_id" :loading="deepeningId !== null" @click="confirmDeepen">{{ t('common.confirm') }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { getAIConfigOptions } from '@renderer/api/ai'
import { deepenCharacter, getBibleDashboard, getKnowledgeMatrix, getRelationshipMatrix, listBibleUpdates, type BibleDashboardResponse } from '@renderer/api/bible'
import { useCardStore } from '@renderer/stores/useCardStore'
import BibleUpdateReview from './BibleUpdateReview.vue'
import KnowledgeMatrix from './KnowledgeMatrix.vue'
import ForgePipelinePanel from './ForgePipelinePanel.vue'
import StoryMemoryPanel from './StoryMemoryPanel.vue'
import LabImportWizard from './LabImportWizard.vue'
import RelationshipMatrix from './RelationshipMatrix.vue'

const props = defineProps<{ projectId?: number; refreshSeq?: number; openReviewId?: number | null; initialSection?: string }>()
const emit = defineEmits<{ (e: 'open-card', id: number): void }>()
const { t } = useI18n()
const cardStore = useCardStore()

const CREATE_SECTIONS = ['memory', 'forge', 'foundation', 'characters', 'relationships', 'world', 'threads', 'promises', 'knowledge', 'timeline', 'updates', 'audits']
const EXTRACT_SECTIONS = ['lab', 'forge', 'analysis', 'memory', 'characters', 'relationships', 'threads', 'promises', 'knowledge', 'timeline', 'audits']
const CARD_SECTIONS = new Set(['foundation', 'characters', 'world', 'threads', 'promises', 'timeline', 'analysis'])

const mode = ref<'create' | 'extract'>('create')
const section = ref<string>(props.initialSection || 'foundation')
const loading = ref(false)
const dashboard = ref<BibleDashboardResponse | null>(null)
const relationships = ref<any[]>([])
const knowledge = ref<{ entities: string[]; rows: any[] }>({ entities: [], rows: [] })
const pendingReviews = ref(0)
const llmConfigs = ref<Array<{ id: number; display_name: string }>>([])
const deepeningId = ref<number | null>(null)
const deepenDialog = reactive({ visible: false, card_id: 0, llm_config_id: null as number | null, notes: '' })
const updateReviewRef = ref<any>(null)

const visibleSections = computed(() => (mode.value === 'create' ? CREATE_SECTIONS : EXTRACT_SECTIONS))
const warnings = computed(() => (dashboard.value?.audits as any)?.warnings || [])

function isCardSection(s: string) { return CARD_SECTIONS.has(s) }
function entriesFor(s: string): any[] { return ((dashboard.value?.sections as any)?.[s]?.entries || []) }
function sectionCount(s: string): number | null {
  if (s === 'relationships') return relationships.value.length
  if (s === 'knowledge') return knowledge.value.rows.length
  if (isCardSection(s)) return entriesFor(s).length
  return null
}
function emptyHint(s: string) {
  if (s === 'foundation') return t('bible.empty.foundationHint')
  if (s === 'analysis') return t('bible.empty.analysisHint')
  if (s === 'characters' || s === 'world') return t('bible.empty.section')
  return t('bible.empty.ledgerHint')
}
function truthType(s: string) { return s === 'canon' ? 'success' : s === 'disputed' ? 'danger' : s === 'inferred' ? 'warning' : s === 'obsolete' ? 'info' : 'primary' }
function urgencyType(u: string) { return u === 'critical' ? 'danger' : u === 'high' ? 'warning' : 'info' }

async function refresh() {
  if (!props.projectId) return
  loading.value = true
  try {
    const [dash, rel, know, updates] = await Promise.all([
      getBibleDashboard(props.projectId),
      getRelationshipMatrix(props.projectId),
      getKnowledgeMatrix(props.projectId),
      listBibleUpdates(props.projectId),
    ])
    dashboard.value = dash
    relationships.value = rel.arcs || []
    knowledge.value = { entities: know.entities || [], rows: know.rows || [] }
    pendingReviews.value = (updates.items || []).filter(r => r.status === 'pending' || r.status === 'partially_applied').length
    updateReviewRef.value?.load?.()
  } catch (e) { console.error(e) } finally { loading.value = false }
}

function deepen(entry: any) {
  deepenDialog.card_id = entry.card_id
  if (!deepenDialog.llm_config_id && llmConfigs.value.length) deepenDialog.llm_config_id = Number(llmConfigs.value[0].id)
  deepenDialog.visible = true
}

async function confirmDeepen() {
  if (!props.projectId || !deepenDialog.llm_config_id) return
  deepeningId.value = deepenDialog.card_id
  deepenDialog.visible = false
  try {
    const card = await deepenCharacter({ project_id: props.projectId, card_id: deepenDialog.card_id, llm_config_id: deepenDialog.llm_config_id, user_notes: deepenDialog.notes || null })
    ElMessage.success(t('bible.deepenDone', { name: card.title }))
    await cardStore.fetchCards(props.projectId)
    await refresh()
  } catch (e) { console.error(e); ElMessage.error(t('bible.deepenFailed')) } finally { deepeningId.value = null }
}

watch(() => props.projectId, refresh)
watch(() => props.refreshSeq, refresh)
watch(() => props.openReviewId, (id) => { if (id) { mode.value = 'create'; section.value = 'updates' } })
// Each mode has a natural landing section; switching modes should always start there.
watch(mode, (m) => { section.value = m === 'create' ? 'foundation' : 'lab' })
onMounted(async () => {
  try { const opts = await getAIConfigOptions(); llmConfigs.value = (opts as any)?.llm_configs || [] } catch { /* ignore */ }
  await refresh()
  if (props.openReviewId) { mode.value = 'create'; section.value = 'updates' }
})
</script>

<style scoped>
.bible-studio { height: 100%; min-height: 0; width: 100%; display: flex; flex-direction: column; padding: 12px 16px; box-sizing: border-box; gap: 10px; overflow: hidden; container-type: inline-size; }
.studio-header { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; flex-wrap: wrap; }
.studio-title { margin: 0; font-size: 18px; }
.muted { color: var(--el-text-color-secondary); font-size: 12px; margin: 4px 0 0; }
.studio-actions { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.studio-body { flex: 1; min-height: 0; display: flex; gap: 12px; }
.studio-nav { width: 200px; flex-shrink: 0; border-right: 1px solid var(--el-border-color-lighter); border-radius: 8px; overflow: auto; }
.studio-nav :deep(.el-menu-item) { height: 40px; line-height: 40px; display: flex; justify-content: space-between; padding-right: 10px; }
.nav-count { margin-left: 6px; }
/* The editor's center pane is often narrow; switch to a horizontal nav strip so content keeps usable width. */
@container (max-width: 720px) {
  .studio-body { flex-direction: column; }
  .studio-nav { width: 100%; display: flex; flex-wrap: wrap; border-right: none; border-bottom: 1px solid var(--el-border-color-lighter); border-radius: 0; padding-bottom: 4px; }
  .studio-nav :deep(.el-menu-item) { height: 32px; line-height: 32px; padding: 0 10px; border-radius: 6px; }
}
.studio-content { flex: 1; min-width: 0; height: 100%; }
.content-inner { padding: 2px 8px 16px 2px; min-height: 200px; min-width: 0; }
.entry-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 10px; }
.entry-card { cursor: pointer; }
.entry-card :deep(.el-card__body) { padding: 12px; display: flex; flex-direction: column; gap: 6px; }
.entry-head { display: flex; align-items: center; gap: 8px; min-width: 0; }
.entry-title { font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.entry-meta { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
.entry-actions { display: flex; justify-content: flex-end; }
.audits { display: flex; flex-direction: column; gap: 8px; }
.audit-alert :deep(.el-alert__title) { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
</style>
