<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { gradingVisualConfig } from '../../config/gradingVisualConfig'
import { translate as t } from '../../i18n'
import type {
  CriteriaItem,
  CriterionProposalItem,
  GradingItem,
  HighlightItem,
  ImplicitGradeItem,
} from '../../types/schema'
import {
  assessmentPoints,
  evidenceColorClass,
  formatAssessmentScore,
  graderType,
  scorePercent,
} from '../../utils/reportAssessmentPresentation'

type ProfileNode =
  | {
      kind: 'criterion'
      key: string
      criterion: CriteriaItem
      proposals: CriterionProposalItem[]
    }
  | { kind: 'create'; key: string; proposal: CriterionProposalItem }

type CriterionProfileNode = Extract<ProfileNode, { kind: 'criterion' }>

const props = withDefaults(
  defineProps<{
    criteria: CriteriaItem[]
    gradings: GradingItem[]
    proposals: CriterionProposalItem[]
    pendingItems?: ImplicitGradeItem[]
    activePendingId?: string | null
    highlights: HighlightItem[]
    reportScore: number
    baseScore: number
    activeCriteriaId?: string | null
    conflictCriteriaIds?: string[]
    reviewCriteriaIds?: string[]
    proposalSavingId?: string | null
    pendingRetryingId?: string | null
    proposalError?: string
  }>(),
  {
    activeCriteriaId: null,
    pendingItems: () => [],
    activePendingId: null,
    conflictCriteriaIds: () => [],
    reviewCriteriaIds: () => [],
    proposalSavingId: null,
    pendingRetryingId: null,
    proposalError: '',
  },
)

const emit = defineEmits<{
  selectCriterion: [criteriaId: string]
  reviewProposal: [proposal: CriterionProposalItem]
  confirmProposal: [proposal: CriterionProposalItem]
  selectPending: [item: ImplicitGradeItem]
  editPending: [item: ImplicitGradeItem]
  retryPending: [item: ImplicitGradeItem]
}>()

const selectedProposalId = ref<string | null>(null)
const pendingExpanded = ref(true)
const gradingByCriteria = computed(
  () => new Map(props.gradings.map((grading) => [String(grading.criteria_id), grading])),
)
const criteriaById = computed(
  () => new Map(props.criteria.map((criterion) => [String(criterion.criteria_id), criterion])),
)
const updateProposalsByCriteria = computed(() => {
  const grouped = new Map<string, CriterionProposalItem[]>()
  props.proposals.forEach((proposal) => {
    if (proposal.proposal_type !== 'update_criterion' || !proposal.target_criterion_id) return
    const criteriaId = String(proposal.target_criterion_id)
    const existing = grouped.get(criteriaId) || []
    existing.push(proposal)
    grouped.set(criteriaId, existing)
  })
  return grouped
})
const createProposals = computed(() =>
  props.proposals.filter((proposal) => proposal.proposal_type === 'create_criterion'),
)
const proposalByImplicitId = computed(() => {
  const proposals = new Map<string, CriterionProposalItem>()
  props.proposals.forEach((proposal) => {
    proposal.source_implicit_grade_ids.forEach((implicitId) => proposals.set(implicitId, proposal))
  })
  return proposals
})
const selectedProposal = computed(
  () =>
    props.proposals.find((proposal) => proposal.proposal_id === selectedProposalId.value) || null,
)
const conflictIds = computed(() => new Set(props.conflictCriteriaIds.map(String)))
const reviewIds = computed(() => new Set(props.reviewCriteriaIds.map(String)))

const profileNodes = computed<ProfileNode[]>(() => {
  const remainingCreates = new Set(createProposals.value.map((proposal) => proposal.proposal_id))
  const nodes: ProfileNode[] = []
  props.criteria.forEach((criterion, index) => {
    nodes.push({
      kind: 'criterion',
      key: `criterion-${criterion.criteria_id}`,
      criterion,
      proposals: updateProposalsByCriteria.value.get(String(criterion.criteria_id)) || [],
    })
    const section = criterion.section || ''
    const nextSection = props.criteria[index + 1]?.section || ''
    if (nextSection === section) return
    createProposals.value
      .filter((proposal) => (proposal.suggested_criterion.section || '') === section)
      .forEach((proposal) => {
        nodes.push({
          kind: 'create',
          key: `proposal-${proposal.proposal_id}`,
          proposal,
        })
        remainingCreates.delete(proposal.proposal_id)
      })
  })
  createProposals.value
    .filter((proposal) => remainingCreates.has(proposal.proposal_id))
    .forEach((proposal) =>
      nodes.push({
        kind: 'create',
        key: `proposal-${proposal.proposal_id}`,
        proposal,
      }),
    )
  return nodes
})

const evidenceByCriteria = computed(() => {
  const grouped = new Map<string, HighlightItem[]>()
  props.highlights.forEach((highlight) => {
    if (highlight.source === 'implicit') return
    const criteriaId = String(highlight.criteria_id || '')
    if (!criteriaById.value.has(criteriaId)) return
    const existing = grouped.get(criteriaId) || []
    existing.push(highlight)
    grouped.set(criteriaId, existing)
  })
  return grouped
})

watch(
  () => props.proposals.map((proposal) => proposal.proposal_id),
  (ids) => {
    if (selectedProposalId.value && !ids.includes(selectedProposalId.value)) {
      selectedProposalId.value = null
    }
  },
)

const criterionGrading = (criterion: CriteriaItem) =>
  gradingByCriteria.value.get(String(criterion.criteria_id)) || null
const criterionScore = (criterion: CriteriaItem) =>
  Math.max(0, Number(criterionGrading(criterion)?.criteria_score) || 0)
const criterionMaximum = (criterion: CriteriaItem) => Math.max(0, Number(criterion.max_score) || 0)
const criterionScoreSource = (criterion: CriteriaItem) => {
  const source = graderType(criterionGrading(criterion))
  if (source === 'teacher' || source === 'user') return 'teacher'
  if (source === 'model' || source === 'llm') return 'model'
  return 'unknown'
}
const criterionScoreColor = (criterion: CriteriaItem) =>
  gradingVisualConfig[criterionScoreSource(criterion)].dotFill
const criterionSegments = (criterion: CriteriaItem) => {
  const grading = criterionGrading(criterion)
  const points = assessmentPoints(criterion, grading)
  if (points.length) return points
  return [
    {
      point: null,
      score: criterionScore(criterion),
      maxScore: criterionMaximum(criterion),
      matched: false,
    },
  ]
}
const segmentWeight = (maximum: number) => ({
  flexGrow: Math.max(maximum, 0.01),
})
const segmentFill = (score: number | null, maximum: number) => ({
  height: `${scorePercent(score, maximum)}%`,
})
const criterionEvidence = (criterion: CriteriaItem) =>
  evidenceByCriteria.value.get(String(criterion.criteria_id)) || []
const criterionStatus = (criterion: CriteriaItem) => {
  const criteriaId = String(criterion.criteria_id)
  if (conflictIds.value.has(criteriaId)) return 'conflict'
  if (reviewIds.value.has(criteriaId)) return 'review'
  return 'complete'
}
const isSelectedCriterion = (criterion: CriteriaItem) =>
  !selectedProposal.value && String(props.activeCriteriaId || '') === String(criterion.criteria_id)
const proposalSourceCount = (proposal: CriterionProposalItem) =>
  (proposal.source_implicit_grade_ids?.length || 0) + (proposal.source_event_ids?.length || 0)
const proposalSourceLabel = (proposal: CriterionProposalItem) => {
  const sourceCount = proposalSourceCount(proposal)
  return t('{count} source evidence', { count: sourceCount })
}
const proposalForPending = (item: ImplicitGradeItem) =>
  proposalByImplicitId.value.get(item.implicit_grade_id) || null
const pendingTargetLabel = (item: ImplicitGradeItem) => {
  const proposal = proposalForPending(item)
  if (!proposal) {
    return item.status === 'failed' ? t('Binding generation failed') : t('Generating binding')
  }
  if (proposal.proposal_type === 'update_criterion') {
    const target = criteriaById.value.get(String(proposal.target_criterion_id || ''))
    return `${t('Merge')} → ${target?.criteria_name || t('Scoring criterion')}`
  }
  return `${t('New')} → ${proposal.suggested_criterion.criteria_name}`
}
const pendingEvidenceLabel = (item: ImplicitGradeItem) =>
  item.anchor.text || item.anchor.description || item.anchor.missing_item || t('Selected evidence')
const pendingStatusLabel = (item: ImplicitGradeItem) => {
  if (proposalForPending(item)) return t('Ready to bind')
  return t('Analyzing')
}
const pendingStatusClass = (item: ImplicitGradeItem) => {
  if (proposalForPending(item)) return 'status-fill-review'
  if (item.status === 'failed') return 'status-fill-conflict'
  return 'status-fill-running'
}
const pendingFailureMessage = (item: ImplicitGradeItem) => {
  const failure = item.failure_reason || ''
  if (failure.includes('scoring_points') || failure.includes('scoring_aggregation')) {
    return t('The model returned incomplete scoring details. Your evidence and score were kept.')
  }
  return t('Rubric generation failed. Your evidence and score were kept.')
}
const reviewPending = (item: ImplicitGradeItem) => {
  const proposal = proposalForPending(item)
  if (proposal) emit('reviewProposal', proposal)
}
const selectedProposalTarget = computed(() => {
  const proposal = selectedProposal.value
  if (!proposal?.target_criterion_id) return null
  return criteriaById.value.get(String(proposal.target_criterion_id)) || null
})
const selectedProposalTypeLabel = computed(() =>
  selectedProposal.value?.proposal_type === 'update_criterion'
    ? t('Merge into existing criterion')
    : t('Create new criterion'),
)
const selectedProposalScoreLabel = computed(() => {
  const proposal = selectedProposal.value
  if (!proposal) return ''
  if (proposal.proposal_type === 'update_criterion' && selectedProposalTarget.value) {
    const target = selectedProposalTarget.value
    return `${formatAssessmentScore(criterionScore(target))}/${formatAssessmentScore(criterionMaximum(target))}`
  }
  return t('{score} points', {
    score: formatAssessmentScore(Number(proposal.suggested_criterion.max_score) || 0),
  })
})
const selectedProposalDescription = computed(() => {
  const proposal = selectedProposal.value
  if (!proposal) return ''
  return proposal.rationale || proposal.suggested_criterion.description || ''
})

const selectCriterion = (criterion: CriteriaItem) => {
  selectedProposalId.value = null
  emit('selectCriterion', String(criterion.criteria_id))
}
const selectProposal = (proposal: CriterionProposalItem) => {
  selectedProposalId.value = proposal.proposal_id
  if (proposal.target_criterion_id) emit('selectCriterion', String(proposal.target_criterion_id))
}
const selectFirstUpdateProposal = (node: CriterionProfileNode) => {
  const proposal = node.proposals[0]
  if (proposal) selectProposal(proposal)
}
</script>

<template>
  <section class="min-w-0" data-keep-card-selection data-testid="report-score-profile">
    <div class="flex flex-wrap items-start justify-between gap-x-4 gap-y-1 text-[10px]">
      <div>
        <div class="font-semibold text-slate-700">
          {{ t('Current report score profile') }}
        </div>
        <div class="mt-0.5 text-slate-400">
          {{ formatAssessmentScore(props.reportScore) }}/{{
            formatAssessmentScore(props.baseScore)
          }}
          <template v-if="props.proposals.length">
            ·
            {{
              t('{count} rubric bindings ready', {
                count: props.proposals.length,
              })
            }}
          </template>
        </div>
      </div>
      <div class="flex flex-wrap items-center justify-end gap-x-3 gap-y-1 text-slate-400">
        <span class="inline-flex items-center gap-1"
          ><span
            class="h-3 w-1.5"
            :style="{ backgroundColor: gradingVisualConfig.model.dotFill }"
          />{{ t('Model') }}</span
        >
        <span class="inline-flex items-center gap-1"
          ><span
            class="h-3 w-1.5"
            :style="{ backgroundColor: gradingVisualConfig.teacher.dotFill }"
          />{{ t('Teacher') }}</span
        >
        <span class="inline-flex items-center gap-1"
          ><span class="h-3 w-1.5 bg-slate-200" />{{ t('Not earned') }}</span
        >
        <span class="inline-flex items-center gap-1"
          ><span class="flex items-center gap-0.5"
            ><span
              class="h-1.5 w-1.5 rounded-full"
              :style="{
                backgroundColor: gradingVisualConfig.model.dotFill,
              }" /><span
              class="h-1.5 w-1.5 rounded-full"
              :style="{
                backgroundColor: gradingVisualConfig.teacher.dotFill,
              }" /></span
          >{{ t('Bound evidence') }}</span
        >
        <span class="inline-flex items-center gap-1"
          ><span class="h-1.5 w-1.5 rounded-full bg-[var(--status-review)]" />{{
            t('Proposed binding')
          }}</span
        >
      </div>
    </div>

    <div v-if="profileNodes.length" class="mt-1.5 grid grid-cols-[3.25rem_minmax(0,1fr)]">
      <div
        class="relative h-[6.75rem] bg-white"
        aria-hidden="true"
        data-testid="score-profile-axis"
      >
        <span
          class="absolute left-0 top-7 text-[9px] font-medium uppercase tracking-wide text-slate-400"
          >{{ t('Score') }}</span
        >
        <span
          class="absolute left-0 top-[4.4rem] text-[9px] font-medium uppercase tracking-wide text-slate-400"
          >{{ t('Evidence') }}</span
        >
      </div>
      <div
        class="min-w-0 overflow-x-auto pl-1 pb-1 scrollbar-thin scrollbar-thumb-slate-200"
        data-testid="score-profile-scroll-area"
      >
        <div
          class="grid h-[6.75rem] min-w-full grid-flow-col auto-cols-[minmax(2rem,1fr)]"
          :style="{ minWidth: `${Math.max(profileNodes.length * 34, 320)}px` }"
        >
          <div
            v-for="node in profileNodes"
            :key="node.key"
            class="relative flex h-[6.5rem] min-w-0 justify-center"
          >
            <template v-if="node.kind === 'criterion'">
              <button
                type="button"
                class="group relative h-full w-full min-w-0 rounded-md focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[var(--interaction-active)]"
                :class="{
                  'bg-[var(--interaction-active-soft)] ring-1 ring-inset ring-[#86b5b0]':
                    isSelectedCriterion(node.criterion),
                }"
                :aria-label="`${node.criterion.criteria_name}, ${formatAssessmentScore(criterionScore(node.criterion))} of ${formatAssessmentScore(criterionMaximum(node.criterion))}`"
                :data-testid="`score-profile-criterion-${node.criterion.criteria_id}`"
                @click="selectCriterion(node.criterion)"
              >
                <span
                  v-if="criterionStatus(node.criterion) !== 'complete'"
                  class="absolute left-1/2 top-0 z-10 grid h-4 w-4 translate-x-1 place-items-center rounded-full text-[9px] font-bold"
                  :class="
                    criterionStatus(node.criterion) === 'conflict'
                      ? 'status-badge-conflict'
                      : 'status-badge-review'
                  "
                  >{{ criterionStatus(node.criterion) === 'conflict' ? '×' : '!' }}</span
                >
                <span
                  class="absolute left-1/2 top-5 flex h-10 w-3.5 -translate-x-1/2 flex-col-reverse gap-0.5"
                  aria-hidden="true"
                >
                  <span
                    v-for="(segment, segmentIndex) in criterionSegments(node.criterion)"
                    :key="segment.point?.point_id || segmentIndex"
                    class="relative min-h-0 overflow-hidden bg-slate-200"
                    :style="segmentWeight(segment.maxScore)"
                  >
                    <span
                      class="absolute inset-x-0 bottom-0"
                      :style="{
                        ...segmentFill(segment.score, segment.maxScore),
                        backgroundColor: criterionScoreColor(node.criterion),
                      }"
                      :data-score-source="criterionScoreSource(node.criterion)"
                      :data-testid="`score-profile-earned-${node.criterion.criteria_id}`"
                    />
                  </span>
                </span>
                <span
                  class="absolute left-1/2 top-[4.25rem] flex -translate-x-1/2 items-center gap-0.5"
                  aria-hidden="true"
                >
                  <span
                    v-for="evidence in criterionEvidence(node.criterion).slice(0, 3)"
                    :key="evidence.id"
                    class="h-1.5 w-1.5 rounded-full"
                    :class="
                      evidence.anchor.anchor_type === 'absence'
                        ? 'border border-slate-400 bg-white'
                        : evidenceColorClass(evidence.color)
                    "
                    :data-testid="`score-profile-evidence-${evidence.id}`"
                  />
                  <span
                    v-if="criterionEvidence(node.criterion).length > 3"
                    class="text-[8px] text-slate-400"
                    >+{{ criterionEvidence(node.criterion).length - 3 }}</span
                  >
                </span>
                <span
                  class="absolute inset-x-0 bottom-1 truncate px-0.5 text-center text-[9px] tabular-nums text-slate-400"
                  >{{ formatAssessmentScore(criterionScore(node.criterion)) }}/{{
                    formatAssessmentScore(criterionMaximum(node.criterion))
                  }}</span
                >
              </button>

              <button
                v-if="node.proposals[0]"
                type="button"
                class="absolute left-1/2 top-0 z-20 -translate-x-1/2 rounded border border-dashed border-[#d8b77f] bg-[#f8efe1] px-1 py-0.5 text-[8px] font-bold tracking-wide text-[#79531d] hover:bg-[#f2dfbf] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--status-review)]"
                :class="{
                  'border-solid bg-[#f2dfbf] ring-1 ring-[#d8b77f]':
                    selectedProposalId === node.proposals[0]?.proposal_id,
                }"
                :data-testid="`score-profile-proposal-${node.proposals[0]?.proposal_id}`"
                @click.stop="selectFirstUpdateProposal(node)"
              >
                {{ t('Update')
                }}<span v-if="node.proposals.length > 1">+{{ node.proposals.length - 1 }}</span>
              </button>
              <span
                v-if="node.proposals[0]"
                class="pointer-events-none absolute left-1/2 top-4 h-[3.9rem] border-l border-dashed border-[#d8b77f]"
                aria-hidden="true"
              />
              <span
                v-if="node.proposals[0]"
                class="pointer-events-none absolute left-1/2 top-[4.25rem] h-1.5 w-1.5 -translate-x-1/2 rounded-full bg-[var(--status-review)]"
                aria-hidden="true"
              />
            </template>

            <button
              v-else
              type="button"
              class="relative h-full w-full rounded-md border border-dashed border-[#d8b77f] bg-[#f8efe1] focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[var(--status-review)]"
              :class="{
                'border-solid bg-[#f2dfbf] ring-1 ring-[#d8b77f]':
                  selectedProposalId === node.proposal.proposal_id,
              }"
              :aria-label="`${t('Create new criterion')}: ${node.proposal.suggested_criterion.criteria_name}`"
              :data-testid="`score-profile-proposal-${node.proposal.proposal_id}`"
              @click="selectProposal(node.proposal)"
            >
              <span
                class="absolute inset-x-0 top-1 text-center text-[8px] font-bold tracking-wide text-[#79531d]"
                >{{ t('New') }}</span
              >
              <span
                class="absolute left-1/2 top-5 h-10 w-3.5 -translate-x-1/2 border border-dashed border-[#d8b77f] bg-[#f2dfbf]"
                aria-hidden="true"
              />
              <span
                class="absolute left-1/2 top-[4.25rem] h-1.5 w-1.5 -translate-x-1/2 rounded-full bg-[var(--status-review)]"
                aria-hidden="true"
              />
              <span
                class="absolute inset-x-0 bottom-1 text-center text-[11px] text-[var(--status-review)]"
                >+</span
              >
            </button>
          </div>
        </div>
      </div>
    </div>

    <div
      v-if="props.pendingItems.length"
      class="border-t border-amber-100 bg-amber-50/35 text-[10px]"
      data-testid="score-profile-pending-list"
    >
      <button
        type="button"
        class="flex w-full items-center gap-2 px-3 py-2 text-left hover:bg-amber-50/70"
        :aria-expanded="pendingExpanded"
        @click="pendingExpanded = !pendingExpanded"
      >
        <span class="font-semibold text-slate-700">{{ t('Pending bindings') }}</span>
        <span
          class="rounded-full bg-amber-100 px-1.5 py-0.5 font-semibold tabular-nums text-amber-800"
          >{{ props.pendingItems.length }}</span
        >
        <span class="text-slate-400">{{ t('Selected evidence awaiting rubric binding') }}</span>
        <span
          class="ml-auto text-slate-400 transition-transform"
          :class="{ 'rotate-180': pendingExpanded }"
          aria-hidden="true"
          >⌄</span
        >
      </button>

      <div
        v-show="pendingExpanded"
        class="max-h-36 overflow-y-auto border-t border-amber-100 scrollbar-thin scrollbar-thumb-amber-200"
      >
        <div
          v-for="item in props.pendingItems"
          :key="item.implicit_grade_id"
          class="grid cursor-pointer grid-cols-[minmax(8rem,.9fr)_minmax(10rem,1.35fr)_auto] items-center gap-3 border-b border-amber-100/80 px-3 py-2 last:border-b-0 hover:bg-white/70"
          :class="{
            'bg-white ring-1 ring-inset ring-amber-300':
              props.activePendingId === item.implicit_grade_id,
          }"
          data-testid="score-profile-pending-item"
          @click="emit('selectPending', item)"
        >
          <div class="min-w-0">
            <div class="flex items-center gap-1.5">
              <span class="h-1.5 w-1.5 shrink-0 rounded-full" :class="pendingStatusClass(item)" />
              <span class="truncate font-semibold text-slate-700">
                {{ item.temporary_label || t('Unbound grading evidence') }}
              </span>
            </div>
            <div class="mt-0.5 truncate text-slate-400">
              {{ pendingEvidenceLabel(item) }}
            </div>
          </div>

          <div class="min-w-0">
            <div class="truncate font-medium text-slate-600">
              {{ pendingTargetLabel(item) }}
            </div>
            <div
              class="mt-0.5 truncate"
              :class="item.status === 'failed' ? 'text-rose-600' : 'text-slate-400'"
            >
              {{
                item.status === 'failed' ? pendingFailureMessage(item) : pendingStatusLabel(item)
              }}
            </div>
          </div>

          <div class="flex items-center justify-end gap-1.5" @click.stop>
            <span
              class="rounded-md bg-white px-2 py-1 font-bold tabular-nums text-slate-700 ring-1 ring-inset ring-slate-200"
            >
              {{ formatAssessmentScore(item.score)
              }}<template v-if="item.max_score"
                >/{{ formatAssessmentScore(item.max_score) }}</template
              >
            </span>
            <button
              type="button"
              class="rounded-md border border-slate-200 bg-white px-2 py-1 font-semibold text-slate-600 hover:bg-slate-50"
              :data-testid="'edit-uninduced-' + item.implicit_grade_id"
              @click="emit('editPending', item)"
            >
              {{ t('Edit') }}
            </button>
            <button
              v-if="item.status === 'failed'"
              type="button"
              class="rounded-md border border-rose-200 bg-white px-2 py-1 font-semibold text-rose-700 hover:bg-rose-50 disabled:opacity-50"
              :data-testid="'retry-uninduced-' + item.implicit_grade_id"
              :disabled="props.pendingRetryingId === item.implicit_grade_id"
              @click="emit('retryPending', item)"
            >
              {{ t('Retry') }}
            </button>
            <button
              v-if="proposalForPending(item)"
              type="button"
              class="rounded-md bg-[var(--status-review)] px-2 py-1 font-semibold text-white hover:bg-[#94631f]"
              @click="reviewPending(item)"
            >
              {{ t('Review') }}
            </button>
          </div>
        </div>
      </div>
    </div>

    <div
      v-if="selectedProposal"
      class="grid gap-0 border-t border-[#ead8b9] bg-[#f8efe1] text-[10px] md:grid-cols-[minmax(10rem,.72fr)_minmax(15rem,1.28fr)_auto]"
      data-testid="score-profile-proposal-detail"
    >
      <div class="px-3 py-2">
        <div class="font-semibold text-[#79531d]">
          {{ selectedProposalTypeLabel }}
        </div>
        <div class="mt-0.5 truncate font-semibold text-slate-700">
          {{ selectedProposal.suggested_criterion.criteria_name }}
        </div>
        <div class="mt-0.5 text-slate-400">
          {{ selectedProposalScoreLabel }} ·
          {{ proposalSourceLabel(selectedProposal) }}
        </div>
      </div>
      <div class="border-[#ead8b9] px-3 py-2 md:border-l">
        <div class="line-clamp-2 leading-relaxed text-slate-600">
          {{ selectedProposalDescription }}
        </div>
      </div>
      <div class="flex items-center justify-end gap-2 border-[#ead8b9] px-3 py-2 md:border-l">
        <button
          type="button"
          class="rounded-md border border-[#d8b77f] bg-white px-2.5 py-1.5 font-semibold text-[#79531d] hover:bg-[#f8efe1]"
          :disabled="props.proposalSavingId === selectedProposal.proposal_id"
          @click="emit('reviewProposal', selectedProposal)"
        >
          {{ t('Review details') }}
        </button>
        <button
          type="button"
          class="rounded-md bg-[var(--status-review)] px-2.5 py-1.5 font-semibold text-white hover:bg-[#94631f] disabled:cursor-wait disabled:opacity-60"
          :disabled="props.proposalSavingId === selectedProposal.proposal_id"
          data-testid="score-profile-confirm-proposal"
          @click="emit('confirmProposal', selectedProposal)"
        >
          {{
            props.proposalSavingId === selectedProposal.proposal_id
              ? t('Confirming...')
              : selectedProposal.proposal_type === 'update_criterion'
                ? t('Confirm merge')
                : t('Confirm new criterion')
          }}
        </button>
      </div>
      <div
        v-if="props.proposalError"
        class="border-t border-rose-100 px-3 py-2 text-rose-700 md:col-span-3"
        role="alert"
      >
        {{ props.proposalError }}
      </div>
    </div>
  </section>
</template>
