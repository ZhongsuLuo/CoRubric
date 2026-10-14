<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { translate as $t } from '../i18n'

defineProps<{
  src: string
  title: string
  alt: string
}>()

const emit = defineEmits<{ close: [] }>()
const closeButtonRef = ref<HTMLButtonElement | null>(null)
const imageFailed = ref(false)

const onKeydown = (event: KeyboardEvent) => {
  if (event.key === 'Escape') emit('close')
}

onMounted(() => {
  window.addEventListener('keydown', onKeydown)
  void nextTick(() => closeButtonRef.value?.focus())
})

onBeforeUnmount(() => window.removeEventListener('keydown', onKeydown))
</script>

<template>
  <Teleport to="body">
    <div
      class="fixed inset-0 z-[100] flex items-center justify-center bg-slate-950/70 p-5 backdrop-blur-sm"
      data-testid="image-evidence-preview"
      role="dialog"
      aria-modal="true"
      :aria-label="title"
      @click.self="emit('close')"
    >
      <section
        class="flex max-h-[92vh] w-full max-w-6xl flex-col overflow-hidden rounded-xl border border-white/10 bg-white shadow-2xl"
      >
        <header
          class="flex shrink-0 items-center justify-between gap-4 border-b border-slate-200 px-4 py-3"
        >
          <div class="min-w-0">
            <p class="truncate text-sm font-semibold text-slate-900">
              {{ title }}
            </p>
            <p class="mt-0.5 text-xs text-slate-500">
              {{ $t('Click outside or press Esc to close') }}
            </p>
          </div>
          <button
            ref="closeButtonRef"
            type="button"
            class="flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-xl leading-none text-slate-500 transition hover:bg-slate-100 hover:text-slate-900 focus:outline-none focus:ring-2 focus:ring-[var(--interaction-active)]"
            :aria-label="$t('Close image preview')"
            data-testid="close-image-evidence-preview"
            @click="emit('close')"
          >
            ×
          </button>
        </header>
        <div
          class="flex min-h-0 flex-1 items-center justify-center overflow-auto bg-slate-100 p-4 sm:p-6"
        >
          <img
            v-if="src && !imageFailed"
            :src="src"
            :alt="alt"
            class="block max-h-[calc(92vh-6rem)] max-w-full rounded-lg bg-white object-contain shadow-sm"
            data-testid="image-evidence-preview-image"
            @error="imageFailed = true"
          />
          <div
            v-else
            class="rounded-lg border border-dashed border-amber-300 bg-amber-50 px-5 py-4 text-sm text-amber-800"
            data-testid="image-evidence-preview-error"
          >
            {{ $t('The source image is unavailable in this report.') }}
          </div>
        </div>
      </section>
    </div>
  </Teleport>
</template>
