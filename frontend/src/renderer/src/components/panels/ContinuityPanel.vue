<template>
  <div class="continuity-panel">
    <div class="toolbar">
      <el-select v-model="llmConfigId" size="small" filterable :placeholder="t('bible.selectModel')" style="width: 100%">
        <el-option v-for="llm in llmConfigs" :key="llm.id" :label="llm.display_name" :value="Number(llm.id)" />
      </el-select>
    </div>

    <el-card class="tool-card" shadow="never">
      <template #header>
        <div class="card-head">
          <span>{{ t('continuity.guardTitle') }}</span>
          <el-tag v-if="report" size="small" effect="dark" :type="verdictType(report.verdict)">{{ t('continuity.verdict.' + report.verdict) }} · {{ report.score }}</el-tag>
        </div>
      </template>
      <p class="hint">{{ t('continuity.guardHint') }}</p>
      <div class="row">
        <el-button size="small" type="primary" :loading="sm.busy.value === 'check' && !usedLlm" :disabled="sm.busy.value !== ''" @click="run(false)">{{ t('continuity.runFast') }}</el-button>
        <el-button size="small" :loading="sm.busy.value === 'check' && usedLlm" :disabled="sm.busy.value !== '' || !llmConfigId" @click="run(true)">{{ t('continuity.runDeep') }}</el-button>
      </div>
      <div v-if="report" class="report">
        <div class="muted">{{ t('continuity.checksRun', { n: report.checks_run.length, chars: report.checked_chars }) }}</div>
        <el-empty v-if="!report.issues.length" :description="t('continuity.clean')" :image-size="50" />
        <div v-else class="issues">
          <div v-for="(i, k) in report.issues" :key="k" class="issue" :class="'sev-' + i.severity" @click="jump(i)">
            <div class="issue-head">
              <el-tag size="small" effect="dark" :type="sevType(i.severity)">{{ i.severity }}</el-tag>
              <code class="code">{{ i.code }}</code>
              <el-tag v-if="i.source === 'llm'" size="small" effect="plain" type="info">LLM</el-tag>
            </div>
            <div class="issue-msg">{{ i.message }}</div>
            <div v-if="i.excerpt" class="issue-excerpt">{{ i.excerpt }}</div>
            <div v-if="i.suggestion" class="muted">💡 {{ i.suggestion }}</div>
          </div>
        </div>
      </div>
    </el-card>

    <CraftGradePanel
      :get-text="currentDraft"
      :pov="pov ?? null"
      :closing-hook="((outline as any)?.closing_hook as string) || null"
      :word-target="((outline as any)?.word_target as number) || null"
      @jump="(span) => emit('jump', span)"
    />

    <el-card class="tool-card" shadow="never">
      <template #header>
        <div class="card-head">
          <span>{{ t('continuity.digestTitle') }}</span>
          <el-tag v-if="digestState === 'fresh'" size="small" type="success" effect="plain">{{ t('continuity.digestFresh') }}</el-tag>
          <el-tag v-else-if="digestState === 'stale'" size="small" type="warning" effect="plain">{{ t('storyMemory.stale') }}</el-tag>
          <el-tag v-else size="small" type="info" effect="plain">{{ t('storyMemory.notDigested') }}</el-tag>
        </div>
      </template>
      <p class="hint">{{ t('continuity.digestHint') }}</p>
      <div class="row">
        <el-button size="small" type="primary" :loading="sm.busy.value === 'digest'" :disabled="sm.busy.value !== '' || !llmConfigId || !chapterNumber" @click="digestNow">{{ t('continuity.digestNow') }}</el-button>
        <span class="muted" v-if="sm.settings.value">{{ sm.settings.value.auto_digest_on_save ? t('continuity.autoOn') : t('continuity.autoOff') }}</span>
      </div>
    </el-card>

    <el-card class="tool-card" shadow="never">
      <template #header>
        <div class="card-head">
          <span>{{ t('continuity.briefTitle') }}</span>
          <el-button size="small" text type="primary" :loading="sm.busy.value === 'brief'" @click="loadBrief">{{ t('bible.refresh') }}</el-button>
        </div>
      </template>
      <div v-if="sm.brief.value" class="brief-compact">
        <div v-if="sm.brief.value.must_address.length">
          <div class="brief-h must">{{ t('storyMemory.brief.must') }}</div>
          <ul><li v-for="(i, k) in sm.brief.value.must_address.slice(0, 8)" :key="k">{{ i.text }}</li></ul>
        </div>
        <div v-if="sm.brief.value.avoid.length">
          <div class="brief-h avoid">{{ t('storyMemory.brief.avoid') }}</div>
          <ul><li v-for="(i, k) in sm.brief.value.avoid.slice(0, 8)" :key="k">{{ i.text }}</li></ul>
        </div>
        <div v-if="sm.brief.value.should_consider.length">
          <div class="brief-h should">{{ t('storyMemory.brief.should') }}</div>
          <ul><li v-for="(i, k) in sm.brief.value.should_consider.slice(0, 6)" :key="k">{{ i.text }}</li></ul>
        </div>
        <div v-if="sm.brief.value.rhythm_advice.length">
          <div class="brief-h">{{ t('storyMemory.brief.rhythm') }}</div>
          <ul><li v-for="(r, k) in sm.brief.value.rhythm_advice" :key="k">{{ r }}</li></ul>
        </div>
        <div class="row">
          <el-button size="small" @click="copyBrief">{{ t('storyMemory.copy') }}</el-button>
        </div>
      </div>
      <el-empty v-else :description="t('storyMemory.briefHint')" :image-size="50" />
    </el-card>

    <el-card class="tool-card" shadow="never" v-if="sm.storySoFar.value">
      <template #header>
        <div class="card-head">
          <span>{{ t('continuity.recapTitle') }}</span>
          <span class="muted">{{ t('storyMemory.recapMeta', { through: sm.storySoFar.value.through_chapter, next: sm.storySoFar.value.next_chapter, used: sm.storySoFar.value.used_chars, budget: sm.storySoFar.value.budget_chars }) }}</span>
        </div>
      </template>
      <el-alert v-if="sm.storySoFar.value.missing_chapters.length" type="warning" :closable="false" show-icon :title="t('storyMemory.recapMissing', { list: sm.storySoFar.value.missing_chapters.slice(0, 12).join(', ') })" />
      <div v-if="sm.storySoFar.value.last_ending_state" class="ending">
        <div class="brief-h">{{ t('storyMemory.dd.endingState') }}</div>
        <p>{{ sm.storySoFar.value.last_ending_state }}</p>
      </div>
      <el-collapse>
        <el-collapse-item :title="t('continuity.showRecap')">
          <pre class="pre">{{ sm.storySoFar.value.text }}</pre>
        </el-collapse-item>
      </el-collapse>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, toRef, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { getAIConfigOptions } from '@renderer/api/ai'
import type { ContinuityIssue } from '@renderer/api/storyMemory'
import { useStoryMemory, type ReportView } from '@renderer/composables/useStoryMemory'
import { useEditorStore } from '@renderer/stores/useEditorStore'
import CraftGradePanel from '@renderer/components/panels/CraftGradePanel.vue'

const props = defineProps<{
  projectId?: number
  chapterNumber?: number | null
  volumeNumber?: number | null
  participants?: string[]
  pov?: string | null
  outline?: Record<string, unknown> | null
  chapterCardId?: number | null
  refreshSeq?: number
}>()
const emit = defineEmits<{ (e: 'jump', span: [number, number]): void; (e: 'digested'): void }>()
const { t } = useI18n()
const editorStore = useEditorStore()

const sm = useStoryMemory(toRef(props, 'projectId'))
const llmConfigs = ref<Array<{ id: number; display_name: string }>>([])
const llmConfigId = ref<number | null>(null)
const report = ref<ReportView | null>(null)
const usedLlm = ref(false)

const digestState = computed<'fresh' | 'stale' | 'missing'>(() => {
  const n = props.chapterNumber
  if (!n) return 'missing'
  if (sm.coverage.value.stale.includes(n)) return 'stale'
  if (sm.coverage.value.digested.includes(n)) return 'fresh'
  return 'missing'
})

function verdictType(v: string) { return v === 'clean' ? 'success' : v === 'review' ? 'warning' : 'danger' }
function sevType(s: string) { return s === 'critical' ? 'danger' : s === 'high' ? 'danger' : s === 'medium' ? 'warning' : 'info' }

async function currentDraft(): Promise<string> {
  // The editor exposes the live draft through the store; fall back to the saved card text.
  const live = await editorStore.getActiveChapterDraft?.()
  return (live ?? '').toString()
}

async function run(useLlm: boolean) {
  const draft = await currentDraft()
  if (!draft.trim()) { ElMessage.warning(t('continuity.noDraft')); return }
  usedLlm.value = useLlm
  try {
    report.value = await sm.runCheck({ draft, chapterNumber: props.chapterNumber ?? null, participants: props.participants || [], pov: props.pov ?? null, outline: props.outline ?? null, useLlm, llmConfigId: llmConfigId.value })
    if (report.value) ElMessage[report.value.verdict === 'clean' ? 'success' : 'warning'](t('continuity.done', { n: report.value.issues.length, score: report.value.score }))
  } catch (e) { console.error(e); ElMessage.error(t('continuity.failed')) }
}

function jump(i: ContinuityIssue) {
  if (i.span && i.span.length === 2) emit('jump', [i.span[0], i.span[1]])
}

async function digestNow() {
  if (!llmConfigId.value || !props.chapterNumber) return
  try {
    // Persist the live draft first so the digest reflects what is on screen.
    await editorStore.persistActiveChapterDraft?.()
    await sm.digestOne(props.chapterNumber, llmConfigId.value, true)
    ElMessage.success(t('storyMemory.digestDone', { n: props.chapterNumber }))
    emit('digested')
    await loadBrief()
  } catch (e) { console.error(e); ElMessage.error(t('storyMemory.digestFailed')) }
}

async function loadBrief() {
  try { await sm.loadBrief(props.chapterNumber ?? null, props.participants || [], props.pov ?? null) } catch (e) { console.error(e) }
}

async function copyBrief() {
  try { await navigator.clipboard.writeText(sm.brief.value?.text || ''); ElMessage.success(t('storyMemory.copied')) } catch { ElMessage.error(t('storyMemory.copyFailed')) }
}

async function reload() {
  try { await sm.refresh(); await loadBrief() } catch (e) { console.error(e) }
}

watch(() => [props.projectId, props.chapterNumber], reload)
watch(() => props.refreshSeq, reload)
watch(() => sm.settings.value?.digest_llm_config_id, (id) => { if (id && !llmConfigId.value) llmConfigId.value = Number(id) })

onMounted(async () => {
  try {
    const opts = await getAIConfigOptions()
    llmConfigs.value = (opts as any)?.llm_configs || []
    if (!llmConfigId.value && llmConfigs.value.length) llmConfigId.value = Number(llmConfigs.value[0].id)
  } catch { /* ignore */ }
  await reload()
})
</script>

<style scoped>
.continuity-panel { display: flex; flex-direction: column; gap: 10px; padding: 8px; min-width: 0; }
.toolbar { display: flex; gap: 8px; }
.tool-card :deep(.el-card__header) { padding: 8px 12px; }
.tool-card :deep(.el-card__body) { padding: 10px 12px; display: flex; flex-direction: column; gap: 8px; }
.card-head { display: flex; justify-content: space-between; align-items: center; gap: 8px; font-weight: 600; font-size: 13px; }
.hint { margin: 0; font-size: 12px; color: var(--el-text-color-secondary); line-height: 1.5; }
.row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.muted { color: var(--el-text-color-secondary); font-size: 12px; }
.report { display: flex; flex-direction: column; gap: 6px; }
.issues { display: flex; flex-direction: column; gap: 6px; }
.issue { border: 1px solid var(--el-border-color-lighter); border-left-width: 4px; border-radius: 6px; padding: 6px 8px; cursor: pointer; display: flex; flex-direction: column; gap: 3px; background: var(--el-fill-color-blank); }
.issue:hover { background: var(--el-fill-color-light); }
.issue.sev-critical { border-left-color: var(--el-color-danger); }
.issue.sev-high { border-left-color: var(--el-color-danger-light-3); }
.issue.sev-medium { border-left-color: var(--el-color-warning); }
.issue.sev-low, .issue.sev-info { border-left-color: var(--el-color-info); }
.issue-head { display: flex; gap: 6px; align-items: center; }
.code { font-size: 11px; color: var(--el-text-color-regular); }
.issue-msg { font-size: 13px; line-height: 1.45; }
.issue-excerpt { font-size: 12px; font-style: italic; color: var(--el-text-color-regular); border-left: 2px solid var(--el-border-color); padding-left: 6px; }
.brief-compact { display: flex; flex-direction: column; gap: 8px; }
.brief-compact ul, .ending p { margin: 0; padding-left: 16px; font-size: 12px; line-height: 1.5; }
.ending p { padding-left: 0; }
.brief-h { font-size: 12px; font-weight: 600; margin-bottom: 2px; }
.brief-h.must { color: var(--el-color-danger); }
.brief-h.avoid { color: var(--el-color-info); }
.brief-h.should { color: var(--el-color-warning); }
.pre { white-space: pre-wrap; word-break: break-word; font-size: 11.5px; line-height: 1.5; padding: 8px; border-radius: 6px; background: var(--el-fill-color-light); color: var(--el-text-color-primary); max-height: 50vh; overflow: auto; margin: 0; }
</style>
