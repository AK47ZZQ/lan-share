import { createApp } from 'vue'
import App from './App.vue'
import { router } from './router'
import './theme'
import './styles.css'

createApp(App).use(router).mount('#app')
