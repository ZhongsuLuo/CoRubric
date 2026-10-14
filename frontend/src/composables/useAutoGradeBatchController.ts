import { onUnmounted, watch } from 'vue'
import { useGradingStore } from '../stores/grading'
import { shouldPollAutoGradeBatch } from '../utils/autoGradeBatch'

const POLL_INTERVAL_MS = 1500

export const useAutoGradeBatchController = () => {
  const store = useGradingStore()
  let pollTimer: number | null = null
  let refreshInFlight = false

  const hasPendingCriterionRevision = () =>
    (store.alignment.criterion_revisions || []).some(
      (revision) =>
        revision.criteria_file_name === store.activeCriteriaFile &&
        ['awaiting_audit', 'scheduling_regrade', 'regrading'].includes(revision.status),
    )

  const stopPolling = () => {
    if (pollTimer !== null) window.clearInterval(pollTimer)
    pollTimer = null
  }

  const refresh = async () => {
    const batch = store.activeAutoGradeBatch
    const revisionPending = hasPendingCriterionRevision()
    if (
      refreshInFlight ||
      (!revisionPending && (!batch || !shouldPollAutoGradeBatch(batch.status)))
    ) {
      return
    }
    refreshInFlight = true
    try {
      if (batch && shouldPollAutoGradeBatch(batch.status)) {
        await store.refreshAutoGradeBatch(batch.batch_id)
      } else {
        // A rubric-revision worker may still be auditing before its batch
        // exists. Refresh alignment until that batch becomes visible, then the
        // normal batch-progress polling path takes over.
        await store.loadAlignment(store.activeCaseId)
      }
    } catch (error) {
      console.warn('[auto-grade] failed to refresh batch progress', error)
    } finally {
      refreshInFlight = false
    }
  }

  const syncPolling = () => {
    stopPolling()
    const batch = store.activeAutoGradeBatch
    const revisionPending = hasPendingCriterionRevision()
    if (!revisionPending && (!batch || !shouldPollAutoGradeBatch(batch.status))) {
      return
    }
    void refresh()
    pollTimer = window.setInterval(() => void refresh(), POLL_INTERVAL_MS)
  }

  watch(
    () =>
      [
        `${store.activeAutoGradeBatch?.batch_id || ''}:${store.activeAutoGradeBatch?.status || ''}`,
        ...(store.alignment.criterion_revisions || []).map(
          (revision) => `${revision.revision_id}:${revision.status}`,
        ),
      ].join('|'),
    syncPolling,
    { immediate: true },
  )
  onUnmounted(stopPolling)
}
