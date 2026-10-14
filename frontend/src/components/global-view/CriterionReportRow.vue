<script setup lang="ts">
import { computed } from 'vue'
import type { GlobalReportSummary, GlobalScorePoint } from '../../types/schema'
import CriterionEvidenceCell from './CriterionEvidenceCell.vue'
import { formatScore } from './presentation'
import { reportToneDotClass, type ReportTone } from './presentation'
import { translate as $t } from '../../i18n'

const props = defineProps<{
  report: GlobalReportSummary
  point: GlobalScorePoint | null
  status: string
  tone: ReportTone
  score: number | null | undefined
  maxScore: number | null | undefined
  provenance: string
  markdown: string
}>()

const emit = defineEmits<{ inspect: [] }>()
const statusDotClass = computed(() => reportToneDotClass(props.tone))
</script>

<template>
  <div
    class="grid grid-cols-[minmax(150px,0.7fr)_82px_minmax(210px,1.25fr)_minmax(230px,1.45fr)_112px] items-start gap-4"
  >
    <div class="min-w-0 pt-1">
      <p class="truncate text-[13px] font-semibold text-slate-900">
        {{ report.report_name }}
      </p>
      <div class="mt-1 flex min-w-0 items-center gap-1.5">
        <span class="h-1.5 w-1.5 shrink-0 rounded-full" :class="statusDotClass" />
        <p class="truncate text-[10px] text-slate-500">{{ status }}</p>
      </div>
    </div>

    <div class="pt-0.5 text-right">
      <p class="text-lg font-semibold tabular-nums tracking-tight text-slate-950">
        {{ formatScore(score)
        }}<span class="text-[10px] font-normal text-slate-400">/{{ formatScore(maxScore) }}</span>
      </p>
      <p v-if="provenance" class="mt-0.5 text-[9px] text-slate-400">
        {{ provenance }}
      </p>
    </div>

    <CriterionEvidenceCell :report-name="report.report_name" :point="point" :markdown="markdown" />

    <div class="min-w-0 border-l border-slate-200 pl-4">
      <p
        class="break-words text-[10px] leading-[1.55] text-slate-600"
        data-testid="global-score-rationale"
      >
        {{ point?.score_reason || $t('No scoring rationale recorded.') }}
      </p>
    </div>

    <div class="flex flex-col items-end justify-center gap-1.5 pt-1">
      <button
        type="button"
        class="text-[10px] text-slate-500 hover:text-slate-900"
        @click="emit('inspect')"
      >
        {{ $t('Open report →') }}
      </button>
    </div>
  </div>
</template>
