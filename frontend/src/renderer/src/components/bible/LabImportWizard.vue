<template>
  <div class="lab-wizard">
    <div class="lab-head">
      <h3 class="title">{{ t('bible.lab.title') }}</h3>
      <p class="muted">{{ t('bible.lab.subtitle') }}</p>
    </div>

    <el-steps :active="lab.step.value" finish-status="success" align-center class="steps">
      <el-step :title="t('bible.lab.step1')" @click="lab.step.value = 0" />
      <el-step :title="t('bible.lab.step2')" @click="lab.preview.value && (lab.step.value = 1)" />
      <el-step :title="t('bible.lab.step3')" @click="hasManuscript && (lab.step.value = 2)" />
      <el-step :title="t('bible.lab.step4')" @click="hasManuscript && (lab.step.value = 3)" />
    </el-steps>

    <!-- Step 1: file + metadata -->
    <div v-show="lab.step.value === 0" class="step-body" data-testid="lab-step-1">
      <label class="drop" :class="{ active: dragging }" @dragover.prevent="dragging = true" @dragleave="dragging = false" @drop.prevent="onDrop">
        <input ref="fileInput" type="file" accept=".txt,.md,.markdown,.epub,.docx" class="hidden-input" @change="onPick" />
        <div v-if="!lab.file.value" class="drop-text">{{ t('bible.lab.dropFile') }}</div>
        <div v-else class="drop-text"><b>{{ lab.file.value.name }}</b> · {{ (lab.file.value.size / 1024).toFixed(0) }} KB</div>
      </label>
      <el-form label-position="top" size="small" class="meta-form">
        <div class="form-grid">
          <el-form-item :label="t('bible.lab.bookTitle')"><el-input v-model="lab.meta.book_title" /></el-form-item>
          <el-form-item :label="t('bible.lab.author')"><el-input v-model="lab.meta.author" /></el-form-item>
          <el-form-item :label="t('bible.lab.genre')"><el-input v-model="lab.meta.genre" /></el-form-item>
          <el-form-item :label="t('bible.lab.language')"><el-input v-model="lab.meta.language" /></el-form-item>
          <el-form-item :label="t('bible.lab.encoding')">
            <el-select v-model="lab.detect.encoding" clearable :placeholder="t('bible.lab.autoDetect')">
              <el-option v-for="e in ['utf-8', 'utf-8-sig', 'utf-16', 'gb18030', 'cp1252', 'latin-1']" :key="e" :label="e" :value="e" />
            </el-select>
          </el-form-item>
          <el-form-item :label="t('bible.lab.chapterPattern')">
            <el-select v-model="lab.detect.chapter_pattern" clearable allow-create filterable default-first-option :placeholder="t('bible.lab.autoDetect')">
              <el-option v-for="p in patternCandidates" :key="p.name" :label="p.name" :value="p.pattern" />
            </el-select>
          </el-form-item>
          <el-form-item :label="t('bible.lab.volumePattern')"><el-input v-model="lab.detect.volume_pattern" :placeholder="t('bible.lab.autoDetect')" /></el-form-item>
          <el-form-item :label="t('bible.lab.minChapterWords')"><el-input-number v-model="lab.detect.min_chapter_words" :min="1" :max="20000" :step="10" data-testid="min-chapter-words" /></el-form-item>
        </div>
        <div class="switches">
          <el-checkbox v-model="lab.detect.exclude_front_matter">{{ t('bible.lab.excludeFront') }}</el-checkbox>
          <el-checkbox v-model="lab.detect.exclude_afterword">{{ t('bible.lab.excludeAfterword') }}</el-checkbox>
        </div>
      </el-form>
      <el-alert v-if="lab.previewError.value" type="error" :closable="false" show-icon :title="lab.previewError.value" />
      <div class="actions">
        <el-button type="primary" :disabled="!lab.file.value" :loading="lab.previewing.value" data-testid="preview-btn" @click="lab.runPreview">{{ lab.previewing.value ? t('bible.lab.previewing') : t('bible.lab.preview') }}</el-button>
      </div>
    </div>

    <!-- Step 2: section check, classification & corrections -->
    <div v-show="lab.step.value === 1" class="step-body" data-testid="lab-step-2">
      <div v-if="preview" class="summary">
        <el-alert type="info" :closable="false" show-icon :title="t('bible.lab.detected', { included: preview.included_chapters, total: preview.total_chapters, words: preview.total_words.toLocaleString(), volumes: preview.volumes.length, pattern: preview.pattern_name })" />
        <el-alert type="success" :closable="false" show-icon :title="t('bible.lab.estimate', { tokens: preview.estimated_input_tokens.toLocaleString(), chapters: preview.included_chapters })" />
        <el-alert v-if="preview.main_story_end_title" type="info" :closable="false" show-icon :title="t('bible.lab.mainEnding', { title: preview.main_story_end_title, index: preview.main_story_end_index })" />
        <el-alert v-if="preview.excluded_side_story_count + preview.excluded_bonus_extra_count + preview.excluded_other_count > 0" type="warning" :closable="false" show-icon data-testid="exclusion-summary"
          :title="t('bible.lab.excludedSummary', { side: preview.excluded_side_story_count, bonus: preview.excluded_bonus_extra_count, other: preview.excluded_other_count, words: preview.excluded_word_count.toLocaleString() })" />
        <el-alert v-if="preview.uncertain_count" type="error" :closable="false" show-icon :title="t('bible.lab.uncertain', { n: preview.uncertain_count })" />
        <el-alert v-if="preview.warnings.length" type="warning" :closable="false" show-icon :title="t('bible.lab.warnings')">
          <ul class="plain-list"><li v-for="(w, i) in preview.warnings" :key="i">{{ w }}</li></ul>
        </el-alert>
      </div>
      <div class="filters">
        <el-radio-group v-model="filter" size="small">
          <el-radio-button value="all">{{ t('bible.lab.filterAll') }} ({{ preview?.total_chapters || 0 }})</el-radio-button>
          <el-radio-button value="included">{{ t('bible.lab.filterIncluded') }} ({{ lab.includedChapters.value.length }})</el-radio-button>
          <el-radio-button value="excluded">{{ t('bible.lab.filterExcluded') }} ({{ lab.excludedChapters.value.length }})</el-radio-button>
        </el-radio-group>
        <el-button v-if="lab.corrections.value.length" size="small" @click="lab.undoLastCorrection">{{ t('bible.lab.undoCorrection', { n: lab.corrections.value.length }) }}</el-button>
      </div>
      <el-table v-if="preview" :data="visibleRows" size="small" border stripe max-height="460" :row-class-name="rowClass" style="width: 100%" :scrollbar-always-on="true" data-testid="section-table">
        <el-table-column label="#" width="56" prop="index" />
        <el-table-column :label="t('common.title')" min-width="240">
          <template #default="{ row }">
            <div class="chapter-title">{{ row.title }}<span v-if="row.number != null" class="muted"> · #{{ row.number }}</span></div>
            <div class="muted preview-text">{{ row.preview }}</div>
            <div v-if="!row.included" class="exclusion-reason" data-testid="exclusion-reason">{{ row.exclusion_reason }}</div>
          </template>
        </el-table-column>
        <el-table-column :label="t('bible.lab.sectionType')" width="170">
          <template #default="{ row }">
            <el-select :model-value="row.section_type" size="small" data-testid="section-type-select" @change="(v: string) => lab.correct({ op: 'set_type', section_id: row.section_id, section_type: v })">
              <el-option v-for="st in sectionTypes" :key="st" :label="t('bible.lab.types.' + st, st)" :value="st" />
            </el-select>
            <div class="muted conf" :title="row.classification_evidence.join('\n')">{{ Math.round(row.classification_confidence * 100) }}%<span v-if="row.manually_overridden"> · {{ t('bible.lab.manual') }}</span></div>
          </template>
        </el-table-column>
        <el-table-column label="Words" width="80" prop="word_count" align="right" />
        <el-table-column :label="t('bible.lab.included')" width="90" align="center">
          <template #default="{ row }">
            <el-switch :model-value="row.included" size="small" data-testid="include-switch" @change="(v: boolean) => lab.correct({ op: v ? 'include' : 'exclude', section_id: row.section_id })" />
          </template>
        </el-table-column>
        <el-table-column label="Flags" width="130">
          <template #default="{ row }">
            <el-tag v-for="f in row.flags" :key="f" size="small" :type="f === 'excluded' || f === 'side_story' || f === 'front_matter' || f === 'afterword' ? 'info' : 'warning'" effect="plain" class="flag">{{ t('bible.lab.flags.' + f, f) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column :label="t('common.action')" width="110" fixed="right" align="center">
          <template #default="{ row }">
            <el-dropdown trigger="click" size="small" @command="(cmd: string) => rowAction(cmd, row)">
              <el-button size="small" text type="primary">{{ t('common.action') }} <el-icon><ArrowDown /></el-icon></el-button>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item command="merge">{{ t('bible.lab.actions.merge') }}</el-dropdown-item>
                  <el-dropdown-item command="rename">{{ t('bible.lab.actions.rename') }}</el-dropdown-item>
                  <el-dropdown-item command="split">{{ t('bible.lab.actions.split') }}</el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
          </template>
        </el-table-column>
      </el-table>
      <div class="actions">
        <el-button @click="lab.step.value = 0">{{ t('common.back') }}</el-button>
        <el-checkbox v-model="lab.replaceExisting.value" class="replace">{{ t('bible.lab.replaceExisting') }}</el-checkbox>
        <el-button type="primary" :disabled="!preview || !props.projectId || !lab.includedChapters.value.length" :loading="lab.importing.value" data-testid="import-btn" @click="doImport">
          {{ lab.importing.value ? t('bible.lab.importing') : t('bible.lab.import', { n: lab.includedChapters.value.length }) }}
        </el-button>
      </div>
    </div>

    <!-- Step 3: analyse (start / monitor the Lab workflow) -->
    <div v-show="lab.step.value === 2" class="step-body" data-testid="lab-step-3">
      <el-alert v-if="lab.importResult.value" type="success" :closable="false" show-icon :title="t('bible.lab.imported', { n: lab.importResult.value.chapter_count, words: lab.importResult.value.total_words.toLocaleString(), excluded: lab.importResult.value.excluded_count })" />
      <el-alert type="info" :closable="false" show-icon :title="t('bible.lab.runWorkflowHint')" />
      <el-form label-position="top" size="small">
        <div class="form-grid">
          <el-form-item :label="t('bible.model')">
            <el-select v-model="llmConfigId" :placeholder="t('bible.selectModel')" data-testid="llm-select">
              <el-option v-for="c in llmStore.llmConfigs" :key="c.id" :label="`${c.display_name || c.model_name} (${c.provider})`" :value="c.id" />
            </el-select>
          </el-form-item>
          <el-form-item :label="t('bible.lab.concurrency')"><el-input-number v-model="concurrency" :min="1" :max="32" /></el-form-item>
          <el-form-item :label="t('bible.lab.scopeStart')"><el-input-number v-model="lab.scope.start_chapter" :min="0" data-testid="scope-start" /></el-form-item>
          <el-form-item :label="t('bible.lab.scopeEnd')"><el-input-number v-model="lab.scope.end_chapter" :min="0" data-testid="scope-end" /></el-form-item>
          <el-form-item :label="t('bible.lab.scopeMode')">
            <el-checkbox v-model="lab.scope.only_missing" data-testid="scope-only-missing">{{ t('bible.lab.onlyMissing') }}</el-checkbox>
            <el-checkbox v-model="lab.scope.only_stale" data-testid="scope-only-stale">{{ t('bible.lab.onlyStale') }}</el-checkbox>
          </el-form-item>
        </div>
      </el-form>
      <el-alert v-if="lab.plan.value" :type="lab.plan.value.chapters_selected ? 'success' : 'warning'" :closable="false" show-icon data-testid="run-plan"
        :title="t('bible.lab.planSummary', { selected: lab.plan.value.chapters_selected, total: lab.plan.value.chapters_total, done: lab.plan.value.chapters_done, failed: lab.plan.value.chapters_failed, calls: lab.plan.value.estimated_model_calls, tokens: lab.plan.value.estimated_input_tokens.toLocaleString() })" />
      <el-alert v-if="lab.runError.value" type="error" :closable="false" show-icon :title="lab.runError.value" data-testid="run-error" />
      <div class="actions">
        <el-button @click="lab.step.value = 1" :disabled="!preview">{{ t('common.back') }}</el-button>
        <el-button :disabled="!hasManuscript || !llmConfigId" :loading="lab.planning.value" data-testid="plan-btn" @click="doPlan">{{ t('bible.lab.planRun') }}</el-button>
        <el-button type="primary" :disabled="!hasManuscript || !llmConfigId || lab.runActive.value" :loading="lab.launching.value" data-testid="run-btn" @click="doStart">{{ t('bible.lab.runWorkflow') }}</el-button>
      </div>
      <LabRunPanel v-if="lab.run.value" :run="lab.run.value" :resumable="lab.runResumable.value" :active="lab.runActive.value" @cancel="lab.cancelRun" @resume="lab.resumeRun" @refresh="lab.refreshRun" />
      <div class="actions"><el-button v-if="lab.run.value?.status === 'succeeded'" type="primary" @click="lab.step.value = 3">{{ t('bible.lab.next') }}</el-button></div>
    </div>

    <!-- Step 4: study & transform -->
    <div v-show="lab.step.value === 3" class="step-body" data-testid="lab-step-4">
      <LabRunPanel v-if="lab.run.value" :run="lab.run.value" :resumable="lab.runResumable.value" :active="lab.runActive.value" @cancel="lab.cancelRun" @resume="lab.resumeRun" @refresh="lab.refreshRun" />
      <p class="muted">{{ t('bible.lab.genomeHint') }}</p>
      <p class="muted">{{ t('bible.lab.legacy') }}</p>
      <div class="actions"><el-button @click="lab.step.value = 2">{{ t('common.back') }}</el-button></div>
    </div>

    <el-divider />
    <div class="manuscript">
      <div class="manuscript-head">
        <h4 class="subtitle">{{ t('bible.lab.manuscript') }}</h4>
        <el-button size="small" @click="lab.loadManuscript">{{ t('bible.refresh') }}</el-button>
      </div>
      <el-empty v-if="!manuscript?.chapters?.length" :description="t('bible.lab.noManuscript')" :image-size="60" />
      <template v-else>
        <div class="muted">{{ manuscript.meta.book_title }}<span v-if="manuscript.meta.author"> · {{ manuscript.meta.author }}</span> · {{ manuscript.chapters.length }} chapters · {{ lab.analysedCount.value }} analysed
          <span v-if="excludedStored.length"> · {{ t('bible.lab.excludedStored', { n: excludedStored.length, words: Number((manuscript.meta as any).excluded_word_count || 0).toLocaleString() }) }}</span>
        </div>
        <el-progress :percentage="manuscript.chapters.length ? Math.round(100 * lab.analysedCount.value / manuscript.chapters.length) : 0" :stroke-width="10" />
        <el-table :data="manuscript.chapters" size="small" border max-height="260" class="manuscript-table" style="width: 100%" :scrollbar-always-on="true">
          <el-table-column label="#" width="56" prop="chapter_number" />
          <el-table-column :label="t('common.title')" min-width="200" prop="title" />
          <el-table-column :label="t('bible.lab.sectionType')" width="130">
            <template #default="{ row }">{{ t('bible.lab.types.' + (row.section_type || 'main_chapter'), row.section_type) }}</template>
          </el-table-column>
          <el-table-column label="Words" width="80" prop="word_count" align="right" />
          <el-table-column :label="t('bible.lab.analysisStatus')" width="120">
            <template #default="{ row }"><el-tag size="small" :type="row.analysis_status === 'done' ? 'success' : 'info'" effect="plain">{{ row.analysis_status }}</el-tag></template>
          </el-table-column>
          <el-table-column label="Scenes" width="80" prop="scene_count" align="right" />
          <el-table-column width="110">
            <template #default="{ row }"><el-button size="small" text type="primary" @click="emit('open-card', row.card_id)">{{ t('bible.openCard') }}</el-button></template>
          </el-table-column>
        </el-table>
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, toRef, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ArrowDown } from '@element-plus/icons-vue'
import { useLLMConfigStore } from '@renderer/stores/useLLMConfigStore'
import * as labApi from '@renderer/api/lab'
import type { ChapterPreview } from '@renderer/api/lab'
import { useLabImport } from '@renderer/composables/useLabImport'
import LabRunPanel from './LabRunPanel.vue'

const props = defineProps<{ projectId?: number }>()
const emit = defineEmits<{ (e: 'open-card', id: number): void; (e: 'imported'): void }>()
const { t } = useI18n()
const llmStore = useLLMConfigStore()
const lab = useLabImport(labApi, toRef(props, 'projectId'))

const fileInput = ref<HTMLInputElement | null>(null)
const dragging = ref(false)
const filter = ref<'all' | 'included' | 'excluded'>('all')
const patternCandidates = ref<Array<{ name: string; pattern: string }>>([])
const sectionTypes = ref<string[]>(['front_matter', 'preface', 'prologue', 'main_chapter', 'main_interlude', 'main_epilogue', 'afterword', 'appendix', 'side_story', 'bonus_story', 'extra', 'unknown'])
const llmConfigId = ref<number | undefined>(undefined)
const concurrency = ref(2)
let pollTimer: ReturnType<typeof setInterval> | null = null

const preview = computed(() => lab.preview.value)
const manuscript = computed(() => lab.manuscript.value)
const hasManuscript = computed(() => !!manuscript.value?.chapters?.length)
const excludedStored = computed<any[]>(() => ((manuscript.value?.meta as any)?.excluded_sections as any[]) || [])
const visibleRows = computed(() => {
  const rows = preview.value?.chapters || []
  if (filter.value === 'included') return rows.filter((r) => r.included)
  if (filter.value === 'excluded') return rows.filter((r) => !r.included)
  return rows
})

function onPick(e: Event) {
  const f = (e.target as HTMLInputElement).files?.[0]
  if (f) void lab.setFile(f)
}
function onDrop(e: DragEvent) {
  dragging.value = false
  const f = e.dataTransfer?.files?.[0]
  if (f) void lab.setFile(f)
}
async function rename(row: ChapterPreview) {
  try {
    const { value } = await ElMessageBox.prompt(t('bible.lab.actions.rename'), row.title, { inputValue: row.title })
    if (value && value !== row.title) await lab.correct({ op: 'rename', section_id: row.section_id, title: value })
  } catch { /* cancelled */ }
}
async function split(row: ChapterPreview) {
  try {
    const { value } = await ElMessageBox.prompt(t('bible.lab.splitPrompt'), row.title)
    if (value) await lab.correct({ op: 'split', section_id: row.section_id, at_text: value })
  } catch { /* cancelled */ }
}
function rowAction(cmd: string, row: ChapterPreview) {
  if (cmd === 'merge') void lab.correct({ op: 'merge_with_next', section_id: row.section_id })
  else if (cmd === 'rename') void rename(row)
  else if (cmd === 'split') void split(row)
}
function rowClass({ row }: { row: ChapterPreview }) { return row.included ? '' : 'row-excluded' }

async function doImport() {
  try {
    if (await lab.runImport()) {
      const r = lab.importResult.value!
      ElMessage.success(t('bible.lab.imported', { n: r.chapter_count, words: r.total_words.toLocaleString(), excluded: r.excluded_count }))
      emit('imported')
    }
  } catch (e) { console.error(e); ElMessage.error(t('bible.lab.importFailed')) }
}

async function doPlan() {
  if (!llmConfigId.value) return
  await lab.planRun(llmConfigId.value, concurrency.value)
}

async function doStart() {
  if (!llmConfigId.value) return
  const run = await lab.startRun(llmConfigId.value, concurrency.value)
  if (run) ElMessage.success(t('bible.lab.runStarted', { id: run.run_id }))
  ensurePolling()
}

function ensurePolling() {
  if (pollTimer) return
  pollTimer = setInterval(async () => {
    if (!lab.run.value) return
    try { await lab.refreshRun() } catch { /* transient */ }
    if (!lab.runActive.value && pollTimer) { clearInterval(pollTimer); pollTimer = null }
  }, 4000)
}

async function init() {
  await Promise.allSettled([lab.loadManuscript(), lab.loadRuns()])
  if (lab.runActive.value) ensurePolling()
}

watch(() => props.projectId, init)
watch(() => llmStore.llmConfigs, (list) => {
  if (!llmConfigId.value) {
    const authnd = list.find((c) => (c.provider || '').toLowerCase() === 'authnd')
    llmConfigId.value = (authnd || list[0])?.id
  }
}, { immediate: true })
onMounted(async () => {
  try {
    const d = await labApi.getManuscriptDefaults()
    patternCandidates.value = d.pattern_candidates
    if (d.section_types?.length) sectionTypes.value = d.section_types
    if (d.min_chapter_words) lab.detect.min_chapter_words = d.min_chapter_words
  } catch { /* ignore */ }
  try { if (!llmStore.llmConfigs.length) await llmStore.fetchLLMConfigs() } catch { /* ignore */ }
  await init()
})
onBeforeUnmount(() => { if (pollTimer) clearInterval(pollTimer) })
</script>

<style scoped>
.lab-wizard { display: flex; flex-direction: column; gap: 12px; }
.title { margin: 0; font-size: 16px; }
.subtitle { margin: 0; font-size: 14px; }
.muted { color: var(--el-text-color-secondary); font-size: 12px; margin: 0; }
.steps { margin: 4px 0; }
.steps :deep(.el-step__title) { font-size: 12px; line-height: 1.3; word-break: normal; cursor: pointer; }
.step-body { display: flex; flex-direction: column; gap: 10px; }
.drop { display: flex; align-items: center; justify-content: center; min-height: 96px; border: 2px dashed var(--el-border-color); border-radius: 10px; cursor: pointer; background: var(--el-fill-color-lighter); transition: border-color .15s; }
.drop.active, .drop:hover { border-color: var(--el-color-primary); }
.drop-text { padding: 12px; text-align: center; }
.hidden-input { display: none; }
.form-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 0 12px; }
.switches { display: flex; gap: 16px; }
.filters { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
.actions { display: flex; gap: 8px; align-items: center; justify-content: flex-end; flex-wrap: wrap; }
.replace { margin-right: auto; white-space: normal; }
.replace :deep(.el-checkbox__label) { white-space: normal; line-height: 1.3; }
.summary { display: flex; flex-direction: column; gap: 6px; }
.plain-list { margin: 4px 0 0; padding-left: 16px; }
.chapter-title { font-weight: 600; }
.preview-text { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 520px; }
.exclusion-reason { color: var(--el-color-warning); font-size: 12px; }
.conf { font-size: 11px; }
.flag { margin-right: 4px; }
:deep(.row-excluded) { opacity: 0.6; }
.manuscript { display: flex; flex-direction: column; gap: 8px; }
.manuscript-head { display: flex; justify-content: space-between; align-items: center; }
.manuscript-table { margin-top: 4px; }
</style>
