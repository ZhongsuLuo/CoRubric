<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import ReportView from './components/ReportView.vue'
import CaseFolderImport from './components/CaseFolderImport.vue'
import CriteriaView from './components/CriteriaView.vue'
import GlobalView from './components/global-view/GlobalView.vue'
import GlobalReadinessModal from './components/global-view/GlobalReadinessModal.vue'
import EvidenceRelationOverlay from './components/report-view/EvidenceRelationOverlay.vue'
import { useGlobalEntryGuard } from './composables/useGlobalEntryGuard'
import { useAutoGradeBatchController } from './composables/useAutoGradeBatchController'
import { useGradingStore } from './stores/grading'
import type { CriteriaMode, GlobalReadinessBlocker } from './types/schema'
import { useI18n } from './i18n'

const store = useGradingStore()
const { locale, setLocale, t } = useI18n()
const loadedCaseId = ref('')
const loadingCaseId = ref('')
const readinessModalOpen = ref(false)
const isProduction = import.meta.env.PROD
const resettingExperiment = ref(false)
const experimentResetMessage = ref('')
const { blockers: globalEntryBlockers, blockerSignature } = useGlobalEntryGuard()
useAutoGradeBatchController()

const experimentResetAvailable = computed(() => store.isExperimentResetAvailable())

const useImportedCase = async (caseId: string) => {
  await store.loadCases()
  loadedCaseId.value = ''
  store.activeCaseId = caseId
  await loadSelectedCase(caseId)
}

const showGlobalReadinessBlockers = () => {
  if (store.mode === 'global') {
    store.resetGlobalCriterionDrilldown()
    store.toggleMode('single')
  }
  readinessModalOpen.value = true
}

const loadSelectedCase = async (caseId: string) => {
  if (!caseId || loadedCaseId.value === caseId || loadingCaseId.value === caseId) return
  loadingCaseId.value = caseId
  try {
    await store.loadCase(caseId)
    loadedCaseId.value = caseId
    if (store.mode === 'global' && globalEntryBlockers.value.length) {
      showGlobalReadinessBlockers()
    }
  } finally {
    if (loadingCaseId.value === caseId) {
      loadingCaseId.value = ''
    }
  }
}

onMounted(async () => {
  await store.loadCases()
  if (store.activeCaseId && store.cases.length) {
    await loadSelectedCase(store.activeCaseId)
  }
})

watch(
  () => store.activeCaseId,
  async (next) => {
    if (next) {
      await loadSelectedCase(next)
    }
  },
)

watch(blockerSignature, () => {
  if (globalEntryBlockers.value.length && store.mode === 'global') {
    showGlobalReadinessBlockers()
  }
})

const requestMode = (next: CriteriaMode) => {
  if (next === 'single') {
    store.toggleMode('single')
    return
  }
  if (globalEntryBlockers.value.length) {
    showGlobalReadinessBlockers()
    return
  }
  store.toggleMode('global')
}

const goToBlocker = (blocker: GlobalReadinessBlocker) => {
  readinessModalOpen.value = false
  store.toggleMode('single')
  if (blocker.report_name) store.setActiveReport(blocker.report_name)
  if (blocker.criterion_id) store.setActiveCriteria(blocker.criterion_id)
  if (blocker.implicit_grade_id) store.setActiveImplicitGrade(blocker.implicit_grade_id)
  if (blocker.proposal_id) store.setActiveProposal(blocker.proposal_id)
  if (blocker.event_id) store.setActiveHighlight(`event-${blocker.event_id}`)
}

const resetCurrentExperiment = async () => {
  const caseId = store.activeCaseId
  if (
    !caseId ||
    !experimentResetAvailable.value ||
    resettingExperiment.value ||
    store.autoGradeBatchStarted
  )
    return
  const confirmed = window.confirm(
    t(
      'Reset experiment "{caseId}" to its configured baseline? The rubric and grading state will be replaced with this case\'s saved starting point. Source reports will be kept. This cannot be undone.',
      { caseId },
    ),
  )
  if (!confirmed) return

  resettingExperiment.value = true
  experimentResetMessage.value = ''
  try {
    await store.resetCurrentExperiment()
    loadedCaseId.value = caseId
    experimentResetMessage.value = t('Reset {caseId} to its experiment baseline.', { caseId })
  } catch (error) {
    experimentResetMessage.value =
      error instanceof Error
        ? t('Could not reset experiment: {message}', { message: error.message })
        : t('Could not reset experiment.')
  } finally {
    resettingExperiment.value = false
  }
}
</script>

<template>
  <div class="flex h-full w-full flex-col overflow-hidden bg-gray-50 font-sans text-slate-800">
    <div
      class="z-10 border-b border-slate-200 bg-white/80 backdrop-blur-sm px-6 py-3 shadow-sm transition-all"
    >
      <div class="flex w-full items-center justify-between gap-6">
        <div class="flex items-center gap-3">
          <img src="/corubric-icon.svg" alt="" aria-hidden="true" class="h-9 w-9 object-contain" />
          <span class="text-lg font-semibold tracking-tight text-slate-800"> CoRubric </span>
        </div>
        <div class="flex items-center gap-2">
          <div
            class="flex items-center rounded-lg border border-slate-200 bg-slate-50 p-1"
            data-testid="page-view-toggle"
            :aria-label="t('Page view')"
          >
            <button
              type="button"
              class="rounded-md px-3 py-1.5 text-xs font-semibold transition"
              :class="
                store.mode === 'single'
                  ? 'bg-white text-slate-900 shadow-sm'
                  : 'text-slate-500 hover:text-slate-800'
              "
              @click="requestMode('single')"
            >
              {{ t('Report View') }}
            </button>
            <button
              type="button"
              class="rounded-md px-3 py-1.5 text-xs font-semibold transition"
              :class="
                store.mode === 'global'
                  ? 'bg-white text-[var(--interaction-active)] shadow-sm'
                  : 'text-slate-500 hover:text-slate-800'
              "
              @click="requestMode('global')"
            >
              {{ t('Global View') }}
            </button>
          </div>
          <div
            class="flex items-center rounded-lg border border-slate-200 bg-slate-50 p-1"
            data-testid="language-toggle"
            :aria-label="t('Interface language')"
          >
            <button
              type="button"
              class="rounded-md px-2 py-1.5 text-xs font-semibold transition"
              :class="
                locale === 'zh'
                  ? 'bg-white text-[var(--interaction-active)] shadow-sm'
                  : 'text-slate-500 hover:text-slate-800'
              "
              @click="setLocale('zh')"
            >
              中
            </button>
            <button
              type="button"
              class="rounded-md px-2 py-1.5 text-xs font-semibold transition"
              :class="
                locale === 'en'
                  ? 'bg-white text-[var(--interaction-active)] shadow-sm'
                  : 'text-slate-500 hover:text-slate-800'
              "
              @click="setLocale('en')"
            >
              EN
            </button>
          </div>
        </div>
        <div class="flex items-center gap-3 text-sm">
          <span class="text-slate-500 font-medium">{{ t('Current Case') }}</span>
          <CaseFolderImport :disabled="Boolean(loadingCaseId)" @use-case="useImportedCase" />
          <div class="relative">
            <select
              v-model="store.activeCaseId"
              :disabled="Boolean(loadingCaseId)"
              data-testid="case-select"
              class="appearance-none rounded-lg border border-slate-200 bg-white py-1.5 pl-3 pr-8 text-sm font-medium text-slate-700 shadow-sm transition-colors hover:border-slate-300 focus:border-[var(--interaction-active)] focus:outline-none focus:ring-1 focus:ring-[var(--interaction-active)]"
            >
              <option v-for="item in store.cases" :key="item" :value="item">
                {{ item }}
              </option>
            </select>
            <div
              class="pointer-events-none absolute inset-y-0 right-0 flex items-center px-2 text-slate-500"
            >
              <svg class="h-4 w-4 fill-current" viewBox="0 0 20 20">
                <path
                  d="M5.293 7.293a1 1 0 011.414 0L10 10.586l3.293-3.293a1 1 0 111.414 1.414l-4 4a1 1 0 01-1.414 0l-4-4a1 1 0 010-1.414z"
                />
              </svg>
            </div>
          </div>
          <button
            v-if="!isProduction && experimentResetAvailable"
            type="button"
            data-testid="debug-reset-experiment"
            class="rounded-lg border border-amber-300 bg-amber-50 px-3 py-1.5 text-xs font-semibold text-amber-800 shadow-sm transition hover:bg-amber-100 disabled:cursor-not-allowed disabled:opacity-50"
            :disabled="Boolean(loadingCaseId) || resettingExperiment || store.autoGradeBatchStarted"
            :title="t('Restore this case’s configured starting point')"
            @click="resetCurrentExperiment"
          >
            {{ resettingExperiment ? t('Resetting…') : t('Reset experiment') }}
          </button>
          <span
            v-if="experimentResetMessage"
            class="max-w-64 truncate text-xs text-slate-500"
            data-testid="experiment-reset-message"
            :title="experimentResetMessage"
          >
            {{ experimentResetMessage }}
          </span>
        </div>
      </div>
    </div>

    <div class="flex-1 min-h-0 px-4 pb-4 pt-2 sm:px-6 sm:pb-6 sm:pt-3">
      <div
        v-show="store.mode === 'single'"
        class="relative isolate grid h-full w-full grid-cols-[minmax(0,2fr)_minmax(0,3fr)] gap-4 bg-white"
        data-testid="report-view-layout"
      >
        <section class="flex min-h-0 flex-col overflow-hidden">
          <ReportView :suspended="store.mode !== 'single'" />
        </section>
        <section class="flex min-h-0 flex-col overflow-hidden">
          <CriteriaView />
        </section>
        <EvidenceRelationOverlay v-if="store.mode === 'single'" />
      </div>
      <div
        v-if="store.mode === 'global'"
        class="h-full min-h-0 overflow-hidden border border-slate-200 bg-white shadow-[0_1px_3px_rgba(15,23,42,0.05)]"
      >
        <GlobalView />
      </div>
    </div>

    <GlobalReadinessModal
      v-if="readinessModalOpen"
      :blockers="globalEntryBlockers"
      @close="readinessModalOpen = false"
      @select="goToBlocker"
    />
  </div>
</template>
