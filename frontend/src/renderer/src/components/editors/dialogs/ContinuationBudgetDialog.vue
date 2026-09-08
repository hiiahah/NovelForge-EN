<template>
  <el-dialog
    :model-value="visible"
    :title="t('misc.continuationConfig')"
    width="620px"
    @close="handleCancel"
  >
    <div class="dialog-body">
      <el-form label-position="top" size="small">
        <el-form-item :label="t('misc.continuationGuidance')">
          <el-input
            v-model="localGuidance"
            type="textarea"
            :rows="4"
            :placeholder="t('misc.continuationGuidancePlaceholder')"
          />
        </el-form-item>
        <el-form-item :label="t('misc.targetWordCount')">
          <el-input-number
            v-model="localTargetWordCount"
            :min="200"
            :max="200000"
            :step="100"
            :controls-position="'right'"
          />
          <span class="helper-text">{{ t('misc.targetWordCountHelper') }}</span>
        </el-form-item>
        <el-form-item :label="t('misc.wordControlMode')">
          <el-radio-group v-model="localWordControlMode">
            <el-radio-button label="prompt_only">{{ t('misc.promptOnly') }}</el-radio-button>
            <el-radio-button label="balanced">{{ t('misc.balancedMode') }}</el-radio-button>
          </el-radio-group>
          <div class="mode-help">
            <p v-if="localWordControlMode === 'prompt_only'">{{ t('misc.promptOnlyHelp') }}</p>
            <p v-else>{{ t('misc.balancedHelp') }}</p>
            <p v-if="localWordControlMode === 'balanced'">{{ t('misc.balancedTokenNote') }}</p>
          </div>
        </el-form-item>

        <el-divider content-position="left">{{ t('misc.storyMemorySection') }}</el-divider>
        <div class="memory-row">
          <el-checkbox v-model="localIncludeMemory">{{ t('misc.includeStoryMemory') }}</el-checkbox>
          <el-checkbox v-model="localIncludeBrief">{{ t('misc.includeChapterBrief') }}</el-checkbox>
        </div>
        <p class="mode-help">{{ t('misc.storyMemoryHelp') }}</p>
        <div v-if="memoryStatus" class="memory-status" :class="{ warn: memoryStatus.missing > 0 }">
          <span>{{ t('misc.memoryStatus', { digested: memoryStatus.digested, written: memoryStatus.written }) }}</span>
          <span v-if="memoryStatus.missing > 0"> · {{ t('misc.memoryMissing', { n: memoryStatus.missing }) }}</span>
          <span v-if="memoryStatus.overdue > 0"> · {{ t('misc.memoryOverdue', { n: memoryStatus.overdue }) }}</span>
        </div>
      </el-form>
    </div>
    <template #footer>
      <div class="dialog-footer">
        <el-button @click="handleCancel">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" @click="handleConfirm">{{ t('misc.startContinuation') }}</el-button>
      </div>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

export type ContinuationWordControlMode = 'prompt_only' | 'balanced'
export interface ContinuationMemoryStatus {
  written: number
  digested: number
  missing: number
  overdue: number
}
const { t } = useI18n()

const props = defineProps<{
  visible: boolean
  targetWordCount: number
  wordControlMode: ContinuationWordControlMode
  guidance: string
  includeStoryMemory?: boolean
  includeChapterBrief?: boolean
  memoryStatus?: ContinuationMemoryStatus | null
}>()

const emit = defineEmits<{
  (e: 'update:visible', value: boolean): void
  (
    e: 'confirm',
    payload: {
      targetWordCount: number
      wordControlMode: ContinuationWordControlMode
      guidance: string
      includeStoryMemory: boolean
      includeChapterBrief: boolean
    }
  ): void
}>()

const localTargetWordCount = ref<number>(3000)
const localWordControlMode = ref<ContinuationWordControlMode>('balanced')
const localGuidance = ref<string>('')
const localIncludeMemory = ref<boolean>(true)
const localIncludeBrief = ref<boolean>(true)

watch(
  () => props.visible,
  (visible) => {
    if (!visible) return
    localTargetWordCount.value = props.targetWordCount || 3000
    localWordControlMode.value = props.wordControlMode || 'balanced'
    localGuidance.value = props.guidance || ''
    localIncludeMemory.value = props.includeStoryMemory !== false
    localIncludeBrief.value = props.includeChapterBrief !== false
  },
  { immediate: true }
)

function handleCancel() {
  emit('update:visible', false)
}

function handleConfirm() {
  emit('confirm', {
    targetWordCount: Math.max(200, Math.floor(localTargetWordCount.value || 3000)),
    wordControlMode: localWordControlMode.value,
    guidance: localGuidance.value.trim(),
    includeStoryMemory: localIncludeMemory.value,
    includeChapterBrief: localIncludeBrief.value,
  })
  emit('update:visible', false)
}
</script>

<style scoped>
.dialog-body {
  padding: 4px 0;
}

.helper-text {
  margin-left: 12px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.mode-help {
  margin-top: 10px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  line-height: 1.6;
}

.mode-help p {
  margin: 0;
}

.memory-row {
  display: flex;
  gap: 18px;
  flex-wrap: wrap;
}

.memory-status {
  margin-top: 6px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  padding: 6px 8px;
  border-radius: 6px;
  background: var(--el-fill-color-light);
}

.memory-status.warn {
  color: var(--el-color-warning-dark-2);
  background: var(--el-color-warning-light-9);
}

.dialog-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}
.dialog-footer :deep(.el-button) { white-space: nowrap; }
</style>
