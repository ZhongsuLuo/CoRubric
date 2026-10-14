import './assets/main.css'

import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import { translate } from './i18n'

const app = createApp(App)
app.use(createPinia())
app.config.globalProperties.$t = translate
app.mount('#app')
