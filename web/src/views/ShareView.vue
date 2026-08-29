<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { publicApi, type ShareMeta } from '../api'

const route = useRoute()
const token = String(route.params.token || '')
const meta = ref<ShareMeta | null>(null)
const err = ref('')
const pwd = ref('')
const dlUrl = ref('')
const busy = ref(false)
const limitReached = ref(false)
const gone = ref('')   // ''=存在；revoked=不存在/已撤销；expired=已过期

async function prepare() {
  busy.value = true
  err.value = ''
  try {
    const r = await publicApi.prepare(token, pwd.value || null)
    dlUrl.value = r.url
    window.open(r.url, '_blank')
  } catch (e) {
    if ((e as Error).message.includes('上限')) { limitReached.value = true; return }
    err.value = (e as Error).message
  } finally { busy.value = false }
}

onMounted(async () => {
  try { meta.value = await publicApi.meta(token) }
  catch (e) {
    const m = (e as Error).message
    if (m.toLowerCase().includes('not found') || m.includes('不存在')) gone.value = 'revoked'
    else if (m.includes('expired') || m.includes('过期')) gone.value = 'expired'
    else err.value = m
  }
})
</script>

<template>
  <main class="login-wrap">
    <div class="card login-card" style="text-align:left">
      <div class="login-logo"><span class="logo big">◈</span></div>
      <h1 style="text-align:center">文件分享</h1>
      <div v-if="meta" style="margin-top:16px">
        <div style="display:flex;gap:12px;align-items:center;margin-bottom:14px">
          <span class="icon" style="width:44px;height:44px;display:grid;place-items:center;font-size:20px;background:var(--glass-strong);border:1px solid var(--line);border-radius:12px">📄</span>
          <div>
            <div style="font-weight:600">{{ meta.name }}</div>
            <small style="color:var(--text-3)">{{ meta.size_human }} · {{ meta.expires_in }}内有效
              <template v-if="meta.max_downloads"> · 已下 {{ meta.downloads }}/{{ meta.max_downloads }}</template></small>
          </div>
        </div>
        <label v-if="meta.needs_password && !dlUrl" class="field">提取码
          <input v-model="pwd" @keydown.enter="prepare" placeholder="请向分享者索取" />
        </label>
        <div v-if="limitReached" class="limit-box">
          <div style="font-size:30px">🚫</div>
          <b>下载次数已达上限</b>
          <p style="font-size:13px;color:var(--text-3);margin:4px 0 0">
            该分享的下载次数已用完，请联系分享者重新创建分享链接。
          </p>
        </div>
        <p v-if="err" class="err">{{ err }}</p>
        <button v-if="!dlUrl" class="btn" style="width:100%;justify-content:center" :disabled="busy" @click="prepare">
          {{ busy ? '获取中…' : meta.needs_password ? '验证并下载' : '下载文件' }}
        </button>
        <p v-else class="ok-text">下载已开始（若被拦截请检查浏览器下载栏）·
          <a :href="dlUrl" target="_blank">再下一次</a></p>
      </div>
      <div v-else-if="gone" class="limit-box">
        <div style="font-size:30px">{{ gone === 'expired' ? '⏰' : '🔍' }}</div>
        <b>{{ gone === 'expired' ? '分享已过期' : '分享不存在或已被撤销' }}</b>
        <p style="font-size:13px;color:var(--text-3);margin:4px 0 0">
          {{ gone === 'expired' ? '链接超过了有效期，请联系分享者重新创建。' : '请向分享者确认链接是否正确，或索取新的分享链接。' }}
        </p>
      </div>
      <p v-else-if="err" class="err" style="text-align:center">{{ err }}</p>
      <p v-else class="empty">加载中…</p>
    </div>
  </main>
</template>
