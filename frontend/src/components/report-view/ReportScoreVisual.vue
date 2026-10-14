<script setup lang="ts">
import { computed } from 'vue'
import { translate as t } from '../../i18n'
import { formatAssessmentScore, scorePercent } from '../../utils/reportAssessmentPresentation'

const props = withDefaults(
  defineProps<{
    score?: number | null
    maximum: number
    compact?: boolean
    label?: string
  }>(),
  { score: null, compact: false, label: '' },
)

const percent = computed(() => scorePercent(props.score, props.maximum))
const scoreLabel = computed(() =>
  props.score === null || props.score === undefined
    ? `—/${formatAssessmentScore(props.maximum)}`
    : `${formatAssessmentScore(props.score)}/${formatAssessmentScore(props.maximum)}`,
)
</script>

<template>
  <div :class="props.compact ? 'min-w-24' : 'min-w-32'">
    <div class="mb-1 flex items-center justify-between gap-2 text-[10px] font-semibold">
      <span v-if="props.label" class="truncate text-slate-500">{{ props.label }}</span>
      <span class="ml-auto tabular-nums text-slate-700">{{ scoreLabel }}</span>
    </div>
    <div
      class="relative h-1.5 overflow-hidden rounded-full bg-slate-200"
      role="meter"
      :aria-label="props.label || t('Criterion score')"
      aria-valuemin="0"
      :aria-valuemax="props.maximum"
      :aria-valuenow="props.score ?? 0"
    >
      <div
        class="h-full rounded-full bg-[var(--interaction-active)] transition-[width] duration-200 motion-reduce:transition-none"
        :style="{ width: `${percent}%` }"
      />
      <span class="absolute inset-y-0 left-4/5 w-px bg-slate-500/30" aria-hidden="true" />
    </div>
  </div>
</template>
