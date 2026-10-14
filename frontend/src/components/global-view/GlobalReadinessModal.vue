<script setup lang="ts">
import type { GlobalReadinessBlocker } from '../../types/schema'
import { translate as $t } from '../../i18n'

defineProps<{ blockers: GlobalReadinessBlocker[] }>()

const emit = defineEmits<{
  close: []
  select: [blocker: GlobalReadinessBlocker]
}>()
</script>

<template>
  <div
    class="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/45 px-6 backdrop-blur-sm"
    data-testid="global-readiness-modal"
    role="dialog"
    aria-modal="true"
    aria-labelledby="global-readiness-title"
  >
    <section class="max-h-[78vh] w-full max-w-2xl overflow-hidden rounded-2xl bg-white shadow-2xl">
      <header class="border-b border-slate-200 px-6 py-5">
        <p class="text-[11px] font-semibold uppercase tracking-[0.16em] text-amber-600">
          {{ $t('Resolve blocking changes') }}
        </p>
        <h2 id="global-readiness-title" class="mt-1 text-lg font-semibold text-slate-900">
          {{ $t('Global View is temporarily unavailable') }}
        </h2>
        <p class="mt-1 text-sm text-slate-500">
          {{
            $t('Resolve grading conflicts and confirm scoring-standard updates before continuing.')
          }}
        </p>
      </header>
      <div class="max-h-[52vh] space-y-2 overflow-y-auto px-6 py-4">
        <button
          v-for="(blocker, index) in blockers"
          :key="`${blocker.kind}-${blocker.report_name || ''}-${blocker.criterion_id || ''}-${index}`"
          type="button"
          class="flex w-full items-center justify-between gap-4 rounded-xl border border-slate-200 px-4 py-3 text-left hover:border-[var(--interaction-active)] hover:bg-[var(--interaction-active-soft)]"
          @click="emit('select', blocker)"
        >
          <span class="min-w-0">
            <span class="block text-sm font-semibold text-slate-800">{{ $t(blocker.label) }}</span>
            <span class="mt-0.5 block truncate text-xs text-slate-500">
              {{ blocker.report_name || $t('Rubric task') }}
            </span>
          </span>
          <span class="interaction-text shrink-0 text-xs font-semibold">
            {{ $t('Go to confirmation →') }}
          </span>
        </button>
      </div>
      <footer class="flex justify-end border-t border-slate-200 bg-slate-50 px-6 py-4">
        <button
          type="button"
          class="rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-medium text-slate-700"
          @click="emit('close')"
        >
          {{ $t('Stay in Report View') }}
        </button>
      </footer>
    </section>
  </div>
</template>
