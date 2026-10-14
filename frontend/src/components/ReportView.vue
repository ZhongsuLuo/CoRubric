<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { useGradingStore } from '../stores/grading'
import MarkdownRenderer from './MarkdownRenderer.vue'
import GlobalEvidenceRenderer from './GlobalEvidenceRenderer.vue'
import { gradingVisualConfig } from '../config/gradingVisualConfig'
import type { GlobalScorePoint, HighlightItem } from '../types/schema'
import { translate as t } from '../i18n'
import {
  findTargetSection,
  formatScore,
  getReportAutoGradeLabel,
  getReportAutoGradeProgress,
  getReportAutoGradeTone,
} from '../utils/reportPresentation'
const $t = t

const store = useGradingStore()
const props = withDefaults(defineProps<{ suspended?: boolean }>(), {
  suspended: false,
})
const showSingle = computed(() => props.suspended || store.mode === 'single')
const rendererRef = ref<InstanceType<typeof MarkdownRenderer> | null>(null)
const globalScrollerRef = ref<HTMLElement | null>(null)
const globalCardElements = new Map<string, HTMLElement>()
const focusedGlobalCardKey = ref<string | null>(null)
let globalFocusTimer: number | null = null
const PENDING_SELECTION_ID = '__pending_selection__'

const selection = ref<{ text: string; x: number; y: number } | null>(null)
const score = ref(0)
const reason = ref('')
const annotationSaving = ref(false)
const annotationError = ref('')
const contextMenu = ref<{ id: string; x: number; y: number } | null>(null)

const selectedCriteriaId = computed(() => {
  const id = store.activeCriteriaId
  if (!id || id === store.UNINDUCED_CRITERIA_ID) return null
  return store.criteriaSchema?.criteria.some((item) => String(item.criteria_id) === String(id))
    ? String(id)
    : null
})
const selectedCriterion = computed(
  () =>
    store.criteriaSchema?.criteria.find(
      (item) => String(item.criteria_id) === selectedCriteriaId.value,
    ) || null,
)
const currentSelectedGrading = computed(() => {
  if (!selectedCriteriaId.value) return null
  return (
    store.gradingResults[store.activeReportId]?.criteria.find(
      (item) => String(item.criteria_id) === selectedCriteriaId.value,
    ) || null
  )
})
const canUpdateEvidence = computed(() =>
  Boolean(selection.value && selectedCriteriaId.value && currentSelectedGrading.value),
)

const pendingSelectionHighlight = computed<HighlightItem | null>(() => {
  if (!selection.value || !store.activeReport) return null
  return {
    id: PENDING_SELECTION_ID,
    report_id: store.activeReport.id,
    criteria_id: PENDING_SELECTION_ID,
    score: 0,
    reason: '',
    source: 'teacher',
    color: 'purple',
    anchor: { text: selection.value.text, occurrence: 0 },
  }
})
const visibleHighlights = computed<HighlightItem[]>(() =>
  pendingSelectionHighlight.value
    ? [...store.reportHighlights, pendingSelectionHighlight.value]
    : store.reportHighlights,
)
const rendererActiveHighlightId = computed(() =>
  pendingSelectionHighlight.value ? PENDING_SELECTION_ID : store.activeHighlightId,
)
const rendererActiveHighlightIds = computed(() => {
  if (pendingSelectionHighlight.value) return [PENDING_SELECTION_ID]
  const criteriaId = selectedCriteriaId.value
  if (!criteriaId) return store.activeHighlightId ? [store.activeHighlightId] : []
  return store.reportHighlights
    .filter((item) => String(item.criteria_id) === criteriaId && item.source !== 'implicit')
    .map((item) => item.id)
})
const selectionPopupStyle = computed(() => {
  if (!selection.value) return {}
  const width = 320
  return {
    left: `${Math.max(12, Math.min(selection.value.x + 12, window.innerWidth - width - 12))}px`,
    top: `${Math.max(12, Math.min(selection.value.y + 12, window.innerHeight - 300))}px`,
  }
})
const reportScore = computed(() => store.currentReportScore)
const hasReportScore = computed(
  () => Boolean(store.gradingResults[store.activeReportId]?.criteria.length),
)
const reportAutoGradeProgress = computed(() =>
  getReportAutoGradeProgress(store.activeAutoGradeBatch, store.activeReportId),
)
const reportAutoGradeLabel = computed(() => getReportAutoGradeLabel(reportAutoGradeProgress.value))
const reportAutoGradeTone = computed(() => getReportAutoGradeTone(reportAutoGradeProgress.value))
const evidenceNavigationMessage = ref('')
const reportIndex = computed(() =>
  store.reports.findIndex((report) => report.id === store.activeReportId),
)
const reportCount = computed(() => store.reports.length)
const globalSummary = computed(() => store.getCriterionGlobalSummary(selectedCriteriaId.value))
const reportOrder = computed(
  () => new Map(store.reports.map((report, index) => [report.id, index])),
)
const globalPoints = computed(() =>
  [...(globalSummary.value?.points || [])].sort((left, right) => {
    const leftIndex = reportOrder.value.get(left.report_id) ?? Number.MAX_SAFE_INTEGER
    const rightIndex = reportOrder.value.get(right.report_id) ?? Number.MAX_SAFE_INTEGER
    return leftIndex - rightIndex
  }),
)
const globalPointKey = (point: GlobalScorePoint) =>
  point.event_id ? `event-${point.event_id}` : `${point.criterion_id}-${point.report_id}`
const selectedGlobalPointKey = computed(() => {
  const focus = store.globalReportFocus
  if (!focus || focus.criterionId !== selectedCriteriaId.value) return null
  return focus.eventId ? `event-${focus.eventId}` : `${focus.criterionId}-${focus.reportId}`
})
const reportForPoint = (point: GlobalScorePoint) =>
  store.reports.find((report) => report.id === point.report_id) || null
const authorConfig = (point: GlobalScorePoint) =>
  gradingVisualConfig[point.author] || gradingVisualConfig.unknown
const setGlobalCardElement = (key: string, element: Element | null) => {
  if (element instanceof HTMLElement) globalCardElements.set(key, element)
  else globalCardElements.delete(key)
}

const scrollToGlobalFocus = () => {
  const focus = store.globalReportFocus
  if (!focus || focus.criterionId !== selectedCriteriaId.value) return
  const key = focus.eventId ? `event-${focus.eventId}` : `${focus.criterionId}-${focus.reportId}`
  void nextTick(() => {
    const target = globalCardElements.get(key)
    const scroller = globalScrollerRef.value
    if (!target || !scroller) return
    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    const targetTop =
      target.offsetTop - Math.max((scroller.clientHeight - target.offsetHeight) / 2, 16)
    scroller.scrollTo({
      top: Math.max(targetTop, 0),
      behavior: reducedMotion ? 'auto' : 'smooth',
    })
    target.focus({ preventScroll: true })
    focusedGlobalCardKey.value = null
    void nextTick(() => {
      focusedGlobalCardKey.value = key
      if (globalFocusTimer !== null) window.clearTimeout(globalFocusTimer)
      globalFocusTimer = window.setTimeout(
        () => {
          if (focusedGlobalCardKey.value === key) focusedGlobalCardKey.value = null
          globalFocusTimer = null
        },
        reducedMotion ? 900 : 1500,
      )
    })
  })
}

const clearSelection = () => {
  selection.value = null
  score.value = 0
  reason.value = ''
  annotationError.value = ''
}

const onSelection = (payload: { text: string; x: number; y: number }) => {
  contextMenu.value = null
  annotationError.value = ''
  selection.value = payload
  const current = currentSelectedGrading.value
  score.value = Number(current?.criteria_score) || 0
  reason.value = current?.score_reason || ''
}

const scrollToActiveHighlight = () => {
  void nextTick(() => {
    if (store.activeHighlightId) rendererRef.value?.scrollToHighlight(store.activeHighlightId)
  })
}

const saveSelection = async () => {
  if (!selection.value || !store.activeReport || annotationSaving.value) return
  const criterionId = selectedCriteriaId.value
  const maxScore = Number(selectedCriterion.value?.max_score)
  if (criterionId && Number.isFinite(maxScore) && score.value > maxScore) {
    annotationError.value = `The score cannot exceed the criterion maximum of ${maxScore}.`
    return
  }

  annotationSaving.value = true
  annotationError.value = ''
  const report = store.activeReport
  const anchor = { ...selection.value }
  try {
    await store.saveTeacherHighlight({
      report_id: report.id,
      criteria_id: criterionId,
      score: Number.isFinite(score.value) ? score.value : 0,
      max_score: null,
      reason: reason.value,
      target_section: findTargetSection(report.markdown || '', anchor.text),
      anchor,
    })
    clearSelection()
    scrollToActiveHighlight()
  } catch (error) {
    console.error('[annotation] save failed', error)
    annotationError.value = error instanceof Error ? error.message : $t('Failed to save annotation')
  } finally {
    annotationSaving.value = false
  }
}

const updateEvidenceAnchor = async () => {
  if (
    !selection.value ||
    !store.activeReport ||
    !currentSelectedGrading.value ||
    !selectedCriteriaId.value ||
    annotationSaving.value
  )
    return

  annotationSaving.value = true
  annotationError.value = ''
  const report = store.activeReport
  const anchor = { ...selection.value }
  const grading = currentSelectedGrading.value
  try {
    await store.saveGradeDirect({
      reportId: report.id,
      criteriaId: selectedCriteriaId.value,
      score: Number(grading.criteria_score) || 0,
      reason: grading.score_reason || '',
      anchorText: anchor.text,
      targetSection: findTargetSection(report.markdown || '', anchor.text),
      matchedScoringPoints: grading.matched_scoring_points || [],
      matchedDeductionRules: grading.matched_deduction_rules || [],
      scoringPointScores: grading.scoring_point_scores || [],
    })
    clearSelection()
    scrollToActiveHighlight()
  } catch (error) {
    console.error('[annotation] evidence update failed', error)
    annotationError.value =
      error instanceof Error ? error.message : $t('Failed to update evidence selection')
  } finally {
    annotationSaving.value = false
  }
}

const onHighlightClick = (id: string) => {
  contextMenu.value = null
  if (id === PENDING_SELECTION_ID) return
  store.setActiveHighlight(id)
  const highlight = store.reportHighlights.find((item) => item.id === id)
  if (!highlight) return
  if (highlight.source === 'implicit' && highlight.implicit_grade_id) {
    store.setActiveImplicitGrade(highlight.implicit_grade_id)
  } else {
    store.setActiveCriteria(highlight.criteria_id)
  }
}

const onHighlightContext = (payload: { id: string; x: number; y: number }) => {
  const highlight = store.reportHighlights.find((item) => item.id === payload.id)
  if (!highlight) return
  store.setActiveHighlight(payload.id)
  if (highlight.source === 'llm') {
    alert('Model annotations cannot be deleted here. Delete a teacher annotation instead.')
    return
  }
  contextMenu.value = payload
}

const deleteHighlight = async () => {
  const target =
    contextMenu.value && store.reportHighlights.find((item) => item.id === contextMenu.value?.id)
  contextMenu.value = null
  if (!target) return
  try {
    await store.deleteTeacherHighlight({
      report_id: target.report_id,
      highlight_id: target.id,
      criteria_id: target.criteria_id,
    })
  } catch (error) {
    console.error('[annotation] delete failed', error)
    annotationError.value =
      error instanceof Error ? error.message : $t('Failed to delete annotation')
  }
}

const goToReport = (offset: number) => {
  const report = store.reports[reportIndex.value + offset]
  if (report) store.setActiveReport(report.id)
}

const onReportSelect = (event: Event) => {
  const reportId = (event.target as HTMLSelectElement).value
  if (reportId) store.setActiveReport(reportId)
}

const focusRequestedEvidence = async () => {
  evidenceNavigationMessage.value = ''
  const criteriaId = selectedCriteriaId.value
  if (!criteriaId) return
  const activeId = store.activeHighlightId
  const candidateIds = store.reportHighlights
    .filter((item) => String(item.criteria_id) === criteriaId)
    .sort((left, right) => {
      if (left.id === activeId) return -1
      if (right.id === activeId) return 1
      if (left.source === right.source) return 0
      return left.source === 'teacher' ? -1 : 1
    })
    .map((item) => item.id)
  if (!candidateIds.length) {
    evidenceNavigationMessage.value = $t(
      'No grounded source location is available for this scoring item.',
    )
    return
  }

  const focusedId = await rendererRef.value?.scrollToFirstHighlight(candidateIds)
  if (focusedId) {
    store.setActiveHighlight(focusedId)
  } else {
    evidenceNavigationMessage.value = $t('The saved evidence could not be located in this report.')
  }
}

watch(
  () => [store.activeHighlightId, store.highlightFocusToken] as const,
  () => {
    if (showSingle.value) void focusRequestedEvidence()
  },
)

watch(
  () => store.evidenceAdjustmentToken,
  async () => {
    if (!showSingle.value || !selectedCriteriaId.value) return
    if (store.activeHighlightId) await focusRequestedEvidence()
    evidenceNavigationMessage.value = store.activeHighlightId
      ? $t('Select a new text range in the report, then choose Update selection.')
      : $t('Select a text range in the report to add evidence for this scoring item.')
  },
)

watch(
  () => store.activeReportId,
  () => {
    evidenceNavigationMessage.value = ''
  },
)

watch(
  () => store.globalReportFocus?.token,
  () => {
    if (store.mode === 'global') scrollToGlobalFocus()
  },
)

watch(
  () => [store.mode, selectedCriteriaId.value],
  () => {
    clearSelection()
    contextMenu.value = null
    focusedGlobalCardKey.value = null
  },
)

onBeforeUnmount(() => {
  if (globalFocusTimer !== null) window.clearTimeout(globalFocusTimer)
})
</script>

<template>
  <div class="flex h-full min-h-0 flex-col bg-white">
    <header
      v-if="showSingle"
      class="z-10 flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 bg-white px-5 py-3 shadow-[0_1px_2px_rgba(0,0,0,0.02)]"
    >
      <div class="flex items-center gap-2">
        <span
          class="inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset"
          :class="
            store.activeReport?.conversion_status === 'finished'
              ? 'status-badge-verified'
              : 'status-badge-running'
          "
        >
          {{
            $t(store.activeReport?.conversion_status === 'finished' ? 'Converted' : 'Converting')
          }}
        </span>
        <span
          v-if="store.conflictCount > 0"
          class="status-badge-conflict inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset"
          >{{ $t('Conflict') }}: {{ store.conflictCount }}</span
        >
        <span
          v-if="reportAutoGradeProgress"
          data-testid="report-auto-grading-status"
          class="inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset"
          :class="{
            'status-badge-running':
              reportAutoGradeTone === 'running' || reportAutoGradeTone === 'queued',
            'status-badge-verified': reportAutoGradeTone === 'complete',
            'status-badge-review': reportAutoGradeTone === 'issues',
          }"
          role="status"
        >
          <span
            v-if="reportAutoGradeTone === 'running' || reportAutoGradeTone === 'queued'"
            class="h-1.5 w-1.5 animate-pulse rounded-full bg-[var(--status-running)]"
          />
          {{ reportAutoGradeLabel }}
        </span>
        <span
          v-if="hasReportScore"
          data-testid="report-total-score"
          class="inline-flex items-center rounded-full bg-slate-50 px-2.5 py-0.5 text-xs font-medium text-[var(--text-primary)] ring-1 ring-inset ring-slate-500/10"
          >{{ $t('Score') }}: <span class="ml-1 font-bold">{{ reportScore }}</span
          >/{{ formatScore(store.gradingResults[store.activeReportId]?.base_score ?? store.criteriaSchema?.base_score ?? 100) }}</span
        >
      </div>
      <div class="flex items-center gap-2 rounded-lg bg-slate-50 p-1">
        <button
          class="rounded p-1 text-slate-400 hover:bg-white hover:text-slate-600 disabled:opacity-50"
          :disabled="reportIndex <= 0"
          :title="$t('Previous Report')"
          @click="goToReport(-1)"
        >
          ‹
        </button>
        <select
          :value="store.activeReportId"
          data-testid="single-report-select"
          class="min-w-[120px] rounded bg-transparent py-1 pl-2 pr-1 text-xs font-medium text-slate-600 focus:outline-none"
          @change="onReportSelect"
        >
          <option v-for="report in store.reports" :key="report.id" :value="report.id">
            {{ report.name }}
          </option>
        </select>
        <span class="px-1 text-xs font-medium text-slate-400"
          >{{ reportIndex + 1 }} / {{ reportCount }}</span
        >
        <button
          class="rounded p-1 text-slate-400 hover:bg-white hover:text-slate-600 disabled:opacity-50"
          :disabled="reportIndex >= reportCount - 1"
          :title="$t('Next Report')"
          @click="goToReport(1)"
        >
          ›
        </button>
      </div>
    </header>
    <div
      v-if="showSingle && evidenceNavigationMessage"
      data-testid="evidence-navigation-message"
      class="border-b border-amber-100 bg-amber-50 px-5 py-2 text-xs text-amber-800"
      role="status"
    >
      {{ evidenceNavigationMessage }}
    </div>
    <header
      v-if="!showSingle"
      class="z-10 flex min-h-[57px] items-center justify-between gap-3 border-b border-slate-100 bg-white px-5 py-3 shadow-[0_1px_2px_rgba(0,0,0,0.02)]"
    >
      <div class="min-w-0">
        <div class="text-xs font-semibold uppercase tracking-wider text-slate-400">
          Global evidence
        </div>
        <div class="mt-0.5 truncate text-sm font-bold text-slate-800">
          {{ selectedCriterion?.criteria_name || 'Select a criterion' }}
        </div>
      </div>
      <span
        v-if="selectedCriterion"
        class="shrink-0 rounded-full bg-slate-100 px-2.5 py-1 text-xs font-semibold text-slate-600"
      >
        {{ globalPoints.length }} evidence cards
      </span>
    </header>

    <main class="relative min-h-0 flex-1 overflow-hidden bg-white">
      <div
        ref="globalScrollerRef"
        class="h-full w-full overflow-y-auto"
        :class="
          showSingle
            ? 'scrollbar-hidden'
            : 'scrollbar-thin scrollbar-thumb-slate-300 scrollbar-track-transparent'
        "
        data-testid="report-scroll-container"
      >
        <div v-if="showSingle" class="min-h-full py-6 pl-8 pr-2">
          <MarkdownRenderer
            ref="rendererRef"
            :markdown="store.activeReport?.markdown || ''"
            :highlights="visibleHighlights"
            :active-highlight-id="rendererActiveHighlightId"
            :active-highlight-ids="rendererActiveHighlightIds"
            :show-evidence-map="true"
            @select="onSelection"
            @highlight-click="onHighlightClick"
            @highlight-context="onHighlightContext"
          />
        </div>
        <div
          v-else-if="!selectedCriterion"
          class="flex min-h-full items-center justify-center px-8 py-10 text-center"
          data-testid="global-report-empty-criterion"
        >
          <div>
            <div
              class="mx-auto flex h-11 w-11 items-center justify-center rounded-full bg-slate-50 text-slate-400"
            >
              ◎
            </div>
            <div class="mt-3 text-sm font-semibold text-slate-700">
              {{ $t('Select a criterion') }}
            </div>
            <p class="mt-1 max-w-sm text-xs leading-relaxed text-slate-500">
              Select a criterion in Global view to see its grading evidence in report order.
            </p>
          </div>
        </div>
        <div
          v-else-if="!globalPoints.length"
          class="flex min-h-full items-center justify-center px-8 py-10 text-center"
          data-testid="global-report-empty-points"
        >
          <div>
            <div class="text-sm font-semibold text-slate-700">
              {{ $t('No valid grading evidence') }}
            </div>
            <p class="mt-1 text-xs text-slate-500">
              Ungraded, abstained, and failed reports do not produce evidence cards.
            </p>
          </div>
        </div>
        <div v-else class="space-y-4 bg-slate-50/40 px-6 py-5" data-testid="global-report-list">
          <article
            v-for="point in globalPoints"
            :key="globalPointKey(point)"
            :ref="
              (element) => setGlobalCardElement(globalPointKey(point), element as Element | null)
            "
            tabindex="-1"
            class="global-report-card rounded-xl border bg-white px-4 py-4 shadow-sm outline-none transition-[border-color,box-shadow,background-color]"
            :class="[
              point.conflict ? 'border-rose-200' : 'border-slate-200',
              focusedGlobalCardKey === globalPointKey(point) ? 'global-report-card--focused' : '',
            ]"
            :data-report-id="point.report_id"
            :data-event-id="point.event_id || ''"
            data-testid="global-report-card"
          >
            <header
              class="mb-3 flex flex-wrap items-start justify-between gap-3 border-b border-slate-100 pb-3"
            >
              <div class="min-w-0">
                <div class="truncate text-sm font-bold text-slate-800">
                  {{ point.report_name }}
                </div>
                <div
                  v-if="point.target_section"
                  class="mt-0.5 truncate text-[10px] uppercase tracking-wide text-slate-400"
                >
                  {{ point.target_section }}
                </div>
              </div>
              <div
                class="flex shrink-0 flex-wrap items-center justify-end gap-1.5 text-[10px] font-semibold"
              >
                <span
                  class="rounded-full px-2 py-1 ring-1 ring-inset"
                  :class="authorConfig(point).badgeClass"
                >
                  {{ $t(authorConfig(point).label) }}
                </span>
                <span
                  class="rounded-full bg-slate-100 px-2 py-1 text-slate-700 ring-1 ring-inset ring-slate-500/10"
                >
                  {{ formatScore(point.score) }}/{{ formatScore(point.max_score) }}
                </span>
                <span
                  v-if="point.conflict"
                  class="rounded-full px-2 py-1 ring-1 ring-inset"
                  :class="gradingVisualConfig.conflict.badgeClass"
                >
                  {{ $t('Conflict') }}
                </span>
              </div>
            </header>
            <GlobalEvidenceRenderer
              :markdown="reportForPoint(point)?.markdown || ''"
              :point="point"
              :active="selectedGlobalPointKey === globalPointKey(point)"
            />
          </article>
        </div>
      </div>

      <div
        v-if="showSingle && contextMenu"
        class="fixed z-50 min-w-[120px] overflow-hidden rounded-lg border border-slate-100 bg-white p-1 shadow-xl ring-1 ring-slate-900/5"
        :style="{ top: `${contextMenu.y}px`, left: `${contextMenu.x}px` }"
      >
        <button
          class="flex w-full items-center gap-2 rounded px-3 py-2 text-left text-sm text-rose-600 hover:bg-rose-50"
          @click="deleteHighlight"
        >
          Delete
        </button>
      </div>

      <div
        v-if="showSingle && annotationError && !selection"
        class="absolute bottom-4 left-8 right-8 z-30 rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm font-medium text-rose-700 shadow-sm"
      >
        {{ annotationError }}
      </div>

      <section
        v-if="showSingle && selection"
        class="fixed z-50 w-80 rounded-xl border border-slate-200 bg-white p-4 shadow-xl ring-1 ring-slate-900/5"
        :style="selectionPopupStyle"
      >
        <div class="flex items-start justify-between gap-3">
          <div class="min-w-0">
            <div class="text-sm font-bold text-slate-800">
              {{ $t('Teacher grading') }}
            </div>
            <p class="mt-1 truncate text-xs italic text-slate-500">
              &ldquo;{{ selection.text }}&rdquo;
            </p>
          </div>
          <button
            type="button"
            class="shrink-0 text-slate-400 hover:text-slate-600"
            :disabled="annotationSaving"
            @click="clearSelection"
          >
            ×
          </button>
        </div>
        <label class="mt-3 block text-xs font-semibold text-slate-600"
          >{{ $t('Score')
          }}<input
            v-model.number="score"
            type="number"
            min="0"
            class="mt-1 w-full rounded-md border border-slate-200 px-2.5 py-2 text-sm font-semibold focus:border-[var(--interaction-active)] focus:outline-none"
        /></label>
        <label class="mt-2 block text-xs font-semibold text-slate-600"
          >{{ $t('Notes (optional)')
          }}<input
            v-model="reason"
            class="mt-1 w-full rounded-md border border-slate-200 px-2.5 py-2 text-sm focus:border-[var(--interaction-active)] focus:outline-none"
        /></label>
        <div v-if="annotationError" class="mt-2 text-xs font-medium text-rose-600">
          {{ annotationError }}
        </div>
        <div class="mt-3 flex justify-end gap-2">
          <button
            type="button"
            class="rounded-md px-3 py-2 text-sm text-slate-600 hover:bg-slate-100"
            :disabled="annotationSaving"
            @click="clearSelection"
          >
            {{ $t('Cancel') }}
          </button>
          <button
            v-if="canUpdateEvidence"
            type="button"
            class="rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm font-medium text-emerald-700 hover:bg-emerald-100 disabled:opacity-50"
            :disabled="annotationSaving"
            @click="updateEvidenceAnchor"
          >
            {{ annotationSaving ? $t('Updating…') : $t('Update selection') }}
          </button>
          <button
            type="button"
            class="rounded-md bg-[var(--interaction-active)] px-3 py-2 text-sm font-medium text-white hover:bg-[var(--interaction-active-strong)] disabled:opacity-50"
            :disabled="annotationSaving"
            @click="saveSelection"
          >
            {{ annotationSaving ? $t('Saving…') : $t('Save and review') }}
          </button>
        </div>
      </section>
    </main>
  </div>
</template>

<style scoped>
@keyframes global-card-focus {
  0% {
    background-color: rgb(231 241 239 / 0.9);
    box-shadow:
      0 0 0 1px rgb(23 107 106 / 0.45),
      0 8px 22px rgb(23 107 106 / 0.12);
  }
  65% {
    background-color: rgb(248 250 252 / 0.55);
    box-shadow:
      0 0 0 1px rgb(23 107 106 / 0.22),
      0 5px 14px rgb(23 107 106 / 0.07);
  }
  100% {
    background-color: white;
    box-shadow: 0 1px 2px rgb(15 23 42 / 0.05);
  }
}

.global-report-card--focused {
  animation: global-card-focus 1.5s ease-out;
}

@media (prefers-reduced-motion: reduce) {
  .global-report-card--focused {
    animation: none;
    background-color: rgb(231 241 239 / 0.8);
    box-shadow: 0 0 0 1px rgb(23 107 106 / 0.35);
  }
}
</style>
