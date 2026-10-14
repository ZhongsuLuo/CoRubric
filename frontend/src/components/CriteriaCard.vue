<script setup lang="ts">
import { computed } from 'vue'
import type {
  AlignmentConflict,
  AlignmentReadiness,
  AutoGradeAbstention,
  AutoGradeJobStatusItem,
  CriteriaItem,
  CriteriaMode,
  CriterionAuthorization,
  CriterionGlobalSummary,
  CriterionConsistencyState,
  ReviewAssignment,
  CriterionProposalItem,
  GradingItem,
  GlobalScorePoint,
  HighlightItem,
} from '../types/schema'
import { translate as t } from '../i18n'
const $t = t
import DistributionChart from './DistributionChart.vue'
import CriteriaCardSummary from './CriteriaCardSummary.vue'
import { gradingVisualConfig } from '../config/gradingVisualConfig'
import {
  authorizationState as resolveAuthorizationState,
  criteriaCardStatusTags,
  isAutoGradeMiss,
} from './criteriaCardPresentation'

const props = defineProps<{
  item: CriteriaItem
  mode: CriteriaMode
  grading?: GradingItem
  globalSummary?: CriterionGlobalSummary | null
  active: boolean
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
  evidenceSegments?: HighlightItem[]
  activeEvidenceSegmentId?: string | null
}>()

const emit = defineEmits<{
  (event: 'delete', criteriaId: string): void
  (event: 'authorize', criteriaId: string): void
  (event: 'confirm-repair', criteriaId: string): void
  (event: 'point-select', point: GlobalScorePoint): void
  (event: 'edit', criteriaId: string): void
  (event: 'accept-model', criteriaId: string): void
  (event: 'review-proposal', proposal: CriterionProposalItem): void
  (event: 'evidence-select', payload: { criteriaId: string; highlightId: string }): void
}>()

const authorizationState = () => resolveAuthorizationState(props.authorization)
const averageLabel = () => {
  const value = props.globalSummary?.average_score
  if (value === null || value === undefined) return '—'
  return Number.isInteger(value) ? String(value) : value.toFixed(1)
}

const consistencyReliable = computed(() => props.consistency?.state === 'reliable')

const autoGradeMissed = computed(() => {
  if (props.mode !== 'single') return false
  return isAutoGradeMiss({
    grading: props.grading,
    abstention: props.abstention,
    autoGradeJob: props.autoGradeJob,
  })
})

const headerTags = computed(() =>
  criteriaCardStatusTags({
    criterionId: String(props.item.criteria_id),
    mode: props.mode,
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

const showCardActions = computed(
  () =>
    Boolean(props.pendingProposals?.length) ||
    (props.mode === 'single' && props.canAcceptModelGrade) ||
    (authorizationState() === 'teacher_validation' &&
      props.readiness?.lifecycle_state === 'active_selective') ||
    authorizationState() === 'repair_required',
)

const reviewPendingProposal = () => {
  const proposal = props.pendingProposals?.[0]
  if (proposal) emit('review-proposal', proposal)
}
</script>

<template>
  <div
    class="relative overflow-hidden rounded-xl border p-4 text-sm transition-all duration-200 ease-in-out group"
    :class="
      active
        ? 'border-[var(--interaction-active)] bg-[var(--interaction-active-soft)] shadow-md ring-1 ring-[var(--interaction-active)]/20'
        : autoGradeMissed
          ? 'border-amber-300 bg-amber-50/40 hover:border-amber-400 hover:shadow-md'
          : 'border-slate-200 bg-white hover:border-[#86b5b0] hover:shadow-md'
    "
  >
    <div class="flex items-start justify-between gap-3">
      <div class="min-w-0 flex-1">
        <div class="font-bold text-slate-800 leading-snug">
          {{ item.criteria_name }}
        </div>
      </div>
      <div
        :data-testid="`criteria-card-header-meta-${item.criteria_id}`"
        class="flex max-w-[72%] flex-shrink-0 flex-wrap items-center justify-end gap-1.5"
      >
        <span
          v-for="tag in headerTags"
          :key="tag.key"
          :data-testid="tag.testId || `criteria-status-${item.criteria_id}-${tag.key}`"
          :title="tag.title"
          class="inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded-full px-2 py-0.5 text-[10px] font-semibold tracking-wide ring-1 ring-inset"
          :class="tag.className"
        >
          <svg
            v-if="tag.spinner"
            class="h-3 w-3 animate-spin"
            viewBox="0 0 24 24"
            fill="none"
            aria-hidden="true"
          >
            <circle
              class="opacity-25"
              cx="12"
              cy="12"
              r="9"
              stroke="currentColor"
              stroke-width="3"
            />
            <path
              class="opacity-90"
              fill="currentColor"
              d="M21 12a9 9 0 0 0-9-9v3a6 6 0 0 1 6 6h3Z"
            />
          </svg>
          {{ tag.label }}
        </span>
        <button
          v-if="mode === 'single'"
          type="button"
          :data-testid="`edit-criterion-${item.criteria_id}`"
          class="inline-flex h-7 w-7 items-center justify-center rounded-lg border border-transparent text-slate-400 transition-colors hover:border-[#b7d2ce] hover:bg-[var(--interaction-active-soft)] hover:text-[var(--interaction-active)] focus:outline-none focus:ring-2 focus:ring-[#b7d2ce]"
          :title="$t('Edit assessment and criterion')"
          :aria-label="$t('Edit assessment and criterion')"
          @click.stop="emit('edit', item.criteria_id)"
        >
          <svg
            class="h-4 w-4"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="2"
          >
            <path
              stroke-linecap="round"
              stroke-linejoin="round"
              d="M16.862 4.487l2.651 2.651M18.375 3a1.875 1.875 0 012.652 2.652L8.25 18.428 3 21l2.572-5.25L18.375 3z"
            />
          </svg>
        </button>
        <button
          type="button"
          class="inline-flex h-7 w-7 items-center justify-center rounded-lg border border-transparent text-slate-400 transition-colors hover:border-rose-200 hover:bg-rose-50 hover:text-rose-600 focus:outline-none focus:ring-2 focus:ring-rose-200"
          :title="$t('Delete criterion')"
          :aria-label="$t('Delete criterion')"
          @click.stop="emit('delete', item.criteria_id)"
        >
          <svg
            class="h-4 w-4"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="2"
          >
            <path stroke-linecap="round" stroke-linejoin="round" d="M3 6h18" />
            <path stroke-linecap="round" stroke-linejoin="round" d="M8 6V4h8v2" />
            <path stroke-linecap="round" stroke-linejoin="round" d="M19 6l-1 14H6L5 6" />
            <path stroke-linecap="round" stroke-linejoin="round" d="M10 11v5M14 11v5" />
          </svg>
        </button>
        <div
          class="min-w-[3.25rem] text-right text-sm font-bold tabular-nums transition-colors"
          :class="
            mode === 'global'
              ? active
                ? 'rounded-full bg-[var(--interaction-active)] px-2.5 py-1 text-white'
                : 'rounded-full bg-slate-100 px-2.5 py-1 text-slate-600 group-hover:bg-slate-200'
              : active
                ? 'text-[var(--interaction-active)]'
                : 'text-slate-700 group-hover:text-[var(--interaction-active)]'
          "
        >
          <template v-if="mode === 'single' && props.abstention && !grading">
            {{ $t('Abstained') }}
          </template>
          <template v-else-if="mode === 'single' && !grading">
            —<span class="opacity-60 text-[0.9em] font-normal mx-0.5">/</span>{{ item.max_score }}
          </template>
          <template v-else-if="mode === 'single'">
            {{ grading?.criteria_score
            }}<span class="opacity-60 text-[0.9em] font-normal mx-0.5">/</span>{{ item.max_score }}
          </template>
          <template v-else>
            {{ averageLabel()
            }}<span class="opacity-60 text-[0.9em] font-normal mx-0.5">{{ $t('Average') }} /</span
            >{{ item.max_score }}
          </template>
        </div>
      </div>
    </div>

    <CriteriaCardSummary
      v-if="mode === 'single'"
      :item="item"
      :grading="grading"
      :consistency="props.consistency"
    />
    <div v-else class="mt-3 space-y-2">
      <DistributionChart
        :points="props.globalSummary?.points || []"
        :max-score="item.max_score"
        @point-select="emit('point-select', $event)"
      />
      <div class="flex flex-wrap items-center justify-between gap-2 text-[10px] text-slate-500">
        <span
          >{{ props.globalSummary?.scored_count || 0 }}/{{
            props.globalSummary?.total_reports || 0
          }}
          graded</span
        >
        <span class="flex items-center gap-2">
          <span
            ><i
              class="mr-1 inline-block h-2 w-2 rounded-full"
              :style="{ backgroundColor: gradingVisualConfig.teacher.dotFill }"
            ></i
            >{{ props.globalSummary?.teacher_count || 0 }} teacher</span
          >
          <span
            ><i
              class="mr-1 inline-block h-2 w-2 rounded-full"
              :style="{ backgroundColor: gradingVisualConfig.model.dotFill }"
            ></i
            >{{ props.globalSummary?.model_count || 0 }} model</span
          >
          <span
            v-if="props.globalSummary?.conflict_count"
            :class="gradingVisualConfig.conflict.textClass"
            >{{ props.globalSummary.conflict_count }} conflict</span
          >
        </span>
      </div>
      <div
        v-if="authorizationState() === 'batch_authorized'"
        class="flex flex-wrap gap-x-2 text-[10px] text-slate-500"
      >
        <span v-if="props.globalSummary?.job_counts.running"
          >{{ props.globalSummary.job_counts.running }} {{ $t('running') }}</span
        >
        <span v-if="props.globalSummary?.job_counts.pending"
          >{{ props.globalSummary.job_counts.pending }} {{ $t('pending') }}</span
        >
        <span v-if="props.globalSummary?.job_counts.abstained"
          >{{ props.globalSummary.job_counts.abstained }} {{ $t('Abstained') }}</span
        >
        <span
          v-if="props.globalSummary?.job_counts.failed"
          :class="gradingVisualConfig.conflict.textClass"
          >{{ props.globalSummary.job_counts.failed }} {{ $t('failed') }}</span
        >
      </div>
    </div>

    <div
      v-if="mode === 'single' && (props.evidenceSegments?.length || 0) > 1"
      class="mt-2.5 flex flex-wrap items-center gap-1.5"
      :aria-label="$t('Evidence segments')"
      data-testid="evidence-segment-selector"
    >
      <button
        v-for="(segment, index) in props.evidenceSegments"
        :key="segment.id"
        type="button"
        class="rounded-md border px-2 py-1 text-[10px] font-semibold transition-colors focus:outline-none focus:ring-2 focus:ring-[#b7d2ce]"
        :class="
          props.activeEvidenceSegmentId === segment.id
            ? 'border-[#86b5b0] bg-[var(--interaction-active-soft)] text-[var(--interaction-active-strong)]'
            : 'border-slate-200 bg-white text-slate-600 hover:border-[#b7d2ce] hover:bg-[var(--interaction-active-soft)] hover:text-[var(--interaction-active)]'
        "
        :data-testid="`evidence-segment-${item.criteria_id}-${index + 1}`"
        @click.stop="
          emit('evidence-select', {
            criteriaId: String(item.criteria_id),
            highlightId: segment.id,
          })
        "
      >
        {{ $t('Evidence segment {index}', { index: index + 1 }) }}
      </button>
    </div>

    <div v-if="showCardActions" class="mt-2.5 flex items-center gap-2">
      <button
        v-if="props.pendingProposals?.length"
        type="button"
        class="whitespace-nowrap rounded-lg border border-[#d8b77f] bg-[#f8efe1] px-3 py-1.5 text-xs font-semibold text-[#79531d] hover:bg-[#f2dfbf]"
        :data-testid="`review-criterion-proposal-${item.criteria_id}`"
        @click.stop="reviewPendingProposal"
      >
        {{ $t('Review rubric update') }}
      </button>
      <button
        v-if="mode === 'single' && props.canAcceptModelGrade"
        type="button"
        class="whitespace-nowrap rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-1.5 text-xs font-semibold text-emerald-800 hover:bg-emerald-100 disabled:cursor-not-allowed disabled:opacity-50"
        :disabled="props.modelAcceptanceBusy"
        :data-testid="`accept-model-pregrade-${item.criteria_id}`"
        @click.stop="emit('accept-model', item.criteria_id)"
      >
        {{ props.modelAcceptanceBusy ? $t('Accepting…') : $t('Accept model pre-grade') }}
      </button>
      <button
        v-if="
          authorizationState() === 'teacher_validation' &&
          props.readiness?.lifecycle_state === 'active_selective'
        "
        type="button"
        class="inline-flex items-center gap-1 whitespace-nowrap rounded-lg border px-3 py-1.5 text-xs font-semibold disabled:cursor-not-allowed disabled:opacity-50"
        :class="
          consistencyReliable
            ? 'border-emerald-200 bg-emerald-50 text-emerald-800 hover:bg-emerald-100'
            : 'border-amber-300 bg-amber-50 text-amber-900 hover:bg-amber-100'
        "
        :disabled="props.authorizationBusy || Boolean(props.conflicts?.length)"
        @click.stop="emit('authorize', item.criteria_id)"
        :data-testid="`grade-remaining-${item.criteria_id}`"
        :title="
          consistencyReliable
            ? $t('Consistency has reached the reliability threshold')
            : $t('Consistency has not reached the reliability threshold')
        "
      >
        {{
          props.authorizationBusy
            ? $t('Authorizing…')
            : props.globalSummary?.eligible_count
              ? $t('Grade remaining {count} reports', {
                  count: props.globalSummary.eligible_count,
                })
              : $t('Authorize model grading')
        }}
        <span v-if="!props.authorizationBusy && !consistencyReliable">
          · {{ $t('Consistency not yet reliable') }}
        </span>
      </button>
      <button
        v-else-if="authorizationState() === 'repair_required'"
        type="button"
        class="rounded-lg border border-amber-300 bg-amber-50 px-3 py-1.5 text-xs font-semibold text-amber-900 hover:bg-amber-100 disabled:cursor-not-allowed disabled:opacity-50"
        :disabled="
          props.authorizationBusy ||
          Boolean(props.conflicts?.some((item) => item.conflict_type !== 'model_rubric_migration'))
        "
        @click.stop="emit('confirm-repair', item.criteria_id)"
      >
        {{
          props.authorizationBusy ? $t('Repairing…') : $t('Confirm review and regrade model scores')
        }}
      </button>
    </div>

    <div
      v-if="active"
      class="absolute bottom-0 left-0 top-0 w-1 bg-[var(--interaction-active)]"
    ></div>
  </div>
</template>
