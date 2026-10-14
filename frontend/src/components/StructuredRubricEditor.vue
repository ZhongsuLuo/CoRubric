<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { ScoringPoint } from '../types/schema'
import {
  cloneScoringPoints,
  newUniqueScoringPoint,
  scoringPointDeductionCapacity,
  structuredDeductionCapacity,
  updateScoringPointContent,
  updateScoringPointScore,
} from '../utils/scoringPoints'
import { translate as t } from '../i18n'
const $t = t

const props = withDefaults(
  defineProps<{
    modelValue?: ScoringPoint[] | null
    criterionId: string
    maxScore?: number | null
  }>(),
  { modelValue: () => [] },
)

const emit = defineEmits<{
  (event: 'update:modelValue', value: ScoringPoint[]): void
}>()
const draft = ref<ScoringPoint[]>([])
const totalScore = computed(() => structuredDeductionCapacity(draft.value))
const hasBudgetMismatch = computed(
  () =>
    props.maxScore !== null &&
    props.maxScore !== undefined &&
    Math.abs(totalScore.value - props.maxScore) > 1e-6,
)
const fingerprint = (value?: ScoringPoint[] | null) => JSON.stringify(value || [])

watch(
  () => props.modelValue,
  (value) => {
    if (fingerprint(value) !== fingerprint(draft.value)) draft.value = cloneScoringPoints(value)
  },
  { immediate: true, deep: true },
)

const commit = () => emit('update:modelValue', cloneScoringPoints(draft.value))
const addPoint = () => {
  draft.value.push(newUniqueScoringPoint(draft.value, props.criterionId))
  commit()
}
const deletePoint = (pointIndex: number) => {
  draft.value.splice(pointIndex, 1)
  commit()
}
const updateContent = (point: ScoringPoint, summary: string) => {
  updateScoringPointContent(point, summary)
  commit()
}
const updateScore = (point: ScoringPoint, score: string) => {
  updateScoringPointScore(point, score)
  commit()
}
</script>

<template>
  <section class="border-t border-slate-200 pt-5">
    <div class="mb-3 flex items-center justify-between gap-3">
      <div>
        <h4 class="text-sm font-bold text-slate-700">{{ $t('Scoring points') }}</h4>
        <p class="mt-1 text-[11px] text-slate-500">
          {{ $t('Enter the content and score for each scoring point.') }}
        </p>
        <p
          v-if="maxScore !== null && maxScore !== undefined"
          class="mt-1 text-[11px]"
          :class="hasBudgetMismatch ? 'text-rose-600' : 'text-emerald-600'"
        >
          {{
            $t('Scoring point total: {total} / Criterion maximum: {maxScore}', {
              total: totalScore,
              maxScore,
            })
          }}
          <span v-if="hasBudgetMismatch">{{ $t('(These totals must match.)') }}</span>
        </p>
      </div>
      <button
        type="button"
        class="rounded-lg border border-[#b7d2ce] bg-[var(--interaction-active-soft)] px-2.5 py-1.5 text-xs font-semibold text-[var(--interaction-active)] hover:bg-[#cfe4e1]"
        @click="addPoint"
      >
        + {{ $t('Add scoring point') }}
      </button>
    </div>
    <div
      class="grid grid-cols-[minmax(0,1fr)_104px_30px] gap-2 border-b border-slate-200 px-1 pb-2 text-[10px] font-semibold uppercase tracking-wide text-slate-500"
    >
      <span>{{ $t('Scoring point content') }}</span>
      <span>{{ $t('Score') }}</span>
      <span></span>
    </div>
    <div v-if="!draft.length" class="py-4 text-xs text-slate-500">
      {{ $t('No scoring points yet.') }}
    </div>
    <div
      v-for="(point, pointIndex) in draft"
      :key="point.point_id || pointIndex"
      class="grid grid-cols-[minmax(0,1fr)_104px_30px] items-center gap-2 border-b border-slate-100 py-2"
    >
      <textarea
        :value="point.summary"
        rows="2"
        class="line-input"
        :placeholder="$t('Describe what this scoring point evaluates')"
        @input="updateContent(point, ($event.target as HTMLTextAreaElement).value)"
      />
      <input
        type="number"
        min="0"
        class="line-input"
        :aria-label="$t('Scoring point score')"
        :value="scoringPointDeductionCapacity(point)"
        @input="updateScore(point, ($event.target as HTMLInputElement).value)"
      />
      <button
        type="button"
        class="text-center text-slate-400 hover:text-rose-600"
        :title="$t('Remove scoring point')"
        @click="deletePoint(pointIndex)"
      >
        ×
      </button>
    </div>
  </section>
</template>

<style scoped>
.line-input {
  width: 100%;
  border-radius: 0.5rem;
  border: 1px solid rgb(226 232 240);
  background: white;
  padding: 0.5rem 0.625rem;
  font-size: 0.75rem;
  color: rgb(51 65 85);
}
.line-input:focus {
  border-color: rgb(99 102 241);
  outline: none;
  box-shadow: 0 0 0 1px rgb(99 102 241);
}
</style>
