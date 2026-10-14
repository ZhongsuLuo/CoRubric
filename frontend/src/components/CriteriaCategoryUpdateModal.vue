<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { translate as t } from '../i18n'
import type { CriteriaCategoryPreview } from '../types/schema'

const props = defineProps<{
  preview: CriteriaCategoryPreview
  saving?: boolean
  error?: string
}>()

const emit = defineEmits<{
  close: []
  confirm: [assignments: Array<{ criteria_id: string; section: string }>]
}>()

type EditableCategory = { id: string; name: string }
type EditableAssignment = {
  criteriaId: string
  criteriaName: string
  oldSection: string
  categoryId: string
}

const categories = ref<EditableCategory[]>([])
const assignments = ref<EditableAssignment[]>([])
const localError = ref('')

const resetDraft = () => {
  const categoryIdByName = new Map<string, string>()
  categories.value = []
  assignments.value = props.preview.assignments.map((assignment) => {
    let categoryId = categoryIdByName.get(assignment.section)
    if (!categoryId) {
      categoryId = `category-${categoryIdByName.size + 1}`
      categoryIdByName.set(assignment.section, categoryId)
      categories.value.push({ id: categoryId, name: assignment.section })
    }
    return {
      criteriaId: assignment.criteria_id,
      criteriaName: assignment.criteria_name,
      oldSection: assignment.old_section || '',
      categoryId,
    }
  })
  localError.value = ''
}

watch(() => props.preview, resetDraft, { immediate: true })

const categoryCounts = computed(() => {
  const counts = new Map(categories.value.map((category) => [category.id, 0]))
  assignments.value.forEach((assignment) => {
    counts.set(assignment.categoryId, (counts.get(assignment.categoryId) || 0) + 1)
  })
  return counts
})

const singletonCount = computed(
  () => [...categoryCounts.value.values()].filter((count) => count === 1).length,
)

const addCategory = () => {
  const id = `category-${Date.now()}`
  categories.value.push({ id, name: t('New category') })
}

const removeCategory = (categoryId: string) => {
  if ((categoryCounts.value.get(categoryId) || 0) > 0) return
  categories.value = categories.value.filter((category) => category.id !== categoryId)
}

const confirm = () => {
  const names = new Map<string, string>()
  for (const category of categories.value) {
    const name = category.name.trim()
    if (!name) {
      localError.value = t('Every category needs a name')
      return
    }
    names.set(category.id, name)
  }
  const normalized = assignments.value.map((assignment) => ({
    criteria_id: assignment.criteriaId,
    section: names.get(assignment.categoryId) || '',
  }))
  if (normalized.some((assignment) => !assignment.section)) {
    localError.value = t('Every criterion needs a category')
    return
  }
  localError.value = ''
  emit('confirm', normalized)
}
</script>

<template>
  <div
    class="fixed inset-0 z-[70] flex items-center justify-center bg-slate-900/50 px-4 backdrop-blur-sm"
    data-testid="criteria-category-modal"
    @click.self="!saving && emit('close')"
  >
    <div
      class="flex max-h-[90vh] w-full max-w-4xl flex-col overflow-hidden rounded-2xl bg-white shadow-2xl"
    >
      <header class="flex items-start justify-between gap-4 border-b border-slate-200 px-6 py-5">
        <div>
          <h2 class="text-lg font-bold text-slate-800">{{ t('Update criteria categories') }}</h2>
          <p class="mt-1 text-sm text-slate-500">
            {{ t('Review the complete rubric organization before applying it.') }}
          </p>
        </div>
        <button
          type="button"
          class="rounded-lg p-1.5 text-slate-400 hover:bg-slate-100 hover:text-slate-700 disabled:opacity-40"
          :disabled="saving"
          :aria-label="t('Close')"
          @click="emit('close')"
        >
          <span aria-hidden="true">✕</span>
        </button>
      </header>

      <div class="min-h-0 flex-1 space-y-5 overflow-y-auto px-6 py-5">
        <p v-if="preview.rationale" class="rounded-xl bg-slate-50 px-4 py-3 text-sm text-slate-600">
          {{ preview.rationale }}
        </p>

        <section class="space-y-2">
          <div class="flex items-center justify-between gap-3">
            <h3 class="text-sm font-semibold text-slate-700">{{ t('Category names') }}</h3>
            <button
              type="button"
              class="text-sm font-medium text-[var(--interaction-active)] hover:underline"
              @click="addCategory"
            >
              + {{ t('Add category') }}
            </button>
          </div>
          <div class="grid gap-2 sm:grid-cols-2">
            <div
              v-for="category in categories"
              :key="category.id"
              class="flex items-center gap-2 rounded-lg border border-slate-200 p-2"
            >
              <input
                v-model="category.name"
                class="min-w-0 flex-1 rounded-md border border-transparent px-2 py-1.5 text-sm font-medium text-slate-700 focus:border-[var(--interaction-active)] focus:outline-none"
                :aria-label="t('Category name')"
              />
              <span class="rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-500">
                {{ categoryCounts.get(category.id) || 0 }}
              </span>
              <button
                v-if="(categoryCounts.get(category.id) || 0) === 0"
                type="button"
                class="text-xs text-rose-500 hover:underline"
                @click="removeCategory(category.id)"
              >
                {{ t('Remove') }}
              </button>
            </div>
          </div>
          <p v-if="singletonCount" class="text-xs text-amber-700">
            {{
              t(
                '{count} category has only one criterion; keep it only when semantically necessary.',
                { count: singletonCount },
              )
            }}
          </p>
        </section>

        <section class="overflow-hidden rounded-xl border border-slate-200">
          <div
            class="grid grid-cols-[minmax(0,1.1fr)_minmax(0,0.9fr)_minmax(0,0.9fr)] gap-3 bg-slate-50 px-4 py-2 text-xs font-semibold uppercase tracking-wide text-slate-500"
          >
            <span>{{ t('Criterion') }}</span>
            <span>{{ t('Current category') }}</span>
            <span>{{ t('Proposed category') }}</span>
          </div>
          <div
            v-for="assignment in assignments"
            :key="assignment.criteriaId"
            class="grid grid-cols-[minmax(0,1.1fr)_minmax(0,0.9fr)_minmax(0,0.9fr)] items-center gap-3 border-t border-slate-100 px-4 py-3 text-sm"
          >
            <span class="min-w-0 font-medium text-slate-700">
              {{ assignment.criteriaId }} · {{ assignment.criteriaName }}
            </span>
            <span class="min-w-0 text-slate-500">
              {{ assignment.oldSection || t('Category pending update') }}
            </span>
            <select
              v-model="assignment.categoryId"
              class="min-w-0 rounded-lg border border-slate-200 bg-white px-2 py-1.5 text-slate-700 focus:border-[var(--interaction-active)] focus:outline-none"
              :aria-label="t('Proposed category')"
            >
              <option v-for="category in categories" :key="category.id" :value="category.id">
                {{ category.name || t('Unnamed category') }}
              </option>
            </select>
          </div>
        </section>

        <p v-if="localError || error" class="text-sm font-medium text-rose-600">
          {{ localError || error }}
        </p>
      </div>

      <footer class="flex justify-end gap-3 border-t border-slate-200 bg-slate-50 px-6 py-4">
        <button
          type="button"
          class="rounded-lg border border-slate-200 bg-white px-4 py-2 text-sm font-medium text-slate-600 hover:bg-slate-100 disabled:opacity-40"
          :disabled="saving"
          @click="emit('close')"
        >
          {{ t('Cancel') }}
        </button>
        <button
          type="button"
          class="rounded-lg bg-[var(--interaction-active)] px-4 py-2 text-sm font-semibold text-white shadow-sm hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
          :disabled="saving"
          data-testid="apply-criteria-categories"
          @click="confirm"
        >
          {{ saving ? t('Applying categories…') : t('Apply categories') }}
        </button>
      </footer>
    </div>
  </div>
</template>
