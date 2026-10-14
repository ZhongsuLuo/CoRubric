import { ref, watch, type Ref } from 'vue'
import { api } from '../../api/client'
import type { CohortSynthesis } from '../../types/schema'
import { translate as t } from '../../i18n'

interface CohortSynthesisOptions {
  caseId: Ref<string>
  criteriaFileName: Ref<string>
  criterionId: Ref<string | null>
  projectionHash: Ref<string | null | undefined>
  enabled?: Ref<boolean>
}

export const useCohortSynthesis = (options: CohortSynthesisOptions) => {
  const synthesis = ref<CohortSynthesis | null>(null)
  const loading = ref(false)
  const error = ref('')
  const synthesisByRequest = new Map<string, CohortSynthesis>()
  let requestSequence = 0
  const isEnabled = () => options.enabled?.value ?? true
  const requestKey = () =>
    [
      options.caseId.value,
      options.criteriaFileName.value,
      options.criterionId.value || '',
      options.projectionHash.value || '',
    ].join('\u001f')

  const load = async () => {
    const requestId = ++requestSequence
    if (!isEnabled()) {
      synthesis.value = null
      loading.value = false
      error.value = ''
      return
    }
    const key = requestKey()
    const cachedSynthesis = synthesisByRequest.get(key)
    if (cachedSynthesis) {
      synthesis.value = cachedSynthesis
      loading.value = false
      error.value = ''
      return
    }
    synthesis.value = null
    loading.value = true
    error.value = ''
    try {
      const response = await api.getCohortSynthesis(options.caseId.value, {
        criteria_file_name: options.criteriaFileName.value || undefined,
        criterion_id: options.criterionId.value || undefined,
      })
      if (requestId !== requestSequence) return
      if (
        response.data?.projection_hash &&
        options.projectionHash.value &&
        response.data.projection_hash !== options.projectionHash.value
      ) {
        return
      }
      synthesis.value = response.data || null
      if (response.data) synthesisByRequest.set(key, response.data)
    } catch (caught) {
      if (requestId !== requestSequence) return
      synthesis.value = null
      error.value = caught instanceof Error ? caught.message : t('Cohort synthesis is unavailable.')
    } finally {
      if (requestId === requestSequence) loading.value = false
    }
  }

  watch(
    [
      () => options.caseId.value,
      () => options.criteriaFileName.value,
      () => options.projectionHash.value,
      () => options.criterionId.value,
      isEnabled,
    ],
    () => void load(),
    { immediate: true, flush: 'sync' },
  )

  return { synthesis, loading, error }
}
