import { createRouter, createWebHistory } from 'vue-router'
import DashboardView from './views/DashboardView.vue'
import HomeView from './views/HomeView.vue'
import LoginView from './views/LoginView.vue'
import PhotosView from './views/PhotosView.vue'
import TrashView from './views/TrashView.vue'
import AdminView from './views/AdminView.vue'
import SharesView from './views/SharesView.vue'
import ShareView from './views/ShareView.vue'
import UpView from './views/UpView.vue'
import ActivityView from './views/ActivityView.vue'
import NotifyOpsView from './views/NotifyOpsView.vue'
import ShareOpsView from './views/ShareOpsView.vue'
import SettingsView from './views/SettingsView.vue'
import GovernanceView from './views/GovernanceView.vue'
import StatusView from './views/StatusView.vue'
import TodosView from './views/TodosView.vue'

// 产品定位（2026-09-10）：Web = 站长管理工作台，App = 内容入口。
// '/' 即控制台总览（meta.admin，成员被 App.vue 守卫转去 /files）；
// 内容四视图（文件/照片/回收站/动态）保留给全员（成员维持现有权限不变），
// 管理分区（成员与权限/通知运营/系统设置/治理/运行状态）仅 admin 渲染。
export const router = createRouter({
  history: createWebHistory('/'),
  routes: [
    { path: '/', name: 'home', component: DashboardView, meta: { admin: true } },
    { path: '/files', name: 'files', component: HomeView },
    { path: '/login', name: 'login', component: LoginView },
    { path: '/photos', name: 'photos', component: PhotosView },
    { path: '/trash', name: 'trash', component: TrashView },
    { path: '/shares', name: 'shares', component: SharesView },
    { path: '/activity', name: 'activity', component: ActivityView },
    { path: '/todos', name: 'todos', component: TodosView },
    { path: '/admin', name: 'admin', component: AdminView, meta: { admin: true } },
    { path: '/notify-ops', name: 'notify-ops', component: NotifyOpsView, meta: { admin: true } },
    { path: '/share-ops', name: 'share-ops', component: ShareOpsView, meta: { admin: true } },
    { path: '/settings', name: 'settings', component: SettingsView, meta: { admin: true } },
    { path: '/governance', name: 'governance', component: GovernanceView, meta: { admin: true } },
    { path: '/status', name: 'status', component: StatusView, meta: { admin: true } },
    { path: '/s/:token', name: 'share', component: ShareView },
    { path: '/up', name: 'up', component: UpView },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})
