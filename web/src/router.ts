import { createRouter, createWebHistory } from 'vue-router'
import HomeView from './views/HomeView.vue'
import LoginView from './views/LoginView.vue'
import PhotosView from './views/PhotosView.vue'
import TrashView from './views/TrashView.vue'
import AdminView from './views/AdminView.vue'
import SharesView from './views/SharesView.vue'
import ShareView from './views/ShareView.vue'
import UpView from './views/UpView.vue'
import ActivityView from './views/ActivityView.vue'
import KbView from './views/KbView.vue'

export const router = createRouter({
  history: createWebHistory('/'),
  routes: [
    { path: '/', name: 'home', component: HomeView },
    { path: '/login', name: 'login', component: LoginView },
    { path: '/photos', name: 'photos', component: PhotosView },
    { path: '/kb', name: 'kb', component: KbView },
    { path: '/trash', name: 'trash', component: TrashView },
    { path: '/shares', name: 'shares', component: SharesView },
    { path: '/activity', name: 'activity', component: ActivityView },
    { path: '/admin', name: 'admin', component: AdminView, meta: { admin: true } },
    { path: '/s/:token', name: 'share', component: ShareView },
    { path: '/up', name: 'up', component: UpView },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})
