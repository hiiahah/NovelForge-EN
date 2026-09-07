<template>
  <el-card shadow="never" class="job-card" data-testid="job-progress">
    <div class="head">
      <div>
        <b>{{ t('autonomous.job', { id: job.id }) }}</b>
        <el-tag size="small" :type="statusType" effect="dark" class="status" data-testid="job-status">{{ t('autonomous.status.' + job.status, job.status) }}</el-tag>
        <el-tag size="small" effect="plain" class="status">{{ t('autonomous.modes.' + job.mode, job.mode) }}</el-tag>
      </div>
      <div class="actions">
        <el-button size="small" @click="emit('refresh')">{{ t('bible.refresh') }}</el-button>
        <el-button v-if="job.status === 'waiting_for_user' && job.waiting_for !== 'storyline_selection'" size="small" type="success" :loading="busy === 'approve'" data-testid="approve-btn" @click="emit('approve')">{{ t('autonomous.approve') }}</el-button>
        <el-button v-if="active" size="small" type="warning" :loading="busy === 'pause'" data-testid="pause-btn" @click="emit('pause')">{{ t('autonomous.pause') }}</el-button>
        <el-button v-if="job.status === 'paused'" size="small" type="primary" :loading="busy === 'resume'" data-testid="resume-btn" @click="emit('resume')">{{ t('autonomous.resume') }}</el-button>
        <el-button v-if="!['completed', 'cancelled', 'failed'].includes(job.status)" size="small" type="danger" plain :loading="busy === 'cancel'" @click="emit('cancel')">{{ t('autonomous.cancel') }}</el-button>
        <el-button v-if="['completed', 'cancelled', 'failed'].includes(job.status)" size="small" type="primary" @click="emit('newRun')">{{ t('autonomous.newRun') }}</el-button>
      </div>
    </div>
    <el-progress :percentage="Math.round(job.progress_percent || 0)" :stroke-width="14" :status="job.status === 'failed' ? 'exception' : job.status === 'completed' ? 'success' : undefined" />
    <div class="muted" data-testid="job-message">{{ job.progress_message }}</div>
    <el-alert v-if="job.error" type="error" :closable="false" show-icon :title="`${job.error.category}: ${job.error.message}`" class="alert" />
    <el-alert v-else-if="job.status === 'waiting_for_user'" type="info" :closable="false" show-icon :title="t('autonomous.waiting.' + (job.waiting_for || 'approval'))" class="alert" />
    <div class="stage-list">
      <div v-for="s in stages" :key="s" class="stage" :class="stageClass(s)">
        <el-icon v-if="stageClass(s) === 'done'"><Check /></el-icon>
        <el-icon v-else-if="stageClass(s) === 'current'" class="spin"><Loading /></el-icon>
        <span v-else class="dot" />
        <span>{{ t('autonomous.stage.' + s, s) }}</span>
        <span v-if="s === 'CHAPTER_GENERATION_LOOP' && job.chapter_count" class="muted"> {{ job.chapters_committed }}/{{ job.chapter_count }}</span>
      </div>
    </div>
    <div class="meta muted">
      <span>{{ t('autonomous.meta.calls', { n: job.model_calls }) }}</span>
      <span>{{ t('autonomous.meta.tokens', { i: Number(job.input_tokens).toLocaleString(), o: Number(job.output_tokens).toLocaleString() }) }}</span>
      <span v-if="eta">{{ t('autonomous.meta.eta', { eta }) }}</span>
      <span v-if="retries">{{ t('autonomous.meta.retries', { n: retries }) }}</span>
    </div>
  </el-card>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { Check, Loading } from '@element-plus/icons-vue'
import type { AutonomousJob } from '@renderer/api/autonomous'

const props = defineProps<{ job: AutonomousJob; active: boolean; stages: string[]; busy: string | null }>()
const emit = defineEmits<{ (e: 'pause'): void; (e: 'resume'): void; (e: 'cancel'): void; (e: 'approve'): void; (e: 'refresh'): void; (e: 'newRun'): void }>()
const { t } = useI18n()

const statusType = computed(() => {
  const s = props.job.status
  if (s === 'completed') return 'success'
  if (s === 'failed' || s === 'cancelled') return 'danger'
  if (s === 'paused' || s === 'waiting_for_user') return 'warning'
  return 'primary'
})
function stageClass(s: string): 'done' | 'current' | 'todo' {
  const all = props.job.stages || []
  const cur = all.indexOf(props.job.stage)
  const idx = all.indexOf(s)
  if (idx < 0) return 'todo'
  if (idx < cur || props.job.status === 'completed') return 'done'
  if (idx === cur) return 'current'
  return 'todo'
}
const retries = computed(() => (props.job.attempts || []).filter((a) => a.status === 'failed').length)
const eta = computed(() => {
  if (!props.job.started_at || props.job.status === 'completed' || props.job.progress_percent <= 1) return ''
  const elapsed = (Date.now() - new Date(props.job.started_at).getTime()) / 1000
  const remain = elapsed * (100 - props.job.progress_percent) / props.job.progress_percent
  if (!isFinite(remain) || remain <= 0) return ''
  return remain > 3600 ? `${(remain / 3600).toFixed(1)} h` : `${Math.round(remain / 60)} min`
})
</script>

<style scoped>
.job-card { margin: 0; }
.head { display: flex; justify-content: space-between; align-items: center; gap: 8px; margin-bottom: 8px; flex-wrap: wrap; }
.actions { display: flex; gap: 6px; }
.status { margin-left: 8px; }
.muted { color: var(--el-text-color-secondary); font-size: 12px; margin: 6px 0; }
.alert { margin: 6px 0; }
.stage-list { display: flex; flex-wrap: wrap; gap: 6px 14px; margin: 8px 0; }
.stage { display: flex; align-items: center; gap: 4px; font-size: 12px; color: var(--el-text-color-secondary); }
.stage.done { color: var(--el-color-success); }
.stage.current { color: var(--el-color-primary); font-weight: 600; }
.dot { width: 8px; height: 8px; border-radius: 50%; background: var(--el-border-color); display: inline-block; }
.spin { animation: spin 1s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
.meta { display: flex; gap: 16px; flex-wrap: wrap; }
</style>
