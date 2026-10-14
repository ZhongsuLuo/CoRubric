<script setup lang="ts">
import { computed, ref } from 'vue'
import type { EvidenceAnchor, GlobalScorePoint } from '../../types/schema'
import { evidenceAnchorsWithLegacyImages, resolveReportImageUrl } from '../../utils/imageEvidence'
import ImageEvidencePreviewModal from '../ImageEvidencePreviewModal.vue'
import { translate as $t } from '../../i18n'

const props = defineProps<{
  reportName: string
  point: GlobalScorePoint | null
  markdown: string
}>()

const apiBase = import.meta.env.VITE_API_BASE || '/api'
const preview = ref<{ src: string; alt: string; title: string } | null>(null)

const hasStructuredEvidence = computed(() => Boolean(props.point?.evidence_anchors?.length))
const displayAnchors = computed(() =>
  evidenceAnchorsWithLegacyImages(props.point?.evidence_anchors, props.point?.anchor_text).filter(
    (anchor) => anchor.anchor_type !== 'absence',
  ),
)
const textSnippets = computed(() => {
  const snippets = displayAnchors.value
    .filter((anchor) => anchor.anchor_type !== 'image')
    .map((anchor) => anchor.text?.trim() || '')
    .filter(Boolean)
  if (snippets.length || displayAnchors.value.length) return snippets
  return !hasStructuredEvidence.value && props.point?.anchor_text?.trim()
    ? [props.point.anchor_text.trim()]
    : []
})
const imageAnchors = computed(() =>
  displayAnchors.value.filter((anchor) => anchor.anchor_type === 'image'),
)
const missingEvidence = computed(() =>
  (props.point?.evidence_anchors || [])
    .filter((anchor) => anchor.anchor_type === 'absence')
    .map((anchor) => anchor.missing_item || anchor.description || 'required evidence')
    .filter(Boolean),
)

const openImagePreview = (anchor: EvidenceAnchor, index: number) => {
  preview.value = {
    src: resolveReportImageUrl(props.markdown, anchor.image_src, apiBase),
    alt: anchor.image_alt || anchor.description || `${$t('Image evidence')} ${index + 1}`,
    title: `${props.reportName} · ${$t('Image evidence')} ${index + 1}`,
  }
}
</script>

<template>
  <div class="min-w-0 border-l border-slate-200 pl-4">
    <div
      v-if="textSnippets.length || imageAnchors.length"
      class="space-y-1.5 text-[10px] leading-[1.5] text-slate-600"
      data-testid="global-evidence-text"
    >
      <div
        v-for="(snippet, index) in textSnippets"
        :key="`${index}-${snippet}`"
        class="grid grid-cols-[16px_minmax(0,1fr)] gap-1.5"
        data-testid="global-highlighted-snippet"
      >
        <span
          class="mt-0.5 flex h-4 w-4 items-center justify-center rounded bg-emerald-50 text-[8px] font-semibold text-emerald-700"
          aria-hidden="true"
        >
          {{ index + 1 }}
        </span>
        <q class="break-words">{{ snippet }}</q>
      </div>
      <button
        v-for="(anchor, index) in imageAnchors"
        :key="`image-${index}-${anchor.image_src}`"
        type="button"
        class="interaction-text flex w-full items-center gap-2 rounded-md border border-[#b8d1cd] bg-[var(--interaction-active-soft)] px-2 py-1.5 text-left text-[10px] font-semibold transition hover:border-[var(--interaction-active)] focus:outline-none focus:ring-2 focus:ring-[#176b6a]/30"
        :aria-label="
          $t('View image evidence {index} in {reportName}', {
            index: index + 1,
            reportName,
          })
        "
        data-testid="global-image-evidence-link"
        @click.stop="openImagePreview(anchor, index)"
      >
        <span aria-hidden="true">▧</span>
        <span>{{ $t('Image evidence') }} {{ index + 1 }} · {{ $t('Preview image') }}</span>
      </button>
    </div>
    <div v-else data-testid="global-evidence-text">
      <p
        v-if="missingEvidence.length"
        class="mb-1.5 text-[10px] font-medium text-slate-500"
        data-testid="global-no-matching-evidence"
      >
        {{ $t('No matching evidence found.') }}
      </p>
      <p
        v-for="item in missingEvidence"
        :key="item"
        class="rounded border border-dashed border-[#d4aa78] bg-[var(--loss-low)] px-2 py-1.5 text-[10px] leading-4 text-[#76521c]"
        data-testid="global-missing-evidence"
      >
        <span class="font-semibold">{{ $t('Missing expected content:') }}</span>
        {{ item }}
      </p>
      <p v-if="!missingEvidence.length" class="text-[10px] leading-4 text-slate-400">
        {{ $t('No highlighted text recorded.') }}
      </p>
    </div>
    <div
      v-if="(textSnippets.length || imageAnchors.length) && missingEvidence.length"
      class="mt-1.5"
    >
      <p
        v-for="item in missingEvidence"
        :key="item"
        class="rounded border border-dashed border-[#d4aa78] bg-[var(--loss-low)] px-2 py-1.5 text-[10px] leading-4 text-[#76521c]"
        data-testid="global-missing-evidence"
      >
        <span class="font-semibold">{{ $t('Missing expected content:') }}</span>
        {{ item }}
      </p>
    </div>
  </div>

  <ImageEvidencePreviewModal
    v-if="preview"
    :src="preview.src"
    :title="preview.title"
    :alt="preview.alt"
    @close="preview = null"
  />
</template>
