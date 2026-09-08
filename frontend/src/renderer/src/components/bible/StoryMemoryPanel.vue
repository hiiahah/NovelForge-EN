<template>
  <div class="story-memory" v-loading="sm.loading.value">
    <!-- Header: health + coverage + actions -->
    <div class="sm-head">
      <div class="health" v-if="sm.health.value">
        <div class="health-score" :class="'grade-' + sm.health.value.grade">
          <span class="num">{{ sm.health.value.score }}</span>
          <span class="grade">{{ sm.health.value.grade }}</span>
        </div>
        <div class="health-meta">
          <div class="title">{{ t('storyMemory.healthTitle') }}</div>
          <div class="muted">{{ sm.health.value.summary }}</div>
          <div class="dims">
            <el-tooltip v-for="d in sm.health.value.dimensions" :key="d.key" :content="d.detail + (d.fix_hint ? ' — ' + d.fix_hint : '')" placement="bottom">
              <el-tag size="small" effect="plain" :type="healthTagType(d.score)">{{ d.label }} {{ d.score }}</el-tag>
            </el-tooltip>
          </div>
        </div>
      </div>
      <div class="coverage">
        <div class="title">{{ t('storyMemory.coverageTitle') }}</div>
        <el-progress :percentage="sm.percent.value" :status="sm.percent.value === 100 ? 'success' : undefined" :stroke-width="10" />
        <div class="muted">{{ t('storyMemory.coverageDetail', { fresh: sm.coverage.value.written.length - sm.pending.value.filter(n => sm.coverage.value.written.includes(n)).length, total: sm.coverage.value.written.length, stale: sm.coverage.value.stale.length }) }}</div>
        <div class="actions">
          <el-select v-model="llmConfigId" size="small" filterable :placeholder="t('bible.selectModel')" style="width: 200px">
            <el-option v-for="llm in llmConfigs" :key="llm.id" :label="llm.display_name" :value="Number(llm.id)" />
          </el-select>
          <el-button size="small" type="primary" :disabled="!llmConfigId || !sm.pending.value.length" :loading="sm.busy.value === 'batch'" @click="digestPending(false)">
            {{ t('storyMemory.digestPending', { n: sm.pending.value.length }) }}
          </el-button>
          <el-button size="small" :disabled="!llmConfigId || !sm.coverage.value.written.length" :loading="sm.busy.value === 'batch'" @click="digestPending(true)">{{ t('storyMemory.redigestAll') }}</el-button>
          <el-button size="small" @click="settingsVisible = true">{{ t('storyMemory.settings') }}</el-button>
        </div>
      </div>
    </div>

    <el-tabs v-model="tab" class="sm-tabs">
      <!-- Timeline of digests -->
      <el-tab-pane :label="t('storyMemory.tabTimeline')" name="timeline">
        <el-empty v-if="!sm.digests.value.length && !sm.coverage.value.written.length" :description="t('storyMemory.emptyNoChapters')" :image-size="70" />
        <el-empty v-else-if="!sm.digests.value.length" :description="t('storyMemory.emptyNoDigests')" :image-size="70" />
        <div v-else class="timeline">
          <div class="tension-strip" :title="t('storyMemory.tensionStrip')">
            <div v-for="d in sm.digests.value" :key="'t' + d.chapter_number" class="tension-bar" :class="{ stale: d.stale, active: selectedChapter === d.chapter_number }" :style="{ height: (8 + d.tension_end * 5) + 'px' }" @click="select(d.chapter_number)">
              <span class="bar-label">{{ d.chapter_number }}</span>
            </div>
          </div>
          <div class="digest-list">
            <div v-for="d in sm.digests.value" :key="d.chapter_number" class="digest-row" :class="{ active: selectedChapter === d.chapter_number, stale: d.stale }" @click="select(d.chapter_number)">
              <div class="row-head">
                <b>Ch.{{ d.chapter_number }}</b>
                <span class="row-title">{{ d.title || d.one_line }}</span>
                <el-tag v-if="d.stale" size="small" type="warning" effect="dark">{{ t('storyMemory.stale') }}</el-tag>
                <el-tag size="small" effect="plain">{{ d.dominant_function }}</el-tag>
                <span class="muted">POV {{ d.pov || '?' }}</span>
              </div>
              <div class="row-line muted">{{ d.one_line }}</div>
              <div class="row-meta muted">
                <span>{{ t('storyMemory.hooksOpened', { n: d.hooks_opened }) }}</span>
                <span>{{ t('storyMemory.hooksClosed', { n: d.hooks_closed }) }}</span>
                <span>{{ t('storyMemory.stateChanges', { n: d.state_changes }) }}</span>
                <span>{{ t('storyMemory.tension', { n: d.tension_end }) }}</span>
                <span>{{ t('storyMemory.hookStrength', { n: d.hook_strength }) }}</span>
                <span v-if="d.rewards_delivered?.length">{{ t('storyMemory.rewards') }}: {{ d.rewards_delivered.join(', ') }}</span>
              </div>
              <div class="row-actions" @click.stop>
                <el-button size="small" text type="primary" :disabled="!llmConfigId" :loading="sm.busy.value === 'digest' && busyChapter === d.chapter_number" @click="redigest(d.chapter_number)">{{ t('storyMemory.redigest') }}</el-button>
                <el-button size="small" text @click="emit('open-card', d.card_id)">{{ t('bible.openCard') }}</el-button>
              </div>
            </div>
            <div v-for="n in sm.coverage.value.missing" :key="'m' + n" class="digest-row missing">
              <div class="row-head"><b>Ch.{{ n }}</b><span class="muted">{{ t('storyMemory.notDigested') }}</span></div>
              <div class="row-actions">
                <el-button size="small" text type="primary" :disabled="!llmConfigId" :loading="sm.busy.value === 'digest' && busyChapter === n" @click="redigest(n)">{{ t('storyMemory.digestNow') }}</el-button>
              </div>
            </div>
          </div>
        </div>
      </el-tab-pane>

      <!-- Digest detail -->
      <el-tab-pane :label="t('storyMemory.tabDigest')" name="digest" :disabled="!sm.selectedDigest.value">
        <DigestDetail v-if="sm.selectedDigest.value" :digest="sm.selectedDigest.value" />
      </el-tab-pane>

      <!-- Story so far (what generation sees) -->
      <el-tab-pane :label="t('storyMemory.tabRecap')" name="recap">
        <div v-if="sm.storySoFar.value" class="recap">
          <div class="recap-meta muted">
            {{ t('storyMemory.recapMeta', { through: sm.storySoFar.value.through_chapter, next: sm.storySoFar.value.next_chapter, used: sm.storySoFar.value.used_chars, budget: sm.storySoFar.value.budget_chars }) }}
            <el-button size="small" text type="primary" @click="copy(sm.storySoFar.value?.text || '')">{{ t('storyMemory.copy') }}</el-button>
          </div>
          <el-alert v-if="sm.storySoFar.value.missing_chapters.length" type="warning" :closable="false" show-icon :title="t('storyMemory.recapMissing', { list: sm.storySoFar.value.missing_chapters.join(', ') })" />
          <el-alert v-if="sm.storySoFar.value.stale_chapters.length" type="info" :closable="false" show-icon :title="t('storyMemory.recapStale', { list: sm.storySoFar.value.stale_chapters.join(', ') })" />
          <pre class="pre" v-if="sm.storySoFar.value.text">{{ sm.storySoFar.value.text }}</pre>
          <el-empty v-else :description="t('storyMemory.emptyNoDigests')" :image-size="60" />
        </div>
      </el-tab-pane>

      <!-- World state -->
      <el-tab-pane :label="t('storyMemory.tabState')" name="state">
        <el-empty v-if="!sm.storySoFar.value?.carry_forward?.length" :description="t('storyMemory.emptyState')" :image-size="60" />
        <el-table v-else :data="sm.storySoFar.value.carry_forward" size="small" stripe>
          <el-table-column prop="entity" :label="t('storyMemory.colEntity')" width="180">
            <template #default="{ row }"><b>{{ row.entity }}</b> <el-tag v-if="!row.alive" size="small" type="danger" effect="dark">{{ t('storyMemory.dead') }}</el-tag></template>
          </el-table-column>
          <el-table-column prop="location" :label="t('storyMemory.colLocation')" width="200" />
          <el-table-column :label="t('storyMemory.colState')">
            <template #default="{ row }">
              <div v-for="(v, k) in row.states" :key="k" class="state-line"><el-tag size="small" effect="plain">{{ k }}</el-tag> {{ v }}</div>
            </template>
          </el-table-column>
          <el-table-column prop="last_seen_chapter" :label="t('storyMemory.colLastSeen')" width="100" />
        </el-table>
      </el-tab-pane>

      <!-- Dangling hooks -->
      <el-tab-pane name="hooks">
        <template #label>
          {{ t('storyMemory.tabHooks') }}
          <el-tag v-if="overdueCount" size="small" type="danger" effect="dark" class="tab-badge">{{ overdueCount }}</el-tag>
        </template>
        <el-empty v-if="!sm.storySoFar.value?.dangling_hooks?.length" :description="t('storyMemory.emptyHooks')" :image-size="60" />
        <div v-else class="hooks">
          <el-alert v-for="(h, i) in sm.storySoFar.value.dangling_hooks" :key="i" :type="h.overdue ? 'error' : h.strength === 'strong' ? 'warning' : 'info'" :closable="false" show-icon>
            <template #title>
              <span class="hook-text">{{ h.hook }}</span>
              <span class="hook-meta muted">— {{ h.hook_type }} · {{ h.strength }} · {{ t('storyMemory.openedCh', { n: h.opened_chapter }) }} · {{ t('storyMemory.openFor', { n: h.chapters_open }) }}<template v-if="h.expected_payoff_window"> · {{ h.expected_payoff_window }}</template></span>
            </template>
          </el-alert>
        </div>
      </el-tab-pane>

      <!-- Next chapter brief -->
      <el-tab-pane :label="t('storyMemory.tabBrief')" name="brief">
        <div class="brief-controls">
          <el-input-number v-model="briefChapter" :min="1" size="small" :controls-position="'right'" />
          <el-button size="small" type="primary" :loading="sm.busy.value === 'brief'" @click="loadBrief">{{ t('storyMemory.buildBrief') }}</el-button>
          <el-button v-if="sm.brief.value?.text" size="small" @click="copy(sm.brief.value?.text || '')">{{ t('storyMemory.copy') }}</el-button>
        </div>
        <NextChapterBriefView v-if="sm.brief.value" :brief="sm.brief.value" @open-card="(id) => emit('open-card', id)" />
        <el-empty v-else :description="t('storyMemory.briefHint')" :image-size="60" />
      </el-tab-pane>
    </el-tabs>

    <!-- Settings -->
    <el-dialog v-model="settingsVisible" :title="t('storyMemory.settings')" width="620px">
      <el-form v-if="settingsDraft" label-position="top" size="small" class="settings-form">
        <el-form-item :label="t('storyMemory.set.autoDigest')">
          <el-switch v-model="settingsDraft.auto_digest_on_save" />
          <span class="muted helper">{{ t('storyMemory.set.autoDigestHelp') }}</span>
        </el-form-item>
        <div class="grid2">
          <el-form-item :label="t('storyMemory.set.minWords')"><el-input-number v-model="settingsDraft.auto_digest_min_words" :min="50" :step="50" /></el-form-item>
          <el-form-item :label="t('storyMemory.set.digestModel')">
            <el-select v-model="settingsDraft.digest_llm_config_id" clearable filterable :placeholder="t('storyMemory.set.digestModelPlaceholder')" style="width: 100%">
              <el-option v-for="llm in llmConfigs" :key="llm.id" :label="llm.display_name" :value="Number(llm.id)" />
            </el-select>
          </el-form-item>
          <el-form-item :label="t('storyMemory.set.recentWindow')"><el-input-number v-model="settingsDraft.recent_window" :min="1" :max="12" /></el-form-item>
          <el-form-item :label="t('storyMemory.set.midWindow')"><el-input-number v-model="settingsDraft.mid_window" :min="0" :max="60" /></el-form-item>
          <el-form-item :label="t('storyMemory.set.budget')"><el-input-number v-model="settingsDraft.recap_budget_chars" :min="1000" :max="60000" :step="500" /></el-form-item>
          <el-form-item :label="t('storyMemory.set.hookOverdue')"><el-input-number v-model="settingsDraft.hook_overdue_chapters" :min="1" :max="100" /></el-form-item>
        </div>
        <el-form-item :label="t('storyMemory.set.injectRecap')"><el-switch v-model="settingsDraft.inject_into_continuation" /></el-form-item>
        <el-form-item :label="t('storyMemory.set.injectBrief')"><el-switch v-model="settingsDraft.inject_brief_into_continuation" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="settingsVisible = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" :loading="sm.busy.value === 'settings'" @click="saveSettings">{{ t('common.save') }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, toRef, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { getAIConfigOptions } from '@renderer/api/ai'
import type { StoryMemorySettings } from '@renderer/api/storyMemory'
import { healthTagType, useStoryMemory } from '@renderer/composables/useStoryMemory'
import DigestDetail from './DigestDetail.vue'
import NextChapterBriefView from './NextChapterBriefView.vue'

const props = defineProps<{ projectId?: number; refreshSeq?: number }>()
const emit = defineEmits<{ (e: 'open-card', id: number): void }>()
const { t } = useI18n()

const sm = useStoryMemory(toRef(props, 'projectId'))
const tab = ref<'timeline' | 'digest' | 'recap' | 'state' | 'hooks' | 'brief'>('timeline')
const llmConfigs = ref<Array<{ id: number; display_name: string }>>([])
const llmConfigId = ref<number | null>(null)
const selectedChapter = ref<number | null>(null)
const busyChapter = ref<number | null>(null)
const settingsVisible = ref(false)
const settingsDraft = ref<StoryMemorySettings | null>(null)
const briefChapter = ref<number>(1)

const overdueCount = computed(() => (sm.storySoFar.value?.dangling_hooks || []).filter(h => h.overdue).length)

async function select(n: number) {
  selectedChapter.value = n
  try { await sm.openDigest(n); tab.value = 'digest' } catch (e) { console.error(e) }
}

async function redigest(n: number) {
  if (!llmConfigId.value) return
  busyChapter.value = n
  try {
    await sm.digestOne(n, llmConfigId.value, true)
    ElMessage.success(t('storyMemory.digestDone', { n }))
    if (selectedChapter.value === n) await sm.openDigest(n)
  } catch (e) { console.error(e); ElMessage.error(t('storyMemory.digestFailed')) } finally { busyChapter.value = null }
}

async function digestPending(force: boolean) {
  if (!llmConfigId.value) return
  try {
    const res = await sm.digestPending(llmConfigId.value, force)
    if (res) {
      const failed = Object.keys(res.failed || {}).length
      ElMessage[failed ? 'warning' : 'success'](t('storyMemory.batchDone', { ok: (res.digested || []).length, failed }))
    }
  } catch (e) { console.error(e); ElMessage.error(t('storyMemory.digestFailed')) }
}

async function loadBrief() {
  try { await sm.loadBrief(briefChapter.value) } catch (e) { console.error(e) }
}

async function copy(text: string) {
  try { await navigator.clipboard.writeText(text); ElMessage.success(t('storyMemory.copied')) } catch { ElMessage.error(t('storyMemory.copyFailed')) }
}

async function saveSettings() {
  if (!settingsDraft.value) return
  try { await sm.updateSettings(settingsDraft.value); settingsVisible.value = false; ElMessage.success(t('storyMemory.settingsSaved')); await sm.refresh() } catch (e) { console.error(e) }
}

watch(settingsVisible, (v) => { if (v && sm.settings.value) settingsDraft.value = { ...sm.settings.value } })
watch(() => sm.storySoFar.value?.next_chapter, (n) => { if (n) briefChapter.value = n })
watch(() => props.projectId, () => sm.refresh())
watch(() => props.refreshSeq, () => sm.refresh())
watch(() => sm.settings.value?.digest_llm_config_id, (id) => { if (id && !llmConfigId.value) llmConfigId.value = Number(id) })

onMounted(async () => {
  try {
    const opts = await getAIConfigOptions()
    llmConfigs.value = (opts as any)?.llm_configs || []
    if (!llmConfigId.value && llmConfigs.value.length) llmConfigId.value = Number(llmConfigs.value[0].id)
  } catch { /* ignore */ }
  await sm.refresh()
})
</script>

<style scoped>
.story-memory { display: flex; flex-direction: column; gap: 12px; min-width: 0; color: var(--el-text-color-primary); }
.sm-head { display: grid; grid-template-columns: minmax(260px, 1fr) minmax(280px, 1.2fr); gap: 14px; }
@container (max-width: 760px) { .sm-head { grid-template-columns: 1fr; } }
.health { display: flex; gap: 12px; align-items: flex-start; padding: 12px; border: 1px solid var(--el-border-color-lighter); border-radius: 10px; background: var(--el-fill-color-blank); }
.health-score { display: flex; flex-direction: column; align-items: center; justify-content: center; width: 72px; height: 72px; border-radius: 12px; color: #fff; flex-shrink: 0; }
.health-score .num { font-size: 26px; font-weight: 700; line-height: 1; }
.health-score .grade { font-size: 12px; opacity: 0.9; }
.grade-A { background: var(--el-color-success); } .grade-B { background: var(--el-color-primary); } .grade-C { background: var(--el-color-warning); } .grade-D { background: var(--el-color-danger-light-3); } .grade-F { background: var(--el-color-danger); }
.health-meta { min-width: 0; display: flex; flex-direction: column; gap: 4px; }
.title { font-weight: 600; }
.muted { color: var(--el-text-color-secondary); font-size: 12px; }
.dims { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 2px; }
.coverage { padding: 12px; border: 1px solid var(--el-border-color-lighter); border-radius: 10px; display: flex; flex-direction: column; gap: 6px; background: var(--el-fill-color-blank); }
.actions { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; margin-top: 4px; }
.sm-tabs { min-width: 0; }
.tab-badge { margin-left: 6px; }
.timeline { display: flex; flex-direction: column; gap: 10px; }
.tension-strip { display: flex; align-items: flex-end; gap: 3px; height: 64px; padding: 4px 6px; border-radius: 8px; background: var(--el-fill-color-light); overflow-x: auto; }
.tension-bar { flex: 0 0 18px; min-width: 18px; background: var(--el-color-primary-light-5); border-radius: 3px 3px 0 0; cursor: pointer; position: relative; transition: background 0.15s; }
.tension-bar:hover, .tension-bar.active { background: var(--el-color-primary); }
.tension-bar.stale { background: var(--el-color-warning-light-5); }
.bar-label { position: absolute; bottom: -2px; left: 0; right: 0; text-align: center; font-size: 9px; color: var(--el-text-color-regular); transform: translateY(100%); }
.digest-list { display: flex; flex-direction: column; gap: 6px; margin-top: 14px; }
.digest-row { border: 1px solid var(--el-border-color-lighter); border-radius: 8px; padding: 8px 10px; cursor: pointer; display: flex; flex-direction: column; gap: 4px; background: var(--el-fill-color-blank); }
.digest-row:hover, .digest-row.active { border-color: var(--el-color-primary); }
.digest-row.stale { border-left: 3px solid var(--el-color-warning); }
.digest-row.missing { border-style: dashed; cursor: default; }
.row-head { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.row-title { font-weight: 500; }
.row-meta { display: flex; gap: 12px; flex-wrap: wrap; }
.row-actions { display: flex; justify-content: flex-end; gap: 4px; }
.recap { display: flex; flex-direction: column; gap: 8px; }
.recap-meta { display: flex; justify-content: space-between; align-items: center; }
.pre { white-space: pre-wrap; word-break: break-word; font-size: 12px; line-height: 1.55; padding: 10px; border-radius: 8px; background: var(--el-fill-color-light); color: var(--el-text-color-primary); max-height: 60vh; overflow: auto; margin: 0; }
.state-line { display: flex; gap: 6px; align-items: baseline; margin: 2px 0; }
.hooks { display: flex; flex-direction: column; gap: 6px; }
.hook-text { font-weight: 500; }
.hook-meta { margin-left: 6px; }
.brief-controls { display: flex; gap: 8px; align-items: center; margin-bottom: 10px; flex-wrap: wrap; }
.settings-form .grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 0 14px; }
.helper { margin-left: 10px; }
</style>
