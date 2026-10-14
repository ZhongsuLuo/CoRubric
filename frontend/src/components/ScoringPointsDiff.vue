<script setup lang="ts">
import { computed } from 'vue'
import type { ScoringPoint } from '../types/schema'
import {
  diffScoringPoints,
  scoringPointDeductionCapacity,
  type StructuredChangeKind,
} from '../utils/scoringPoints'
import { translate as $t } from '../i18n'

const props = withDefaults(
  defineProps<{
    before?: ScoringPoint[] | null
    after?: ScoringPoint[] | null
    emptyText?: string
  }>(),
  { emptyText: '' },
)

const changes = computed(() => diffScoringPoints(props.before, props.after))
const label = (kind: StructuredChangeKind) =>
  ({ added: $t('Added'), removed: $t('Removed'), modified: $t('Updated') })[kind]
const tone = (kind: StructuredChangeKind) =>
  ({
    added: 'border-emerald-200 bg-emerald-50/60',
    removed: 'border-rose-200 bg-rose-50/60',
    modified: 'border-amber-200 bg-amber-50/60',
  })[kind]
const badgeTone = (kind: StructuredChangeKind) =>
  ({
    added: 'bg-emerald-100 text-emerald-800',
    removed: 'bg-rose-100 text-rose-800',
    modified: 'bg-amber-100 text-amber-800',
  })[kind]
const value = (item: unknown) =>
  item === null || item === undefined || item === '' ? '—' : String(item)
const pointLabel = (point?: ScoringPoint) => point?.summary?.trim() || $t('Scoring point')
const pointSnapshot = (point?: ScoringPoint) =>
  point
    ? [
        { label: $t('Scoring point content'), value: value(point.summary) },
        {
          label: $t('Score'),
          value: $t('{score} points', { score: scoringPointDeductionCapacity(point) }),
        },
      ]
    : []
</script>

<template>
  <section class="rounded-lg border border-slate-200 bg-slate-50 p-3">
    <div class="text-xs font-semibold uppercase tracking-wide text-slate-600">
      {{ $t('Scoring point changes') }}
    </div>
    <p v-if="!changes.length" class="mt-2 text-xs text-slate-500">
      {{ emptyText || $t('No scoring point changes.') }}
    </p>
    <ol v-else class="mt-3 space-y-3">
      <li
        v-for="change in changes"
        :key="change.pointId"
        class="rounded-xl border p-3 text-xs text-slate-700"
        :class="tone(change.kind)"
        data-testid="scoring-point-change"
      >
        <div class="flex items-center gap-2">
          <span class="font-bold text-slate-900">{{
            pointLabel(change.after || change.before)
          }}</span>
          <span
            class="rounded px-1.5 py-0.5 text-[10px] font-semibold"
            :class="badgeTone(change.kind)"
            >{{ label(change.kind) }}</span
          >
        </div>

        <div
          v-if="change.kind !== 'modified'"
          class="mt-3 grid items-stretch gap-2 md:grid-cols-[1fr_auto_1fr]"
          data-testid="before-after-pair"
        >
          <div class="rounded-lg border border-slate-200 bg-white p-3">
            <div class="mb-2 font-semibold text-slate-500">{{ $t('Before') }}</div>
            <div v-if="change.before" class="space-y-1.5">
              <div v-for="item in pointSnapshot(change.before)" :key="item.label">
                <span class="font-semibold text-slate-600">{{ item.label }}: </span>{{ item.value }}
              </div>
            </div>
            <div v-else class="text-slate-400">{{ $t('None (new)') }}</div>
          </div>
          <div class="hidden self-center text-lg text-slate-400 md:block" aria-hidden="true">→</div>
          <div class="rounded-lg border border-[#b7d2ce] bg-[var(--interaction-active-soft)] p-3">
            <div class="mb-2 font-semibold text-[var(--interaction-active)]">{{ $t('After') }}</div>
            <div v-if="change.after" class="space-y-1.5 text-[var(--interaction-active-strong)]">
              <div v-for="item in pointSnapshot(change.after)" :key="item.label">
                <span class="font-semibold">{{ item.label }}: </span>{{ item.value }}
              </div>
            </div>
            <div v-else class="text-[var(--interaction-active)]">{{ $t('None (removed)') }}</div>
          </div>
        </div>

        <div v-else class="mt-3 space-y-3">
          <div v-for="field in change.fields" :key="field.field">
            <div class="mb-1.5 font-semibold text-slate-700">{{ $t(field.label) }}</div>
            <div
              class="grid items-stretch gap-2 md:grid-cols-[1fr_auto_1fr]"
              data-testid="before-after-pair"
            >
              <div class="rounded-lg border border-slate-200 bg-white p-2.5">
                <div class="mb-1 text-[10px] font-semibold text-slate-500">{{ $t('Before') }}</div>
                <div class="whitespace-pre-wrap">{{ value(field.before) }}</div>
              </div>
              <div class="hidden self-center text-lg text-slate-400 md:block" aria-hidden="true">
                →
              </div>
              <div
                class="rounded-lg border border-[#b7d2ce] bg-[var(--interaction-active-soft)] p-2.5"
              >
                <div class="mb-1 text-[10px] font-semibold text-[var(--interaction-active)]">
                  {{ $t('After') }}
                </div>
                <div class="whitespace-pre-wrap text-[var(--interaction-active-strong)]">
                  {{ value(field.after) }}
                </div>
              </div>
            </div>
          </div>
        </div>
      </li>
    </ol>
  </section>
</template>
