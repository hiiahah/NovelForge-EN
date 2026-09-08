<template>
  <el-card class="tool-card craft-grade" shadow="never" data-testid="craft-grade">
    <template #header>
      <div class="card-head">
        <span>{{ t('craft.gradeTitle') }}</span>
        <el-tag v-if="grade" size="small" effect="dark" :type="verdictType(grade.critic.verdict)" data-testid="craft-verdict">
          {{ t('craft.verdict.' + grade.critic.verdict) }} · {{ grade.critic.overall.toFixed(1) }}
        </el-tag>
      </div>
    </template>
    <p class="hint">{{ t('craft.gradeHint') }}</p>
    <div class="row">
      <el-button size="small" type="primary" :loading="busy" @click="run" data-testid="craft-grade-btn">{{ t('craft.gradeNow') }}</el-button>
      <span v-if="grade" class="muted">{{ t('craft.words', { n: grade.words }) }} · {{ t('craft.tics', { n: grade.tic_summary.total }) }}</span>
    </div>

    <template v-if="grade">
      <div class="scores">
        <div v-for="dim in DIMENSIONS" :key="dim" class="score" :title="t('craft.dim.' + dim + 'Hint')">
          <span class="score-label">{{ t('craft.dim.' + dim) }}</span>
          <el-progress :percentage="(scores[dim] ?? 0) * 10" :stroke-width="8" :color="scoreColor(scores[dim] ?? 0)" :show-text="false" />
          <b class="score-num">{{ scores[dim] ?? "—" }}</b>
        </div>
      </div>

      <div class="hook" :class="{ soft: grade.hook.is_soft }">
        <div class="hook-head">
          <b>{{ t('craft.hook') }}</b>
          <el-tag size="small" effect="plain" :type="grade.hook.is_soft ? 'warning' : 'success'">{{ t('craft.hookType.' + grade.hook.hook_type) }} · {{ grade.hook.strength }}/10</el-tag>
        </div>
        <div class="muted">{{ t('craft.endingClass', { c: t('craft.ending.' + grade.hook.ending_class) }) }}</div>
        <div v-if="grade.hook.is_soft" class="muted">💡 {{ t('craft.hookSuggest', { h: t('craft.hookType.' + grade.hook.suggested_hook) }) }}</div>
        <div class="payoffs">
          <span class="muted">{{ t('craft.payoffs') }}:</span>
          <el-tag v-for="p in grade.hook.micro_payoffs" :key="p" size="small" effect="light" type="success">{{ t('craft.payoff.' + p) }}</el-tag>
          <el-tag v-if="grade.hook.payoff_missing" size="small" effect="light" type="danger">{{ t('craft.noPayoff') }}</el-tag>
        </div>
      </div>

      <el-collapse v-if="findings.length" class="findings">
        <el-collapse-item :title="t('craft.findings', { n: findings.length })" name="f">
          <div v-for="(f, k) in findings" :key="k" class="finding" :class="'sev-' + f.severity" @click="jumpTo(f)">
            <div class="finding-head">
              <el-tag size="small" effect="dark" :type="sevType(f.severity)">{{ f.severity }}</el-tag>
              <code class="code">{{ t('craft.dim.' + f.dimension) }}</code>
            </div>
            <div class="finding-msg">{{ f.problem }}</div>
            <div v-if="f.quote" class="finding-quote">“{{ f.quote }}”</div>
            <div v-if="f.fix" class="muted">✂ {{ f.fix }}</div>
          </div>
        </el-collapse-item>
      </el-collapse>
      <el-empty v-else :description="t('craft.clean')" :image-size="48" />
    </template>
  </el-card>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { gradeText, type CriticFinding, type GradeResponse } from '@renderer/api/craft'

const DIMENSIONS = ['authenticity', 'voice', 'interiority', 'dialogue', 'pacing', 'sensory', 'hook', 'payoff'] as const

const props = defineProps<{
  getText: () => Promise<string> | string
  pov?: string | null
  closingHook?: string | null
  wordTarget?: number | null
}>()
const emit = defineEmits<{ (e: 'jump', span: [number, number]): void }>()
const { t } = useI18n()

const busy = ref(false)
const grade = ref<GradeResponse | null>(null)
const lastText = ref('')
const scores = computed<Record<string, number>>(() => (grade.value?.critic.scores ?? {}) as Record<string, number>)
const findings = computed<CriticFinding[]>(() => grade.value?.critic.findings ?? [])

function verdictType(v: string) { return v === 'accept' ? 'success' : v === 'polish' ? 'warning' : 'danger' }
function sevType(s: string) { return s === 'critical' || s === 'high' ? 'danger' : s === 'medium' ? 'warning' : 'info' }
function scoreColor(s: number) { return s >= 8 ? 'var(--el-color-success)' : s >= 6 ? 'var(--el-color-warning)' : 'var(--el-color-danger)' }

async function run() {
  const text = (await props.getText())?.toString() ?? ''
  if (!text.trim()) { ElMessage.warning(t('craft.noText')); return }
  busy.value = true
  try {
    lastText.value = text
    grade.value = await gradeText({ text, pov: props.pov || '', closing_hook: props.closingHook || '', word_target: props.wordTarget || null })
  } catch (e) { console.error(e); ElMessage.error(t('craft.failed')) } finally { busy.value = false }
}

function jumpTo(f: CriticFinding) {
  if (!f.quote || !lastText.value) return
  const idx = lastText.value.indexOf(f.quote)
  if (idx >= 0) emit('jump', [idx, idx + f.quote.length])
}

defineExpose({ run })
</script>

<style scoped>
.craft-grade { color: var(--el-text-color-primary); }
.tool-card :deep(.el-card__header) { padding: 8px 12px; }
.tool-card :deep(.el-card__body) { padding: 10px 12px; display: flex; flex-direction: column; gap: 8px; }
.card-head { display: flex; justify-content: space-between; align-items: center; gap: 8px; font-weight: 600; font-size: 13px; }
.hint { margin: 0; font-size: 12px; color: var(--el-text-color-secondary); line-height: 1.5; }
.row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.muted { color: var(--el-text-color-secondary); font-size: 12px; }
.scores { display: grid; grid-template-columns: 1fr 1fr; gap: 4px 14px; }
.score { display: grid; grid-template-columns: 78px 1fr 20px; align-items: center; gap: 6px; font-size: 12px; }
.score-label { color: var(--el-text-color-regular); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.score-num { text-align: right; font-size: 12px; }
.hook { border: 1px solid var(--el-border-color-lighter); border-left: 4px solid var(--el-color-success); border-radius: 6px; padding: 6px 8px; display: flex; flex-direction: column; gap: 3px; background: var(--el-fill-color-blank); }
.hook.soft { border-left-color: var(--el-color-warning); }
.hook-head { display: flex; justify-content: space-between; align-items: center; font-size: 13px; }
.payoffs { display: flex; gap: 4px; align-items: center; flex-wrap: wrap; }
.findings :deep(.el-collapse-item__header) { font-size: 12px; height: 32px; }
.findings :deep(.el-collapse-item__content) { padding-bottom: 6px; display: flex; flex-direction: column; gap: 6px; }
.finding { border: 1px solid var(--el-border-color-lighter); border-left-width: 4px; border-radius: 6px; padding: 6px 8px; cursor: pointer; display: flex; flex-direction: column; gap: 3px; background: var(--el-fill-color-blank); }
.finding:hover { background: var(--el-fill-color-light); }
.finding.sev-critical, .finding.sev-high { border-left-color: var(--el-color-danger); }
.finding.sev-medium { border-left-color: var(--el-color-warning); }
.finding.sev-low { border-left-color: var(--el-color-info); }
.finding-head { display: flex; gap: 6px; align-items: center; }
.code { font-size: 11px; color: var(--el-text-color-regular); }
.finding-msg { font-size: 13px; line-height: 1.45; }
.finding-quote { font-size: 12px; font-style: italic; color: var(--el-text-color-regular); border-left: 2px solid var(--el-border-color); padding-left: 6px; }
</style>
