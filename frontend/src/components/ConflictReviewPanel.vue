<script setup lang="ts">
import { ref } from 'vue'
import { useGradingStore } from '../stores/grading'
import type {
  AlignmentConflict,
  AlignmentConflictEventRef,
  TeacherScoringPattern,
} from '../types/schema'
import { translate as t } from '../i18n'
import { formatAssessmentScore } from '../utils/reportAssessmentPresentation'

const $t = t
const props = withDefaults(
  defineProps<{
    conflicts: AlignmentConflict[]
    inline?: boolean
    reportName?: string
  }>(),
  { inline: false, reportName: '' },
)
const emit = defineEmits<{
  (event: 'select-criterion', criteriaId: string): void
}>()

const store = useGradingStore()
const actionId = ref<string | null>(null)
const actionError = ref('')
const resolutionNotes = ref<Record<string, string>>({})
const patternKinds = ['current', 'alternative'] as const

const criterionLabel = (conflict: AlignmentConflict) => {
  const criterion = store.criteriaSchema?.criteria.find(
    (item) => String(item.criteria_id) === String(conflict.criteria_id),
  )
  return criterion?.criteria_name || $t('Scoring criterion')
}

const pattern = (conflict: AlignmentConflict, kind: 'current' | 'alternative') =>
  (kind === 'current' ? conflict.current_pattern : conflict.alternative_pattern) || null

const pointScoreLabel = (item: TeacherScoringPattern) =>
  (item.scoring_point_scores || [])
    .map((point) => `${point.point_id}: ${formatAssessmentScore(point.score)}`)
    .join(' · ')

const jumpToPattern = (item: TeacherScoringPattern | null) => {
  if (!item?.event_id || !item.report_name) return
  store.jumpToConflictEvent({
    event_id: item.event_id,
    report_name: item.report_name,
    score: item.criteria_score,
    anchor_text: item.anchor_text,
    score_reason: item.score_reason,
  } as AlignmentConflictEventRef)
}

const resolve = async (conflict: AlignmentConflict, selectedPatternId: string | undefined) => {
  if (!conflict.conflict_id || !selectedPatternId || actionId.value) return
  actionId.value = conflict.conflict_id
  actionError.value = ''
  try {
    await store.resolveConflict(
      conflict.conflict_id,
      selectedPatternId,
      resolutionNotes.value[conflict.conflict_id] || '',
    )
  } catch (error) {
    actionError.value = error instanceof Error ? error.message : $t('Failed to process conflict')
  } finally {
    actionId.value = null
  }
}
</script>

<template>
  <section
    v-if="props.conflicts.length"
    class="space-y-3"
    :data-testid="props.inline ? 'inline-conflict-review' : 'conflict-review'"
  >
    <article
      v-for="conflict in props.conflicts"
      :key="conflict.conflict_id || conflict.criteria_id"
      class="rounded-xl border border-rose-200 bg-rose-50/70 p-3 text-xs text-slate-700"
      data-testid="teacher-conflict-card"
    >
      <div class="flex flex-wrap items-start justify-between gap-2">
        <div>
          <div class="font-bold text-rose-900">{{ $t('Teacher scoring conflict') }}</div>
          <button
            v-if="!props.inline"
            type="button"
            class="mt-1 font-semibold text-rose-800 underline decoration-rose-300 underline-offset-2"
            @click="emit('select-criterion', conflict.criteria_id)"
          >
            {{ criterionLabel(conflict) }}
          </button>
        </div>
        <span class="max-w-xl text-right text-[11px] leading-relaxed text-rose-700">
          {{
            conflict.reason || $t('Comparable teacher grading patterns have different outcomes.')
          }}
        </span>
      </div>

      <div class="mt-3 grid gap-3 md:grid-cols-2">
        <section
          v-for="kind in patternKinds"
          :key="kind"
          class="flex flex-col rounded-lg border border-slate-200 bg-white p-3"
          :data-testid="`conflict-pattern-${kind}`"
        >
          <div class="text-[11px] font-bold uppercase tracking-wide text-slate-500">
            {{
              kind === 'current' ? $t('Current scoring pattern') : $t('Alternative scoring pattern')
            }}
          </div>
          <template v-if="pattern(conflict, kind)">
            <button
              type="button"
              class="mt-2 self-start font-semibold text-[var(--interaction-active-strong)] underline decoration-[#9fc5c0] underline-offset-2"
              @click="jumpToPattern(pattern(conflict, kind))"
            >
              {{ pattern(conflict, kind)?.report_name }}
            </button>
            <div class="mt-2 text-2xl font-bold text-slate-900">
              {{ formatAssessmentScore(pattern(conflict, kind)?.criteria_score ?? 0) }}
              <span class="text-xs font-medium text-slate-500">{{ $t('pts') }}</span>
            </div>
            <p v-if="pattern(conflict, kind)?.score_reason" class="mt-2 leading-relaxed">
              {{ pattern(conflict, kind)?.score_reason }}
            </p>
            <p
              v-if="pattern(conflict, kind)?.anchor_text"
              class="mt-2 rounded bg-slate-50 p-2 text-[11px] text-slate-600"
            >
              “{{ pattern(conflict, kind)?.anchor_text }}”
            </p>
            <p
              v-if="pointScoreLabel(pattern(conflict, kind)!)"
              class="mt-2 text-[11px] text-slate-500"
            >
              {{ pointScoreLabel(pattern(conflict, kind)!) }}
            </p>
          </template>

          <div class="mt-auto pt-3">
            <button
              type="button"
              class="w-full rounded-md px-3 py-2 text-[11px] font-semibold text-white disabled:cursor-not-allowed disabled:opacity-50"
              :class="
                kind === 'current'
                  ? 'bg-slate-700 hover:bg-slate-800'
                  : 'bg-[var(--interaction-active)] hover:bg-[var(--interaction-active-strong)]'
              "
              :data-testid="`select-${kind}-pattern`"
              :disabled="!conflict.conflict_id || Boolean(actionId)"
              @click="resolve(conflict, pattern(conflict, kind)?.pattern_id)"
            >
              {{
                actionId === conflict.conflict_id
                  ? $t('Applying…')
                  : kind === 'current'
                    ? $t('Use current pattern')
                    : $t('Use alternative pattern')
              }}
            </button>
            <p class="mt-1.5 text-[10px] leading-relaxed text-slate-500">
              {{
                kind === 'current'
                  ? $t('Correct only the alternative score. The rubric stays unchanged.')
                  : $t('Update the rubric and regrade every report for this criterion.')
              }}
            </p>
          </div>
        </section>
      </div>

      <label
        v-if="conflict.conflict_id"
        class="mt-3 block text-[11px] font-semibold text-slate-600"
      >
        {{ $t('Resolution note (optional)') }}
        <textarea
          v-model="resolutionNotes[conflict.conflict_id]"
          rows="2"
          class="mt-1.5 w-full resize-y rounded-md border border-slate-200 bg-white px-2.5 py-2 text-xs font-normal outline-none focus:border-[var(--interaction-active)]"
        />
      </label>
      <div v-if="actionError" class="mt-2 text-[11px] text-rose-700">{{ actionError }}</div>
    </article>
  </section>
</template>
