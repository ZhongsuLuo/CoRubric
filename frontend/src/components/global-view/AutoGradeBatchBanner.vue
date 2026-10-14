<script setup lang="ts">
import { computed } from 'vue'
import type { AutoGradeBatch } from '../../types/schema'
import { autoGradeBatchStatusLabel, isTerminalAutoGradeBatch } from '../../utils/autoGradeBatch'
import { translate as $t } from '../../i18n'

const props = defineProps<{
  batch: AutoGradeBatch
  actionPending: boolean
  error: string
}>()

const emit = defineEmits<{
  confirm: []
  cancel: []
}>()

const isTerminal = computed(() => isTerminalAutoGradeBatch(props.batch.status))
const statusLabel = computed(() => autoGradeBatchStatusLabel(props.batch.status))
</script>

<template>
  <section
    class="flex items-center justify-between gap-5 border-b border-[#cfdedb] bg-[#eef5f3] px-5 py-3 text-xs"
    data-testid="global-auto-grade-batch-banner"
  >
    <div class="min-w-0 flex-1">
      <div class="flex items-center gap-3">
        <span class="font-semibold text-[var(--text-primary)]">{{ statusLabel }}</span>
        <span class="interaction-text tabular-nums">
          {{ batch.progress.completed }} / {{ batch.progress.total }}
          {{ $t('targets') }}
        </span>
        <span v-if="batch.progress.cancelled" class="text-slate-500">
          · {{ batch.progress.cancelled }} {{ $t('cancelled') }}
        </span>
      </div>
      <div class="mt-1 h-1.5 overflow-hidden rounded-full bg-white">
        <div
          class="h-full rounded-full bg-[var(--status-running)] transition-all duration-500"
          :style="{ width: `${batch.progress.percent}%` }"
          data-testid="global-auto-grade-progress-bar"
        />
      </div>
      <p v-if="error" class="mt-1.5 text-rose-700" role="alert">{{ error }}</p>
    </div>
    <div class="flex shrink-0 items-center gap-2">
      <button
        v-if="batch.status === 'awaiting_calibration'"
        type="button"
        class="rounded-md bg-[var(--interaction-active)] px-3 py-1.5 font-semibold text-white hover:bg-[var(--interaction-active-strong)] disabled:opacity-50"
        data-testid="global-confirm-auto-grade-batch"
        :disabled="actionPending"
        @click="emit('confirm')"
      >
        {{ actionPending ? $t('Starting…') : $t('Confirm rubric and start') }}
      </button>
      <button
        v-if="!isTerminal && batch.status !== 'cancelling'"
        type="button"
        class="rounded-md border border-rose-200 bg-white px-3 py-1.5 font-semibold text-rose-700 hover:bg-rose-50 disabled:opacity-50"
        data-testid="global-cancel-auto-grade-batch"
        :disabled="actionPending"
        @click="emit('cancel')"
      >
        {{ $t('Stop auto-grading') }}
      </button>
    </div>
  </section>
</template>
