<template>
  <el-card shadow="never" class="run-panel" data-testid="run-panel">
    <div class="run-head">
      <div>
        <b>{{ t('bible.lab.run.title', { id: run.run_id }) }}</b>
        <el-tag size="small" :type="statusType" effect="dark" class="status" data-testid="run-status">{{ run.status }}</el-tag>
      </div>
      <div class="run-actions">
        <el-button size="small" @click="emit('refresh')">{{ t('bible.refresh') }}</el-button>
        <el-button v-if="active" size="small" type="danger" data-testid="cancel-btn" @click="emit('cancel')">{{ t('bible.lab.run.cancel') }}</el-button>
        <el-button v-if="resumable" size="small" type="warning" data-testid="resume-btn" @click="emit('resume')">{{ t('bible.lab.run.resume') }}</el-button>
      </div>
    </div>
    <el-progress :percentage="Math.round(run.percent || 0)" :stroke-width="12" :status="run.status === 'failed' ? 'exception' : run.status === 'succeeded' ? 'success' : undefined" />
    <div class="muted" data-testid="run-progress">
      <span v-if="run.current_node">{{ t('bible.lab.run.currentNode', { node: run.current_node }) }}</span>
      <span v-if="run.chapters_total"> · {{ t('bible.lab.run.chapters', { done: run.chapters_done, total: run.chapters_total, failed: run.chapters_failed }) }}</span>
      <span v-if="run.current_message"> · {{ run.current_message }}</span>
    </div>
    <el-alert v-if="run.error" type="error" :closable="false" show-icon :title="run.error" data-testid="run-error-text" />
    <el-collapse v-if="run.nodes?.length" class="nodes">
      <el-collapse-item :title="t('bible.lab.run.nodes', { n: run.nodes.length })">
        <div v-for="n in run.nodes" :key="n.node_id || ''" class="node-row">
          <el-tag size="small" :type="nodeType(n.status || undefined)" effect="plain">{{ n.status }}</el-tag>
          <span class="node-id">{{ n.node_id }}</span>
          <span v-if="n.error" class="muted node-error">{{ n.error }}</span>
        </div>
      </el-collapse-item>
    </el-collapse>
  </el-card>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import type { LabRunStatus } from '@renderer/api/lab'

const props = defineProps<{ run: LabRunStatus; active: boolean; resumable: boolean }>()
const emit = defineEmits<{ (e: 'cancel'): void; (e: 'resume'): void; (e: 'refresh'): void }>()
const { t } = useI18n()

const statusType = computed(() => {
  const s = props.run.status
  if (s === 'succeeded') return 'success'
  if (s === 'failed' || s === 'timeout') return 'danger'
  if (s === 'cancelled' || s === 'paused') return 'warning'
  return 'primary'
})
function nodeType(status: string | undefined) {
  if (status === 'success') return 'success'
  if (status === 'error') return 'danger'
  if (status === 'running') return 'primary'
  return 'info'
}
</script>

<style scoped>
.run-panel { margin-top: 4px; }
.run-head { display: flex; justify-content: space-between; align-items: center; gap: 8px; margin-bottom: 8px; }
.run-actions { display: flex; gap: 6px; }
.status { margin-left: 8px; }
.muted { color: var(--el-text-color-secondary); font-size: 12px; margin: 6px 0; }
.nodes { margin-top: 6px; }
.node-row { display: flex; gap: 8px; align-items: center; font-size: 12px; padding: 2px 0; }
.node-id { font-family: monospace; }
.node-error { color: var(--el-color-danger); }
</style>
