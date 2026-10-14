<script setup lang="ts">
import type { GlobalReportSummary, GlobalScorePoint } from '../../types/schema'
import CriterionReportRow from './CriterionReportRow.vue'
import OverallReportRow from './OverallReportRow.vue'
import type { ReportTone } from './presentation'

defineProps<{
  report: GlobalReportSummary
  point: GlobalScorePoint | null
  selectedCriterionId: string | null
  highlighted: boolean
  status: string
  tone: ReportTone
  score: number | null | undefined
  maxScore: number | null | undefined
  provenance: string
  focused: boolean
  density: number
  markdown: string
}>()

const emit = defineEmits<{
  inspect: []
}>()
</script>

<template>
  <article
    class="group min-w-0 px-5 outline-none transition focus:bg-[var(--interaction-active-soft)]"
    :class="[
      highlighted ? 'interaction-soft interaction-outline' : '',
      focused ? 'interaction-soft ring-1 ring-inset ring-[var(--interaction-active)]' : '',
      density === 1 ? 'py-2' : density === 2 ? 'py-3' : 'py-4',
    ]"
    :data-report-id="report.report_id"
    data-testid="global-report-tile"
    tabindex="0"
  >
    <CriterionReportRow
      v-if="selectedCriterionId"
      :report="report"
      :point="point"
      :status="status"
      :tone="tone"
      :score="score"
      :max-score="maxScore"
      :provenance="provenance"
      :markdown="markdown"
      @inspect="emit('inspect')"
    />
    <OverallReportRow
      v-else
      :report="report"
      :status="status"
      :tone="tone"
      :score="score"
      :max-score="maxScore"
      :provenance="provenance"
      @inspect="emit('inspect')"
    />
  </article>
</template>
