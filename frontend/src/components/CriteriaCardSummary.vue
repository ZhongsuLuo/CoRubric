<script setup lang="ts">
import { computed } from 'vue'
import type { CriteriaItem, CriterionConsistencyState, GradingItem } from '../types/schema'
import { gradingVisualConfig, type GradingAuthor } from '../config/gradingVisualConfig'
import { consistencyLabel, consistencyPercent } from './criteriaCardPresentation'
import { translate as t } from '../i18n'
const $t = t

const props = defineProps<{
  item: CriteriaItem
  grading?: GradingItem
  consistency?: CriterionConsistencyState | null
}>()

const formatValue = (value: number) =>
  Number.isInteger(value) ? String(value) : String(Number(value.toFixed(2)))
const scorePercent = computed(() => {
  const score = Number(props.grading?.criteria_score)
  const maximum = Number(props.item.max_score)
  if (!Number.isFinite(score) || !Number.isFinite(maximum) || maximum <= 0) return 0
  return Math.max(0, Math.min(100, (score / maximum) * 100))
})
const hasConsistencySamples = computed(
  () => (props.consistency?.agreement_count ?? 0) + (props.consistency?.correction_count ?? 0) > 0,
)
const agreementPercent = computed(() =>
  hasConsistencySamples.value
    ? Math.max(0, Math.min(100, (props.consistency?.smoothed_agreement ?? 0.5) * 100))
    : 0,
)
const graderType = computed(() => {
  const provenance = props.grading?.provenance
  const provenanceType =
    provenance && typeof provenance === 'object' ? String(provenance.grader_type || '') : ''
  return (
    provenanceType ||
    props.grading?.grader_type ||
    props.grading?.graded_by ||
    ''
  ).toLowerCase()
})
const gradingAuthor = computed<GradingAuthor>(() => {
  if (graderType.value === 'model' || graderType.value === 'llm') return 'model'
  if (graderType.value === 'teacher' || graderType.value === 'user') return 'teacher'
  return 'unknown'
})
const provenanceVisual = computed(() => gradingVisualConfig[gradingAuthor.value])
const provenanceLabel = computed(() => {
  if (graderType.value === 'model' || graderType.value === 'llm') {
    return props.grading?.acceptance_status === 'accepted_model'
      ? $t('Model grade · accepted')
      : $t('Model pre-grade')
  }
  if (graderType.value === 'teacher' || graderType.value === 'user') return $t('Teacher grade')
  return graderType.value || $t('No grade')
})
const provenanceMark = computed(() => {
  if (graderType.value === 'model' || graderType.value === 'llm') return 'M'
  if (graderType.value === 'teacher' || graderType.value === 'user') return 'T'
  return '—'
})
</script>

<template>
  <div class="mt-3 space-y-3">
    <p class="text-xs leading-relaxed text-slate-600">{{ item.description }}</p>
    <div
      class="grid grid-cols-2 gap-4 rounded-lg border border-slate-100 bg-slate-50/70 px-3 py-2.5"
    >
      <div :data-testid="`criterion-score-visual-${item.criteria_id}`">
        <div
          class="mb-1.5 flex items-center justify-between gap-2 text-[10px] font-semibold text-slate-500"
        >
          <span class="flex items-center gap-1.5">
            <span>{{ $t('Score') }}</span>
            <span
              :data-testid="`criterion-provenance-${item.criteria_id}`"
              class="inline-flex h-4 min-w-4 items-center justify-center rounded-full border border-current/20 px-1 text-[8px] font-bold"
              :class="provenanceVisual.badgeClass"
              :title="provenanceLabel"
              :aria-label="provenanceLabel"
              >{{ provenanceMark }}</span
            >
            <span
              v-if="consistency?.state_source === 'teacher_override'"
              class="font-normal text-slate-500"
              >{{ $t('Teacher override') }}</span
            >
          </span>
          <span class="tabular-nums text-slate-700">
            {{ grading?.criteria_score ?? '—' }}/{{ formatValue(Number(item.max_score)) }}
          </span>
        </div>
        <div
          class="h-1.5 overflow-hidden rounded-full bg-slate-200"
          role="meter"
          :aria-label="$t('Criterion score')"
          aria-valuemin="0"
          :aria-valuemax="item.max_score"
          :aria-valuenow="grading?.criteria_score ?? 0"
        >
          <div
            class="h-full rounded-full bg-[var(--interaction-active)] transition-[width] duration-300"
            :style="{ width: `${scorePercent}%` }"
          ></div>
        </div>
      </div>
      <div
        v-if="consistency"
        :data-testid="`consistency-badge-${item.criteria_id}`"
        :aria-label="`${consistencyLabel(consistency.state)} ${consistencyPercent(consistency)}`"
      >
        <div
          class="mb-1.5 flex items-center justify-between gap-2 text-[10px] font-semibold text-slate-500"
        >
          <span>{{ $t('Consistency') }}</span>
          <span
            class="tabular-nums"
            :class="
              !hasConsistencySamples
                ? 'text-[var(--status-neutral)]'
                : consistency.state === 'unreliable'
                  ? 'text-[var(--status-review)]'
                  : consistency.state === 'reliable'
                    ? 'text-[var(--status-verified)]'
                    : 'text-[var(--status-running)]'
            "
            >{{ consistencyPercent(consistency) }}</span
          >
        </div>
        <div
          class="flex h-1.5 overflow-hidden rounded-full bg-slate-200"
          role="meter"
          :aria-label="$t('Agreement rate')"
          aria-valuemin="0"
          aria-valuemax="100"
          :aria-valuenow="Math.round(agreementPercent)"
        >
          <div
            class="h-full transition-[width] duration-300"
            :class="
              !hasConsistencySamples
                ? 'status-fill-neutral'
                : consistency.state === 'unreliable'
                  ? 'status-fill-review'
                  : consistency.state === 'reliable'
                    ? 'status-fill-verified'
                    : 'status-fill-running'
            "
            :style="{ width: `${agreementPercent}%` }"
          ></div>
        </div>
      </div>
      <div v-else>
        <div class="mb-1.5 text-[10px] font-semibold text-slate-500">
          {{ $t('Consistency') }}
        </div>
        <div class="text-[10px] text-slate-400">{{ $t('Not calibrated') }}</div>
      </div>
    </div>
  </div>
</template>
