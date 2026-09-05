<template>
  <div class="forge" data-testid="forge-panel">
    <div class="forge-head">
      <div>
        <h3 class="title">{{ t('bible.forge.title') }}</h3>
        <p class="muted">{{ t('bible.forge.subtitle') }}</p>
      </div>
      <el-button size="small" :loading="!!forge.busy.value" @click="forge.refresh">{{ t('bible.refresh') }}</el-button>
    </div>

    <el-steps :active="forge.activeStep.value" finish-status="success" align-center class="steps">
      <el-step v-for="s in FORGE_STEPS" :key="s" :title="t('bible.forge.steps.' + s)" />
    </el-steps>

    <el-alert v-if="forge.error.value" type="error" :closable="false" show-icon :title="forge.error.value" data-testid="forge-error" />

    <!-- Status board -->
    <div class="board">
      <el-card shadow="never" class="cell" data-testid="forge-source-status">
        <template #header><b>{{ t('bible.forge.source') }}</b></template>
        <template v-if="source && (source.chapters > 0 || !forge.isOriginal.value)">
          <div class="row"><span>{{ t('bible.forge.chapters') }}</span><b>{{ source.analysed }} / {{ source.chapters }}</b></div>
          <div class="row"><span>{{ t('bible.forge.failedChapters') }}</span>
            <el-tag :type="source.failed_chapters.length ? 'danger' : 'success'" size="small" effect="plain">{{ source.failed_chapters.length ? source.failed_chapters.join(', ') : t('bible.forge.none') }}</el-tag>
          </div>
          <div class="row"><span>{{ t('bible.forge.evidenceCoverage') }}</span><b>{{ Math.round(source.evidence_coverage * 100) }}%</b></div>
          <el-progress :percentage="Math.round(source.evidence_coverage * 100)" :stroke-width="8" :status="source.evidence_coverage >= 0.9 ? 'success' : undefined" />
          <div class="row"><span>{{ t('bible.forge.integrity') }}</span>
            <el-tag :type="source.integrity?.ok ? 'success' : 'danger'" size="small" effect="plain">{{ source.integrity?.ok ? t('bible.forge.ok') : t('bible.forge.broken') }}</el-tag>
          </div>
          <div class="row"><span>{{ t('bible.forge.fingerprint') }}</span>
            <el-tag v-if="source.fingerprint" :type="source.fingerprint.stale ? 'warning' : 'success'" size="small" effect="plain">{{ source.fingerprint.version }} · {{ source.fingerprint.stale ? t('bible.forge.stale') : t('bible.forge.current') }}</el-tag>
            <el-tag v-else type="info" size="small" effect="plain">{{ t('bible.forge.missing') }}</el-tag>
          </div>
          <div class="row"><span>{{ t('bible.forge.examples') }}</span><b>{{ source.example_library?.examples || 0 }} · {{ Object.keys(source.example_library?.functions || {}).length }} {{ t('bible.forge.functions') }}</b></div>
          <div class="actions">
            <el-button size="small" :disabled="!!forge.busy.value" @click="forge.verify">{{ t('bible.forge.verify') }}</el-button>
            <el-button size="small" :disabled="!!forge.busy.value || !forge.analysisComplete.value" type="primary" data-testid="forge-fingerprint-btn" @click="forge.buildFingerprint">{{ t('bible.forge.buildFingerprint') }}</el-button>
            <el-button size="small" :disabled="!!forge.busy.value || !forge.analysisComplete.value" @click="forge.buildExamples">{{ t('bible.forge.buildExamples') }}</el-button>
          </div>
          <div v-if="!forge.isOriginal.value" class="create">
            <el-input v-model="originalName" size="small" :placeholder="t('bible.forge.originalName')" data-testid="forge-original-name" />
            <el-button size="small" type="success" :disabled="!!forge.busy.value || !forge.fingerprintReady.value || !forge.examplesReady.value || !originalName" data-testid="forge-create-original" @click="createOriginal">{{ t('bible.forge.createOriginal') }}</el-button>
          </div>
        </template>
        <el-empty v-else :description="t(forge.isOriginal.value ? 'bible.forge.originalNoSource' : 'bible.forge.noSource')" :image-size="50" />
      </el-card>

      <el-card shadow="never" class="cell" data-testid="forge-manifest">
        <template #header><b>{{ t('bible.forge.manifest') }}</b></template>
        <template v-if="manifest">
          <div class="row"><span>{{ t('bible.forge.role') }}</span><el-tag size="small" effect="dark" :type="forge.isOriginal.value ? 'success' : 'info'">{{ manifest.project_role }}</el-tag></div>
          <div class="row"><span>{{ t('bible.forge.isolation') }}</span>
            <el-tag v-if="forge.isolation.value?.not_applicable" size="small" effect="plain" type="info" data-testid="forge-isolation">{{ t('bible.forge.isolationNa') }}</el-tag>
            <el-tag v-else size="small" effect="plain" :type="forge.isolationOk.value ? 'success' : 'danger'" data-testid="forge-isolation">{{ forge.isolationOk.value ? t('bible.forge.isolated') : t('bible.forge.contaminated', { n: forge.isolation.value?.problems?.length || 0 }) }}</el-tag>
          </div>
          <div class="row"><span>{{ t('bible.forge.canonRevision') }}</span><b>{{ manifest.canon_revision }}</b></div>
          <div class="row"><span>{{ t('bible.forge.staleDeps') }}</span>
            <el-tag size="small" effect="plain" :type="forge.staleCount.value ? 'warning' : 'success'" data-testid="forge-stale">{{ forge.staleCount.value }}</el-tag>
          </div>
          <div class="row"><span>{{ t('bible.forge.lastSynced') }}</span><b>{{ forge.lastSynced.value }}</b></div>
          <div class="row"><span>{{ t('bible.forge.nextAllowed') }}</span><b data-testid="forge-next-chapter">{{ forge.nextChapter.value }}</b></div>
          <div class="row"><span>{{ t('bible.forge.lastSync') }}</span><span class="muted">{{ manifest.last_sync_status || '—' }}</span></div>
          <div v-if="manifest.unresolved_errors?.length" class="muted err">{{ t('bible.forge.unresolved', { n: manifest.unresolved_errors.length }) }}</div>
          <el-collapse v-if="manifest.stale_artifacts?.length" class="stale">
            <el-collapse-item :title="t('bible.forge.staleList', { n: manifest.stale_artifacts.length })">
              <div v-for="(a, i) in manifest.stale_artifacts" :key="i" class="muted mono">{{ a.artifact_kind }} · {{ a.artifact_key }}</div>
            </el-collapse-item>
          </el-collapse>
          <div v-if="forge.isOriginal.value" class="actions">
            <el-button size="small" :disabled="!!forge.busy.value" @click="forge.seedCanon">{{ t('bible.forge.seedCanon') }}</el-button>
          </div>
        </template>
      </el-card>
    </div>

    <!-- Chapter generation (original projects only) -->
    <el-card v-if="forge.isOriginal.value" shadow="never" class="cell" data-testid="forge-chapters">
      <template #header><b>{{ t('bible.forge.chapterRun') }}</b></template>
      <el-alert v-if="forge.chapterBlocker.value" type="warning" :closable="false" show-icon :title="t('bible.forge.blockers.' + forge.chapterBlocker.value)" data-testid="forge-blocker" />
      <el-form label-position="top" size="small">
        <div class="form-grid">
          <el-form-item :label="t('bible.model')">
            <el-select v-model="llmConfigId" :placeholder="t('bible.selectModel')">
              <el-option v-for="c in llmStore.llmConfigs" :key="c.id" :label="`${c.display_name || c.model_name} (${c.provider})`" :value="c.id" />
            </el-select>
          </el-form-item>
          <el-form-item :label="t('bible.forge.chapterNumber')"><el-input-number v-model="chapter" :min="1" :max="forge.nextChapter.value" /></el-form-item>
        </div>
      </el-form>
      <div class="actions">
        <el-button size="small" :disabled="!!forge.busy.value" @click="forge.compile(chapter, chapter < forge.nextChapter.value)">{{ t('bible.forge.compileOnly') }}</el-button>
        <el-button size="small" type="primary" :loading="forge.activeRun.value" :disabled="!llmConfigId || !forge.canRunChapter(chapter, chapter < forge.nextChapter.value)" data-testid="forge-run-btn" @click="runChapter">
          {{ chapter < forge.nextChapter.value ? t('bible.forge.regenerate', { n: chapter }) : t('bible.forge.generate', { n: chapter }) }}
        </el-button>
      </div>
      <el-table :data="forge.runs.value" size="small" border max-height="260" style="width: 100%">
        <el-table-column label="#" width="56" prop="chapter_number" />
        <el-table-column :label="t('bible.forge.status')" width="130">
          <template #default="{ row }"><el-tag size="small" effect="plain" :type="runType(row.status)">{{ row.status }}</el-tag></template>
        </el-table-column>
        <el-table-column :label="t('bible.forge.continuity')" width="110">
          <template #default="{ row }"><el-tag v-if="row.validation_passed !== null && row.validation_passed !== undefined" size="small" effect="plain" :type="row.validation_passed ? 'success' : 'danger'">{{ row.validation_passed ? t('bible.forge.pass') : t('bible.forge.fail', { n: row.blocking_issues || 0 }) }}</el-tag></template>
        </el-table-column>
        <el-table-column :label="t('bible.forge.originality')" width="110">
          <template #default="{ row }"><el-tag v-if="row.originality_passed !== null && row.originality_passed !== undefined" size="small" effect="plain" :type="row.originality_passed ? 'success' : 'danger'">{{ row.originality_passed ? t('bible.forge.pass') : t('bible.forge.failed') }}</el-tag></template>
        </el-table-column>
        <el-table-column :label="t('bible.forge.style')" width="80" align="right">
          <template #default="{ row }">{{ typeof row.style_score === 'number' ? Math.round(row.style_score * 100) + '%' : '—' }}</template>
        </el-table-column>
        <el-table-column :label="t('bible.forge.repairs')" width="80" prop="repair_attempts" align="right" />
        <el-table-column :label="t('bible.forge.canonRevision')" width="90" align="right">
          <template #default="{ row }">{{ row.canon_revision_before }} → {{ row.canon_revision_after ?? '—' }}</template>
        </el-table-column>
        <el-table-column :label="t('bible.forge.error')" min-width="200">
          <template #default="{ row }"><span v-if="row.error" class="muted err">{{ row.error.code }}: {{ row.error.message }}</span></template>
        </el-table-column>
        <el-table-column width="100">
          <template #default="{ row }"><el-button v-if="row.chapter_card_id" size="small" text type="primary" @click="emit('open-card', row.chapter_card_id)">{{ t('bible.openCard') }}</el-button></template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, toRef, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { useLLMConfigStore } from '@renderer/stores/useLLMConfigStore'
import * as forgeApi from '@renderer/api/forge'
import { FORGE_STEPS, useForgePipeline } from '@renderer/composables/useForgePipeline'

const props = defineProps<{ projectId?: number }>()
const emit = defineEmits<{ (e: 'open-card', id: number): void; (e: 'original-created', projectId: number): void }>()
const { t } = useI18n()
const llmStore = useLLMConfigStore()
const forge = useForgePipeline(forgeApi, toRef(props, 'projectId'))

const originalName = ref('')
const llmConfigId = ref<number | undefined>(undefined)
const chapter = ref(1)
const source = computed(() => forge.source.value)
const manifest = computed(() => forge.manifest.value)

function runType(s: string) {
  if (s === 'committed') return 'success'
  if (s === 'rejected' || s === 'compile_failed' || s === 'sync_failed' || s === 'error') return 'danger'
  return 'info'
}
async function createOriginal() {
  const res = await forge.createOriginal(originalName.value)
  if (res) {
    ElMessage.success(t('bible.forge.originalCreated', { id: res.project_id }))
    emit('original-created', res.project_id)
  }
}
async function runChapter() {
  if (!llmConfigId.value) return
  const res = await forge.run(chapter.value, llmConfigId.value, chapter.value < forge.nextChapter.value)
  if (res) {
    const status = String(res.status)
    if (status === 'committed') ElMessage.success(t('bible.forge.runCommitted', { n: chapter.value }))
    else ElMessage.warning(t('bible.forge.runEnded', { status }))
  }
}

watch(() => forge.nextChapter.value, (n) => { chapter.value = n })
watch(() => forge.error.value, (msg) => { if (msg) ElMessage.error(msg) })
watch(() => props.projectId, () => forge.refresh())
watch(() => llmStore.llmConfigs, (list) => { if (!llmConfigId.value) llmConfigId.value = list[0]?.id }, { immediate: true })
onMounted(async () => {
  try { if (!llmStore.llmConfigs.length) await llmStore.fetchLLMConfigs() } catch { /* ignore */ }
  await forge.refresh()
})
</script>

<style scoped>
.forge { display: flex; flex-direction: column; gap: 12px; }
.forge-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 8px; }
.title { margin: 0; font-size: 16px; }
.muted { color: var(--el-text-color-secondary); font-size: 12px; margin: 0; }
.mono { font-family: monospace; }
.err { color: var(--el-color-danger); }
.steps :deep(.el-step__title) { font-size: 12px; line-height: 1.3; word-break: normal; }
.board { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 12px; }
.cell { font-size: 13px; }
.row { display: flex; justify-content: space-between; align-items: center; gap: 8px; padding: 3px 0; }
.actions { display: flex; gap: 8px; align-items: center; justify-content: flex-end; flex-wrap: wrap; margin-top: 8px; }
.create { display: flex; gap: 8px; margin-top: 8px; }
.form-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 0 12px; }
.stale { margin-top: 6px; }
</style>
