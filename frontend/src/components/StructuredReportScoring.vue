<script setup lang="ts">
import { computed } from 'vue'
import type { ScoringPoint, ScoringPointScore } from '../types/schema'
import {
  normalizeScoringPointScores,
  scoringPointDeductionCapacity,
  structuredPointScoreTotal,
} from '../utils/scoringPoints'
import { translate as t } from '../i18n'
const $t = t

const props = withDefaults(
  defineProps<{
    points?: ScoringPoint[] | null
    maxScore: number
    pointScores?: ScoringPointScore[]
  }>(),
  { points: () => [], pointScores: () => [] },
)

const emit = defineEmits<{
  (event: 'update:pointScores', value: ScoringPointScore[]): void
}>()
const normalizedScores = computed(() =>
  normalizeScoringPointScores(props.points, props.pointScores),
)
const totalScore = computed(() => structuredPointScoreTotal(normalizedScores.value))
const updateScore = (pointId: string, raw: number | string) => {
  const maxScore = scoringPointDeductionCapacity(
    (props.points || []).find((point) => point.point_id === pointId) || {
      point_id: pointId,
      summary: '',
      deduction_rules: [],
    },
  )
  const parsed = Number(raw)
  const score = Number.isFinite(parsed) ? Math.min(Math.max(parsed, 0), maxScore) : 0
  emit(
    'update:pointScores',
    normalizedScores.value.map((item) =>
      item.point_id === pointId ? { ...item, score, max_score: maxScore } : item,
    ),
  )
}
</script>

<template>
  <section class="border-t border-slate-200 pt-4">
    <div class="mb-3 flex items-end justify-between gap-3">
      <div>
        <h4 class="text-sm font-bold text-slate-700">
          {{ $t('Scoring point scores') }}
        </h4>
        <p class="mt-1 text-[11px] text-slate-500">
          {{ $t('Enter each scoring point score. The total is calculated automatically.') }}
        </p>
      </div>
      <div class="text-right">
        <div class="text-[11px] text-slate-500">
          {{ $t('Total scoring point score') }}
        </div>
        <div class="text-lg font-bold text-[var(--interaction-active)]">
          {{ totalScore }} / {{ maxScore }}
        </div>
      </div>
    </div>
    <div
      class="grid grid-cols-[minmax(0,1fr)_76px_96px] gap-2 border-b border-slate-200 px-1 pb-2 text-[10px] font-semibold uppercase tracking-wide text-slate-500"
    >
      <span>{{ $t('Scoring point') }}</span
      ><span>{{ $t('Maximum') }}</span
      ><span class="text-right">{{ $t('Report score') }}</span>
    </div>
    <template v-for="point in points" :key="point.point_id">
      <div
        class="grid grid-cols-[minmax(0,1fr)_76px_96px] items-center gap-2 border-b border-slate-100 py-2 text-xs"
      >
        <div class="min-w-0">
          <div class="font-medium text-slate-700">{{ point.summary }}</div>
        </div>
        <div class="font-semibold text-slate-600">
          {{ scoringPointDeductionCapacity(point) }}
        </div>
        <div class="flex items-center justify-end gap-1">
          <input
            class="w-16 rounded border border-slate-200 px-2 py-1 text-right font-semibold text-[var(--interaction-active)] focus:border-[var(--interaction-active)] focus:outline-none"
            type="number"
            :data-testid="`scoring-point-score-${point.point_id}`"
            min="0"
            :max="scoringPointDeductionCapacity(point)"
            :value="normalizedScores.find((item) => item.point_id === point.point_id)?.score"
            @input="updateScore(point.point_id, ($event.target as HTMLInputElement).value)"
          /><span class="text-slate-400">/ {{ scoringPointDeductionCapacity(point) }}</span>
        </div>
      </div>
    </template>
  </section>
</template>
