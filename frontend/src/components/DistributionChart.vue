<script setup lang="ts">
import { computed, ref } from 'vue'
import {
  GLOBAL_DISTRIBUTION_REPORT_LIMIT,
  gradingVisualConfig,
} from '../config/gradingVisualConfig'
import type { GlobalScorePoint } from '../types/schema'
import { layoutGlobalScorePoints } from '../utils/globalDistribution'
import { translate as t } from '../i18n'

const props = defineProps<{
  points: GlobalScorePoint[]
  maxScore: number
}>()
const emit = defineEmits<{
  (event: 'point-select', point: GlobalScorePoint): void
}>()

const width = 240
const height = 88
const activePoint = ref<GlobalScorePoint | null>(null)
const positionedPoints = computed(() =>
  layoutGlobalScorePoints(props.points, props.maxScore, width, height),
)
const exceedsRecommendedLimit = computed(
  () => props.points.length > GLOBAL_DISTRIBUTION_REPORT_LIMIT,
)
const tickValues = computed(() => [0, props.maxScore / 2, props.maxScore])
const tickX = (value: number) => 10 + (props.maxScore > 0 ? value / props.maxScore : 0) * 220
const fillFor = (point: GlobalScorePoint) =>
  gradingVisualConfig[point.author]?.dotFill || gradingVisualConfig.unknown.dotFill
const pointLabel = (point: GlobalScorePoint) =>
  `${point.report_name}: ${point.score}/${point.max_score}, ${t(gradingVisualConfig[point.author]?.label || 'Unknown')}${point.conflict ? `, ${t('conflict')}` : ''}`
const formatScore = (value: number) =>
  Number.isInteger(value) ? String(value) : value.toFixed(1).replace(/\.0$/, '')
const selectPoint = (positionedPoint: GlobalScorePoint & { x?: number; y?: number }) => {
  const point = { ...positionedPoint }
  delete point.x
  delete point.y
  activePoint.value = point
  emit('point-select', point)
}
</script>

<template>
  <div class="relative">
    <div
      v-if="activePoint"
      class="pointer-events-none absolute right-1 top-0 z-10 max-w-[75%] rounded-md border border-slate-200 bg-white/95 px-2 py-1 text-[10px] leading-tight text-slate-700 shadow-sm"
    >
      <div class="truncate font-semibold">{{ activePoint.report_name }}</div>
      <div>
        {{ formatScore(activePoint.score) }}/{{ formatScore(activePoint.max_score) }} ·
        {{ t(gradingVisualConfig[activePoint.author]?.label || 'Unknown')
        }}<span v-if="activePoint.conflict" :class="gradingVisualConfig.conflict.textClass">
          · {{ t('Conflict') }}</span
        >
      </div>
    </div>
    <svg
      class="block w-full"
      :viewBox="`0 0 ${width} ${height}`"
      role="img"
      :aria-label="t('Score distribution for {count} graded reports', { count: props.points.length })"
    >
      <line
        x1="10"
        :y1="height - 16"
        x2="230"
        :y2="height - 16"
        stroke="#cbd5e1"
        stroke-width="1"
      />
      <g v-for="tick in tickValues" :key="tick">
        <line
          :x1="tickX(tick)"
          :y1="height - 19"
          :x2="tickX(tick)"
          :y2="height - 13"
          stroke="#94a3b8"
          stroke-width="1"
        />
        <text
          :x="tickX(tick)"
          :y="height - 3"
          text-anchor="middle"
          class="fill-slate-400 text-[8px]"
        >
          {{ formatScore(tick) }}
        </text>
      </g>
      <circle
        v-for="point in positionedPoints"
        :key="`${point.report_name}-${point.event_id || point.score}`"
        :cx="point.x"
        :cy="point.y"
        r="3.5"
        :fill="fillFor(point)"
        :stroke="point.conflict ? gradingVisualConfig.conflict.ringStroke : '#ffffff'"
        :stroke-width="point.conflict ? 2 : 1"
        tabindex="0"
        role="button"
        :aria-label="pointLabel(point)"
        :data-report-id="point.report_id"
        :data-criterion-id="point.criterion_id"
        data-testid="global-score-point"
        class="cursor-pointer outline-none transition-[r] focus:[r:5px]"
        @mouseenter="activePoint = point"
        @mouseleave="activePoint = null"
        @focus="activePoint = point"
        @blur="activePoint = null"
        @click.stop="selectPoint(point)"
        @keydown.enter.prevent.stop="selectPoint(point)"
        @keydown.space.prevent.stop="selectPoint(point)"
      >
        <title>{{ pointLabel(point) }}</title>
      </circle>
    </svg>
    <div v-if="exceedsRecommendedLimit" class="mt-1 text-[10px] leading-tight text-amber-700">
      {{ t('Score distribution: {count} reports shown; distributions above {limit} may be dense.', { count: props.points.length, limit: GLOBAL_DISTRIBUTION_REPORT_LIMIT }) }}
    </div>
  </div>
</template>
