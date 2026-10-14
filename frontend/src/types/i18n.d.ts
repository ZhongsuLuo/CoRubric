import type { translate } from '../i18n'

declare module 'vue' {
  interface ComponentCustomProperties {
    $t: typeof translate
  }
}
