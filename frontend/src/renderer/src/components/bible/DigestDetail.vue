<template>
  <div class="digest-detail">
    <div class="dd-head">
      <h3 class="dd-title">Ch.{{ digest.chapter_number }} <span v-if="digest.title">— {{ digest.title }}</span></h3>
      <div class="dd-tags">
        <el-tag size="small" effect="plain">POV {{ digest.pov || '?' }}</el-tag>
        <el-tag size="small" effect="plain">{{ digest.dominant_function }}</el-tag>
        <el-tag v-if="digest.story_time" size="small" effect="plain" type="info">{{ digest.story_time }}</el-tag>
        <el-tag v-if="digest.stale" size="small" type="warning" effect="dark">{{ t('storyMemory.stale') }}</el-tag>
        <span class="muted">{{ t('storyMemory.words', { n: digest.word_count }) }} · {{ t('storyMemory.tension', { n: digest.tension_end }) }} · {{ t('storyMemory.hookStrength', { n: digest.hook_strength }) }}</span>
      </div>
    </div>

    <p class="one-line">{{ digest.one_line }}</p>
    <p class="summary">{{ digest.summary }}</p>

    <div class="grid">
      <section v-if="digest.ending_state" class="block accent">
        <h4>{{ t('storyMemory.dd.endingState') }}</h4>
        <p>{{ digest.ending_state }}</p>
        <p v-if="digest.last_paragraph_gist" class="muted">{{ t('storyMemory.dd.lastParagraph') }}: {{ digest.last_paragraph_gist }}</p>
      </section>
      <section v-if="digest.continuity_risks?.length" class="block warn">
        <h4>{{ t('storyMemory.dd.risks') }}</h4>
        <ul><li v-for="(r, i) in digest.continuity_risks" :key="i">{{ r }}</li></ul>
      </section>
      <section v-if="digest.events?.length" class="block">
        <h4>{{ t('storyMemory.dd.events') }}</h4>
        <ol>
          <li v-for="(e, i) in digest.events" :key="i">
            <el-tag size="small" :type="sigType(e.significance)" effect="plain" class="sig">{{ e.significance }}</el-tag>
            {{ e.summary }}<span v-if="e.location" class="muted"> @ {{ e.location }}</span>
            <div v-if="e.consequence" class="muted">→ {{ e.consequence }}</div>
          </li>
        </ol>
      </section>
      <section v-if="digest.state_changes?.length" class="block">
        <h4>{{ t('storyMemory.dd.stateChanges') }}</h4>
        <ul>
          <li v-for="(s, i) in digest.state_changes" :key="i">
            <b>{{ s.entity }}</b> <el-tag size="small" effect="plain">{{ s.kind }}</el-tag>
            <span v-if="s.before" class="muted">{{ s.before }} → </span>{{ s.after }}
            <el-tag v-if="s.permanent === false" size="small" type="info" effect="plain">{{ t('storyMemory.dd.temporary') }}</el-tag>
          </li>
        </ul>
      </section>
      <section v-if="digest.hooks_opened?.length" class="block">
        <h4>{{ t('storyMemory.dd.hooksOpened') }}</h4>
        <ul>
          <li v-for="(h, i) in digest.hooks_opened" :key="i">
            <el-tag size="small" :type="h.strength === 'strong' ? 'danger' : h.strength === 'medium' ? 'warning' : 'info'" effect="plain">{{ h.strength }}</el-tag>
            {{ h.hook }} <span class="muted">({{ h.hook_type }}<template v-if="h.expected_payoff_window">, {{ h.expected_payoff_window }}</template>)</span>
          </li>
        </ul>
      </section>
      <section v-if="digest.hooks_closed?.length" class="block">
        <h4>{{ t('storyMemory.dd.hooksClosed') }}</h4>
        <ul><li v-for="(h, i) in digest.hooks_closed" :key="i"><s class="muted">{{ h.hook }}</s> → {{ h.resolution }} <el-tag v-if="!h.complete" size="small" effect="plain">{{ t('storyMemory.dd.partial') }}</el-tag></li></ul>
      </section>
      <section v-if="digest.knowledge_deltas?.length" class="block">
        <h4>{{ t('storyMemory.dd.knowledge') }}</h4>
        <ul><li v-for="(k, i) in digest.knowledge_deltas" :key="i"><b>{{ k.entity }}</b> ← {{ k.learned }} <el-tag v-if="k.is_false_belief" size="small" type="danger" effect="plain">{{ t('storyMemory.dd.falseBelief') }}</el-tag><span v-if="k.how" class="muted"> ({{ k.how }})</span></li></ul>
      </section>
      <section v-if="digest.relationship_shifts?.length" class="block">
        <h4>{{ t('storyMemory.dd.relationships') }}</h4>
        <ul><li v-for="(r, i) in digest.relationship_shifts" :key="i">{{ r }}</li></ul>
      </section>
      <section v-if="digest.promises_made?.length" class="block">
        <h4>{{ t('storyMemory.dd.promises') }}</h4>
        <ul><li v-for="(p, i) in digest.promises_made" :key="i">{{ p }}</li></ul>
      </section>
      <section v-if="digest.objects_introduced?.length || digest.named_extras?.length || digest.locations?.length" class="block">
        <h4>{{ t('storyMemory.dd.introduced') }}</h4>
        <div class="chips">
          <el-tag v-for="l in digest.locations" :key="'l' + l" size="small" type="info" effect="plain">📍 {{ l }}</el-tag>
          <el-tag v-for="o in digest.objects_introduced" :key="'o' + o" size="small" effect="plain">🎒 {{ o }}</el-tag>
          <el-tag v-for="x in digest.named_extras" :key="'x' + x" size="small" type="warning" effect="plain">👤 {{ x }}</el-tag>
        </div>
      </section>
      <section v-if="digest.quotable_lines?.length" class="block">
        <h4>{{ t('storyMemory.dd.quotes') }}</h4>
        <blockquote v-for="(q, i) in digest.quotable_lines" :key="i">“{{ q.line }}”<span v-if="q.speaker" class="muted"> — {{ q.speaker }}</span><div v-if="q.why_memorable" class="muted">{{ q.why_memorable }}</div></blockquote>
      </section>
      <section v-if="digest.style_notes?.length || digest.rewards_delivered?.length" class="block">
        <h4>{{ t('storyMemory.dd.craft') }}</h4>
        <div v-if="digest.rewards_delivered?.length" class="chips"><el-tag v-for="r in digest.rewards_delivered" :key="r" size="small" type="success" effect="plain">{{ r }}</el-tag></div>
        <ul><li v-for="(s, i) in digest.style_notes" :key="i">{{ s }}</li></ul>
      </section>
    </div>
    <div class="muted foot">{{ t('storyMemory.dd.digestedAt', { at: digest.digested_at || '—' }) }}</div>
  </div>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import type { ChapterDigest } from '@renderer/api/storyMemory'

defineProps<{ digest: ChapterDigest }>()
const { t } = useI18n()

function sigType(s: string) { return s === 'pivotal' ? 'danger' : s === 'major' ? 'warning' : s === 'minor' ? 'info' : 'primary' }
</script>

<style scoped>
.digest-detail { display: flex; flex-direction: column; gap: 10px; min-width: 0; }
.dd-head { display: flex; flex-direction: column; gap: 6px; }
.dd-title { margin: 0; font-size: 16px; }
.dd-tags { display: flex; gap: 6px; flex-wrap: wrap; align-items: center; }
.muted { color: var(--el-text-color-secondary); font-size: 12px; }
.one-line { margin: 0; font-weight: 600; }
.summary { margin: 0; line-height: 1.6; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 10px; }
.block { border: 1px solid var(--el-border-color-lighter); border-radius: 8px; padding: 10px 12px; background: var(--el-fill-color-blank); min-width: 0; }
.block.accent { border-color: var(--el-color-primary-light-5); background: var(--el-color-primary-light-9); }
.block.warn { border-color: var(--el-color-warning-light-5); background: var(--el-color-warning-light-9); }
.block h4 { margin: 0 0 6px; font-size: 13px; }
.block ul, .block ol { margin: 0; padding-left: 18px; line-height: 1.55; font-size: 13px; }
.block p { margin: 0; line-height: 1.55; font-size: 13px; }
.sig { margin-right: 4px; }
.chips { display: flex; flex-wrap: wrap; gap: 4px; margin-bottom: 4px; }
blockquote { margin: 4px 0; padding-left: 10px; border-left: 3px solid var(--el-border-color); font-style: italic; font-size: 13px; }
.foot { text-align: right; }
</style>
