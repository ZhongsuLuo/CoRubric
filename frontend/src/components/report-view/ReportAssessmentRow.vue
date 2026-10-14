<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type {
  AlignmentConflict,
  AlignmentReadiness,
  AutoGradeAbstention,
  AutoGradeJobStatusItem,
  CriteriaItem,
  CriterionAuthorization,
  CriterionConsistencyState,
  CriterionProposalItem,
  GradingItem,
  HighlightItem,
  ReviewAssignment,
} from '../../types/schema'
import { translate as t } from '../../i18n'
import { criteriaCardStatusTags, isAutoGradeMiss } from '../criteriaCardPresentation'
import {
  evidenceAnchorLabel,
  evidenceColorClass,
  evidenceSegmentLabel,
} from '../../utils/reportAssessmentPresentation'
import ReportAssessmentDetail from './ReportAssessmentDetail.vue'
import ReportScoreVisual from './ReportScoreVisual.vue'
import type {
  ReportAssessmentMode,
  ReportGradeDraft,
  ReportRubricDraft,
} from './reportAssessmentTypes'

const props = defineProps<{
  item: CriteriaItem
  grading?: GradingItem
  active: boolean
  reportName?: string
  readiness?: AlignmentReadiness | null
  conflicts?: AlignmentConflict[]
  abstention?: AutoGradeAbstention | null
  autoGradeJob?: AutoGradeJobStatusItem | null
  auditJob?: AutoGradeJobStatusItem | null
  pendingProposals?: CriterionProposalItem[]
  authorization?: CriterionAuthorization | null
  consistency?: CriterionConsistencyState | null
  reviewAssignment?: ReviewAssignment | null
  authorizationBusy?: boolean
  canAcceptModelGrade?: boolean
  modelAcceptanceBusy?: boolean
  gradeSaving?: boolean
  rubricSaving?: boolean
  actionError?: string
  evidenceSegments?: HighlightItem[]
  activeEvidenceSegmentId?: string | null
}>()

const emit = defineEmits<{
  (event: 'delete', criteriaId: string): void
  (event: 'select', criteriaId: string): void
  (event: 'toggle', criteriaId: string): void
  (event: 'authorize', criteriaId: string): void
  (event: 'confirm-repair', criteriaId: string): void
  (event: 'edit', criteriaId: string): void
  (event: 'save-grade', draft: ReportGradeDraft): void
  (event: 'save-rubric', draft: ReportRubricDraft): void
  (event: 'accept-model', criteriaId: string): void
  (event: 'review-proposal', proposal: CriterionProposalItem): void
  (event: 'evidence-select', payload: { criteriaId: string; highlightId: string }): void
  (event: 'evidence-adjust', payload: { criteriaId: string; highlightId: string | null }): void
}>()

const autoGradeMissed = computed(() =>
  isAutoGradeMiss({
    grading: props.grading,
    abstention: props.abstention,
    autoGradeJob: props.autoGradeJob,
  }),
)

const statusTags = computed(() =>
  criteriaCardStatusTags({
    criterionId: String(props.item.criteria_id),
    mode: 'single',
    authorization: props.authorization,
    conflicts: props.conflicts,
    abstention: props.abstention,
    autoGradeJob: props.autoGradeJob,
    auditJob: props.auditJob,
    pendingProposals: props.pendingProposals,
    readiness: props.readiness,
    consistency: props.consistency,
    reviewAssignment: props.reviewAssignment,
    autoGradeMissed: autoGradeMissed.value,
  }),
)
const primaryStatusTag = computed(() => statusTags.value[0] || null)
const workbenchMode = ref<ReportAssessmentMode>('score')
const requestedMode = ref<ReportAssessmentMode | null>(null)

watch(
  () => props.active,
  (active) => {
    if (!active) return
    workbenchMode.value = requestedMode.value || (props.conflicts?.length ? 'conflict' : 'score')
    requestedMode.value = null
  },
  { immediate: true },
)

watch(
  () => props.conflicts?.length || 0,
  (count, previousCount) => {
    if (props.active && count > previousCount) workbenchMode.value = 'conflict'
  },
)

const openWorkbench = (mode: ReportAssessmentMode) => {
  workbenchMode.value = mode
  if (!props.active) requestedMode.value = mode
  emit('select', String(props.item.criteria_id))
}

const firstEvidence = computed(() => props.evidenceSegments?.[0] || null)
const summaryEvidence = computed(() => {
  if (firstEvidence.value) return evidenceSegmentLabel(firstEvidence.value)
  const anchor = props.grading?.evidence_anchors?.find((item) => item.anchor_type !== 'absence')
  return anchor ? evidenceAnchorLabel(anchor) : ''
})
const absenceCount = computed(
  () =>
    props.grading?.evidence_anchors?.filter((anchor) => anchor.anchor_type === 'absence').length ||
    0,
)
const absenceEvidenceSummary = computed(() => {
  const anchor = props.grading?.evidence_anchors?.find((item) => item.anchor_type === 'absence')
  return anchor ? evidenceAnchorLabel(anchor) : ''
})
const evidenceCount = computed(() => (props.evidenceSegments?.length || 0) + absenceCount.value)

const selectEvidence = (highlightId: string) => {
  emit('evidence-select', {
    criteriaId: String(props.item.criteria_id),
    highlightId,
  })
}

const reviewPendingProposal = () => {
  const proposal = props.pendingProposals?.[0]
  if (proposal) emit('review-proposal', proposal)
}
</script>

<template>
  <article
    class="border-b border-slate-200 bg-white transition-colors last:border-b-0"
    :class="[
      props.active ? 'bg-[var(--interaction-active-soft)]' : 'hover:bg-slate-50/70',
      autoGradeMissed ? 'bg-amber-50/30' : '',
    ]"
    :data-testid="`assessment-row-${props.item.criteria_id}`"
  >
    <div
      class="grid min-h-[64px] grid-cols-[minmax(10rem,1.05fr)_minmax(12rem,1.25fr)_minmax(10rem,.8fr)] items-center"
    >
      <div class="min-w-0 px-4 py-3">
        <div class="flex items-start gap-2">
          <span class="mt-1 flex min-h-4 w-2 shrink-0 flex-col justify-center gap-0.5">
            <span
              v-for="segment in props.evidenceSegments"
              :key="segment.id"
              class="h-1.5 w-1.5 rounded-full ring-1 ring-white"
              :class="evidenceColorClass(segment.color)"
              :data-criterion-id="props.item.criteria_id"
              :data-criterion-connection-highlight-id="segment.id"
              :data-connection-color="segment.color"
              aria-hidden="true"
            />
            <span
              v-if="!props.evidenceSegments?.length"
              class="h-1.5 w-1.5 rounded-full border border-slate-300 bg-white"
              aria-hidden="true"
            />
          </span>
          <div class="min-w-0">
            <div class="truncate text-xs font-bold text-slate-800">
              {{ props.item.criteria_name }}
            </div>
            <p class="mt-1 line-clamp-2 text-[10px] leading-4 text-slate-500">
              {{ props.item.description }}
            </p>
          </div>
        </div>
      </div>

      <div class="min-w-0 border-l border-slate-100 px-4 py-3">
        <div v-if="summaryEvidence" class="line-clamp-2 text-[10px] leading-4 text-slate-600">
          “{{ summaryEvidence }}”
        </div>
        <div v-else class="text-[10px] text-slate-400">
          {{
            absenceEvidenceSummary ||
            (props.abstention ? t('Abstained') : t('No grounded evidence'))
          }}
        </div>
        <div class="mt-1 flex items-center gap-2 text-[9px] font-semibold text-slate-400">
          <span>{{ t('{count} evidence locations', { count: evidenceCount }) }}</span>
          <span v-if="props.grading?.target_section" class="truncate">
            {{ props.grading.target_section }}
          </span>
        </div>
        <div
          v-if="(props.evidenceSegments?.length || 0) > 1"
          class="mt-1.5 flex flex-wrap gap-1"
          data-testid="evidence-segment-selector"
          :aria-label="t('Evidence segments')"
        >
          <button
            v-for="(segment, index) in props.evidenceSegments"
            :key="segment.id"
            type="button"
            class="rounded border px-1.5 py-0.5 text-[8px] font-semibold"
            :class="
              props.activeEvidenceSegmentId === segment.id
                ? 'border-[#86b5b0] bg-[var(--interaction-active-soft)] text-[var(--interaction-active-strong)]'
                : 'border-slate-200 bg-white text-slate-500 hover:border-[#b7d2ce] hover:text-[var(--interaction-active)]'
            "
            :data-testid="`evidence-segment-${props.item.criteria_id}-${index + 1}`"
            @click.stop="selectEvidence(segment.id)"
          >
            {{ index + 1 }}
          </button>
        </div>
      </div>

      <div
        :data-testid="`criterion-score-visual-${props.item.criteria_id}`"
        class="group/score border-l border-slate-100 px-4 py-3"
      >
        <div class="flex items-start gap-2">
          <div class="min-w-0 flex-1">
            <ReportScoreVisual
              :score="props.grading?.criteria_score ?? null"
              :maximum="props.item.max_score"
              compact
            />
            <button
              v-if="primaryStatusTag"
              type="button"
              :data-testid="
                primaryStatusTag.testId ||
                `criteria-status-${props.item.criteria_id}-${primaryStatusTag.key}`
              "
              class="mt-1 inline-flex items-center rounded-full px-1.5 py-0.5 text-[8px] font-semibold ring-1 ring-inset"
              :class="primaryStatusTag.className"
              :title="primaryStatusTag.title"
              @click.stop="openWorkbench(props.conflicts?.length ? 'conflict' : 'score')"
            >
              {{ primaryStatusTag.label }}
            </button>
          </div>
          <div class="flex shrink-0 items-center gap-1">
            <button
              v-if="props.pendingProposals?.length"
              type="button"
              :data-testid="`review-criterion-proposal-${props.item.criteria_id}`"
              class="rounded-md border border-[#d8b77f] bg-[#f8efe1] px-2 py-1 text-[8px] font-semibold text-[#79531d] hover:bg-[#f2dfbf]"
              @click.stop="reviewPendingProposal"
            >
              {{ t('Review') }}
            </button>
            <button
              type="button"
              :data-testid="`edit-criterion-${props.item.criteria_id}`"
              class="rounded-md p-1.5 text-slate-400 opacity-0 hover:bg-amber-50 hover:text-amber-700 group-hover/score:opacity-100 focus:opacity-100"
              :title="t('Edit scoring standard')"
              :aria-label="t('Edit scoring standard')"
              @click.stop="openWorkbench('rubric')"
            >
              ✎
            </button>
            <button
              type="button"
              class="rounded p-1 text-xs text-[var(--interaction-active)] hover:bg-[var(--interaction-active-soft)]"
              :aria-label="
                props.active ? t('Collapse criterion details') : t('Expand criterion details')
              "
              :aria-expanded="props.active"
              :data-testid="`toggle-criterion-${props.item.criteria_id}`"
              @click.stop="emit('toggle', String(props.item.criteria_id))"
            >
              {{ props.active ? '⌃' : '⌄' }}
            </button>
          </div>
        </div>
      </div>
    </div>

    <ReportAssessmentDetail
      v-if="props.active"
      :item="props.item"
      :grading="props.grading"
      :mode="workbenchMode"
      :report-name="props.reportName"
      :readiness="props.readiness"
      :conflicts="props.conflicts"
      :authorization="props.authorization"
      :consistency="props.consistency"
      :authorization-busy="props.authorizationBusy"
      :can-accept-model-grade="props.canAcceptModelGrade"
      :model-acceptance-busy="props.modelAcceptanceBusy"
      :grade-saving="props.gradeSaving"
      :rubric-saving="props.rubricSaving"
      :action-error="props.actionError"
      :evidence-segments="props.evidenceSegments"
      :active-evidence-segment-id="props.activeEvidenceSegmentId"
      @update:mode="workbenchMode = $event"
      @delete="emit('delete', $event)"
      @authorize="emit('authorize', $event)"
      @confirm-repair="emit('confirm-repair', $event)"
      @accept-model="emit('accept-model', $event)"
      @advanced-edit="emit('edit', $event)"
      @save-grade="emit('save-grade', $event)"
      @save-rubric="emit('save-rubric', $event)"
      @evidence-select="emit('evidence-select', $event)"
      @evidence-adjust="emit('evidence-adjust', $event)"
    />
  </article>
</template>
