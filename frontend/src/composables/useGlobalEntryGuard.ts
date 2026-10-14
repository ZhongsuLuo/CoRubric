import { computed } from 'vue'
import { useGradingStore } from '../stores/grading'
import { translate as t } from '../i18n'
import type { GlobalReadinessBlocker } from '../types/schema'

const BLOCKING_KINDS = new Set(['conflict', 'rubric_proposal', 'repair_required'])

export const useGlobalEntryGuard = () => {
  const store = useGradingStore()

  const blockers = computed<GlobalReadinessBlocker[]>(() => {
    const declared = (store.alignment.global_readiness?.blockers || []).filter((blocker) =>
      BLOCKING_KINDS.has(blocker.kind),
    )
    const declaredConflictCriteria = new Set(
      declared
        .filter((blocker) => blocker.kind === 'conflict')
        .map((blocker) => String(blocker.criterion_id || '')),
    )
    const declaredProposalIds = new Set(
      declared
        .filter((blocker) => blocker.kind === 'rubric_proposal')
        .map((blocker) => String(blocker.proposal_id || '')),
    )
    const conflicts: GlobalReadinessBlocker[] = store.openConflicts
      .filter((conflict) => !declaredConflictCriteria.has(String(conflict.criteria_id)))
      .map((conflict) => {
        const ref = conflict.suspect_events?.[0] || conflict.affected_events?.[0]
        return {
          kind: 'conflict',
          label: t('Resolve grading conflict'),
          report_name: ref?.report_name || null,
          criterion_id: String(conflict.criteria_id || ''),
          event_id: ref?.event_id || null,
        }
      })
    const proposals: GlobalReadinessBlocker[] = store.pendingProposals
      .filter((proposal) => !declaredProposalIds.has(String(proposal.proposal_id)))
      .map((proposal) => ({
        kind: 'rubric_proposal',
        label: t('Review scoring standard update'),
        criterion_id: proposal.target_criterion_id || null,
        proposal_id: proposal.proposal_id,
      }))
    return [...declared, ...conflicts, ...proposals]
  })

  const blockerSignature = computed(() =>
    blockers.value
      .map((item) => `${item.kind}:${item.criterion_id || ''}:${item.proposal_id || ''}`)
      .join('|'),
  )

  return { blockers, blockerSignature }
}
