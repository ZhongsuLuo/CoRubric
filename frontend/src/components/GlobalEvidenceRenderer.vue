<script setup lang="ts">
import { computed } from 'vue'
import { gradingVisualConfig } from '../config/gradingVisualConfig'
import type { GlobalScorePoint, HighlightItem } from '../types/schema'
import { buildGlobalEvidenceBlocks } from '../utils/globalEvidence'
import { evidenceAnchorsWithLegacyImages } from '../utils/imageEvidence'
import { translate as $t } from '../i18n'

const props = defineProps<{
  markdown: string
  point: GlobalScorePoint
  active?: boolean
}>()

const apiBase = import.meta.env.VITE_API_BASE || '/api'
const normalizedAnchors = computed(() =>
  evidenceAnchorsWithLegacyImages(props.point.evidence_anchors, props.point.anchor_text),
)
const anchors = computed(() =>
  normalizedAnchors.value.length
    ? normalizedAnchors.value
    : [
        {
          anchor_type: 'text' as const,
          text: props.point.anchor_text || '',
          occurrence: props.point.anchor_occurrence || 0,
        },
      ],
)
const absenceAnchors = computed(() =>
  anchors.value.filter((anchor) => anchor.anchor_type === 'absence'),
)
const highlights = computed<HighlightItem[]>(() => {
  return anchors.value
    .filter((anchor) => anchor.anchor_type !== 'absence')
    .map((anchor, index) => ({
      id: props.point.event_id
        ? `global-${props.point.event_id}-${index}`
        : `global-${props.point.criterion_id}-${props.point.report_id}-${index}`,
      report_id: props.point.report_id,
      criteria_id: props.point.criterion_id,
      event_id: props.point.event_id,
      score: props.point.score,
      max_score: props.point.max_score,
      reason: props.point.score_reason || '',
      source: props.point.author === 'model' ? 'llm' : 'teacher',
      color: props.point.conflict
        ? gradingVisualConfig.conflict.highlightColor
        : props.point.author === 'model'
          ? gradingVisualConfig.model.highlightColor
          : gradingVisualConfig.teacher.highlightColor,
      anchor: {
        anchor_type: anchor.anchor_type || 'text',
        text: anchor.text || anchor.image_src || anchor.image_alt || '',
        occurrence: Number.isFinite(anchor.occurrence) ? Number(anchor.occurrence) : 0,
        start_offset: anchor.start_offset,
        end_offset: anchor.end_offset,
        image_src: anchor.image_src,
        image_alt: anchor.image_alt,
        description: anchor.description,
        region: anchor.region,
        expected_location: anchor.expected_location,
        missing_item: anchor.missing_item,
      },
    }))
})

const evidence = computed(() =>
  buildGlobalEvidenceBlocks(
    props.markdown,
    highlights.value,
    apiBase,
    props.active ? highlights.value[0]?.id || null : null,
  ),
)
</script>

<template>
  <div
    v-if="evidence.found || absenceAnchors.length"
    class="global-evidence-blocks space-y-3"
    data-testid="global-evidence-found"
  >
    <template v-for="(block, index) in evidence.blocks" :key="index">
      <div
        v-if="block.separatedBefore"
        class="flex items-center gap-3 py-1 text-slate-300"
        data-testid="global-evidence-gap"
        aria-hidden="true"
      >
        <span class="h-px flex-1 bg-slate-100"></span>
        <span class="text-sm tracking-[0.3em]">•••</span>
        <span class="h-px flex-1 bg-slate-100"></span>
      </div>
      <div class="markdown-body -mx-1 px-1 py-1 text-sm leading-6" v-html="block.html"></div>
    </template>
    <div
      v-for="(anchor, index) in absenceAnchors"
      :key="`absence-${index}`"
      class="rounded-lg border border-dashed border-[#d8b77f] bg-[#f8efe1] px-3 py-3 text-xs leading-relaxed text-[#5e421b]"
      data-testid="global-absence-evidence"
    >
      <span class="font-semibold">{{ $t('Expected content is absent') }}</span>
      <span class="mt-1 block">{{ $t('Missing') }}: {{ anchor.missing_item }}</span>
      <span class="mt-1 block text-[#79531d]"
        >{{ $t('Expected at:') }} {{ anchor.expected_location }}</span
      >
    </div>
  </div>
  <div
    v-else
    class="rounded-lg border border-dashed border-amber-200 bg-amber-50/70 px-3 py-3 text-xs leading-relaxed text-amber-800"
    data-testid="global-evidence-missing"
  >
    <span class="font-semibold">{{
      $t('The grading evidence could not be located in the report.')
    }}</span>
    <span v-if="point.anchor_text" class="mt-1 block break-words text-amber-700"
      >{{ $t('Recorded evidence:') }} {{ point.anchor_text }}</span
    >
    <span v-else class="mt-1 block text-amber-700">{{
      $t('This valid grade has no saved evidence text.')
    }}</span>
  </div>
</template>

<style scoped>
.global-evidence-blocks :deep(.markdown-body > :first-child) {
  margin-top: 0;
}

.global-evidence-blocks :deep(.markdown-body > :last-child) {
  margin-bottom: 0;
}

.global-evidence-blocks :deep(.markdown-body) {
  overflow-x: auto;
}

.global-evidence-blocks :deep(table) {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.75rem;
}

.global-evidence-blocks :deep(th),
.global-evidence-blocks :deep(td) {
  border: 1px solid rgb(226 232 240);
  padding: 0.4rem 0.55rem;
  text-align: left;
}

.global-evidence-blocks :deep(th) {
  background: rgb(248 250 252);
  color: rgb(71 85 105);
  font-weight: 600;
}

.global-evidence-blocks :deep(img) {
  display: block;
  max-width: 100%;
  height: auto;
  border-radius: 0.5rem;
}

.global-evidence-blocks :deep(pre) {
  overflow-x: auto;
  border-radius: 0.5rem;
  background: rgb(248 250 252);
  padding: 0.75rem;
}

.global-evidence-blocks :deep(blockquote) {
  border-left: 3px solid rgb(203 213 225);
  padding-left: 0.75rem;
  color: rgb(71 85 105);
}
</style>
