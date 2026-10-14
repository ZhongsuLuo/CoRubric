<script setup lang="ts">
import { translate as t } from '../i18n'
const $t = t
defineProps<{ before: string; after: string; visible: boolean }>()
const emit = defineEmits<{ (e: 'close'): void }>()
</script>

<template>
  <div v-if="visible" class="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4">
    <div class="w-full max-w-2xl rounded-lg bg-white p-4 shadow-xl">
      <div class="mb-3 flex items-center justify-between">
        <h3 class="text-sm font-semibold text-slate-800">{{ $t('Rubric Update') }}</h3>
        <button class="text-xs text-slate-500" @click="emit('close')">{{ $t('Close') }}</button>
      </div>
      <div class="grid gap-3 text-xs md:grid-cols-2">
        <div class="rounded border border-slate-200 bg-slate-50 p-3">
          <div class="mb-1 font-semibold text-slate-600">{{ $t('Before') }}</div>
          <p class="whitespace-pre-wrap text-slate-700">{{ before }}</p>
        </div>
        <div class="rounded border border-emerald-200 bg-emerald-50 p-3">
          <div class="mb-1 font-semibold text-emerald-700">{{ $t('After') }}</div>
          <p class="whitespace-pre-wrap text-emerald-800">{{ after }}</p>
        </div>
      </div>
      <div class="mt-4 flex justify-end">
        <button class="rounded bg-emerald-600 px-3 py-1 text-xs text-white" @click="emit('close')">
          Confirm
        </button>
      </div>
    </div>
  </div>
</template>
