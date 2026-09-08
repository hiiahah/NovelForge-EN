<template>
  <div class="brief">
    <div class="brief-cols">
      <section class="col must">
        <h4>{{ t('storyMemory.brief.must') }} <el-tag size="small" type="danger" effect="plain">{{ brief.must_address.length }}</el-tag></h4>
        <el-empty v-if="!brief.must_address.length" :image-size="40" :description="t('storyMemory.brief.none')" />
        <div v-for="(i, k) in brief.must_address" :key="k" class="item">
          <el-tag size="small" effect="plain">{{ i.kind }}</el-tag>
          <span class="text">{{ i.text }}</span>
          <span v-if="i.reason" class="muted">— {{ i.reason }}</span>
          <el-button v-if="i.card_id" size="small" text type="primary" @click="emit('open-card', i.card_id)">{{ t('bible.openCard') }}</el-button>
        </div>
      </section>
      <section class="col should">
        <h4>{{ t('storyMemory.brief.should') }} <el-tag size="small" type="warning" effect="plain">{{ brief.should_consider.length }}</el-tag></h4>
        <el-empty v-if="!brief.should_consider.length" :image-size="40" :description="t('storyMemory.brief.none')" />
        <div v-for="(i, k) in brief.should_consider" :key="k" class="item">
          <el-tag size="small" effect="plain">{{ i.kind }}</el-tag>
          <span class="text">{{ i.text }}</span>
          <span v-if="i.reason" class="muted">— {{ i.reason }}</span>
          <el-button v-if="i.card_id" size="small" text type="primary" @click="emit('open-card', i.card_id)">{{ t('bible.openCard') }}</el-button>
        </div>
      </section>
      <section class="col avoid">
        <h4>{{ t('storyMemory.brief.avoid') }} <el-tag size="small" type="info" effect="plain">{{ brief.avoid.length }}</el-tag></h4>
        <el-empty v-if="!brief.avoid.length" :image-size="40" :description="t('storyMemory.brief.none')" />
        <div v-for="(i, k) in brief.avoid" :key="k" class="item">
          <el-tag size="small" effect="plain">{{ i.kind }}</el-tag>
          <span class="text">{{ i.text }}</span>
          <span v-if="i.reason" class="muted">— {{ i.reason }}</span>
        </div>
      </section>
    </div>
    <section v-if="brief.rhythm_advice?.length" class="rhythm">
      <h4>{{ t('storyMemory.brief.rhythm') }}</h4>
      <ul><li v-for="(r, i) in brief.rhythm_advice" :key="i">{{ r }}</li></ul>
    </section>
  </div>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import type { BriefView } from '@renderer/composables/useStoryMemory'

defineProps<{ brief: BriefView }>()
const emit = defineEmits<{ (e: 'open-card', id: number): void }>()
const { t } = useI18n()
</script>

<style scoped>
.brief { display: flex; flex-direction: column; gap: 10px; min-width: 0; color: var(--el-text-color-primary); }
.brief-cols { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 10px; }
.col { border: 1px solid var(--el-border-color-lighter); border-radius: 8px; padding: 10px; background: var(--el-fill-color-blank); min-width: 0; }
.col.must { border-top: 3px solid var(--el-color-danger); }
.col.should { border-top: 3px solid var(--el-color-warning); }
.col.avoid { border-top: 3px solid var(--el-color-info); }
h4 { margin: 0 0 8px; font-size: 13px; display: flex; gap: 6px; align-items: center; }
.item { display: flex; flex-wrap: wrap; gap: 6px; align-items: baseline; font-size: 13px; line-height: 1.5; padding: 4px 0; border-bottom: 1px dashed var(--el-border-color-lighter); }
.item:last-child { border-bottom: none; }
.text { flex: 1 1 200px; min-width: 0; }
.muted { color: var(--el-text-color-secondary); font-size: 12px; }
.rhythm { border: 1px solid var(--el-border-color-lighter); border-radius: 8px; padding: 10px; background: var(--el-fill-color-light); }
.rhythm ul { margin: 0; padding-left: 18px; font-size: 13px; line-height: 1.55; }
</style>
