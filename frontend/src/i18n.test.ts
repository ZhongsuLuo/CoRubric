import { afterEach, describe, expect, it } from 'vitest'
import { setLocale, translate } from './i18n'

describe('UI locale switching', () => {
  afterEach(() => setLocale('en'))

  it('switches fixed UI copy without changing interpolation values', () => {
    setLocale('en')
    expect(translate('Current Case')).toBe('Current Case')
    setLocale('zh')
    expect(translate('Current Case')).toBe('当前案例')
    expect(translate('Reset {caseId} to its experiment baseline.', { caseId: 'demo' })).toBe(
      '已将 demo 恢复到实验基线。',
    )
  })
})
