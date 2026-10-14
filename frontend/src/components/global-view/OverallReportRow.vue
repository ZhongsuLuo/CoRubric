<script setup lang="ts">
import { computed } from 'vue'
import type { GlobalReportSummary } from '../../types/schema'
import { formatScore, reportToneDotClass } from './presentation'
import { translate as $t } from '../../i18n'
import type { ReportTone } from './presentation'

const props = defineProps<{
  report: GlobalReportSummary
  status: string
  tone: ReportTone
  score: number | null | undefined
  maxScore: number | null | undefined
  provenance: string
}>()

const emit = defineEmits<{ inspect: [] }>()
const analysisSummary = computed(
  () => props.report.analysis_summary || $t('No report analysis available'),
)
const progressToneClass = computed(() => {
  if (props.tone === 'verified') return 'bg-[var(--status-verified)]'
  if (props.tone === 'running') return 'bg-[var(--status-running)]'
  return 'bg-[#82918b]'
})
const statusDotClass = computed(() => reportToneDotClass(props.tone))
</script>

<template>
  <div
    class="grid grid-cols-[minmax(150px,220px)_76px_minmax(260px,1fr)_minmax(150px,0.65fr)_90px] items-center gap-4"
    data-testid="global-overall-report-row"
  >
    <div class="min-w-0">
      <p class="truncate text-[13px] font-semibold text-slate-900">
        {{ report.report_name }}
      </p>
    </div>
    <p class="text-right text-lg font-semibold tabular-nums text-slate-950">
      {{ formatScore(score)
      }}<span class="text-[10px] font-normal text-slate-400">/{{ formatScore(maxScore) }}</span>
    </p>
    <p
      class="line-clamp-2 min-w-0 text-[11px] leading-4 text-[var(--text-secondary)]"
      :title="analysisSummary"
      data-testid="global-report-analysis-summary"
    >
      {{ analysisSummary }}
    </p>
    <div class="min-w-0">
      <div class="mb-1 flex min-w-0 items-center gap-1.5 text-[10px] text-[var(--text-secondary)]">
        <span class="h-1.5 w-1.5 shrink-0 rounded-full" :class="statusDotClass" />
        <span class="truncate">{{ status }}</span>
      </div>
      <div class="mb-1 flex items-center justify-between text-[9px] text-slate-500">
        <span
          >{{ report.graded_criteria_count }}/{{ report.total_criteria_count }} {{ $t('criteria') }}
          {{ $t('graded') }}</span
        >
        <span v-if="provenance">{{ provenance }}</span>
      </div>
      <div class="h-1.5 overflow-hidden rounded-full bg-slate-100">
        <span
          class="block h-full rounded-full"
          :class="progressToneClass"
          data-testid="global-report-progress"
          :style="{
            width: `${report.total_criteria_count ? (report.graded_criteria_count / report.total_criteria_count) * 100 : 0}%`,
          }"
        />
      </div>
    </div>
    <button
      type="button"
      class="justify-self-end text-[10px] text-slate-500 hover:text-slate-900"
      @click="emit('inspect')"
    >
      {{ $t('Open report →') }}
    </button>
  </div>
</template>
