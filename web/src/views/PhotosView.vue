<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '../api'
import { toast } from '../ui'

interface Ph { path: string; name: string; taken: string; thumb: string }
const groups = ref<{ date: string; photos: Ph[] }[]>([])
const total = ref(0)
const offset = ref(0)
const loading = ref(false)
const err = ref('')
const preview = ref<Ph | null>(null)
const LIMIT = 120

async function loadMore() {
  if (loading.value) return
  loading.value = true
  err.value = ''
  try {
    const r = await api.photos(LIMIT, offset.value)
    total.value = r.total
    for (const p of r.items) {
      const last = groups.value[groups.value.length - 1]
      if (last && last.date === p.taken) last.photos.push(p)
      else groups.value.push({ date: p.taken, photos: [p] })
    }
    offset.value += r.items.length
  } catch (e) { err.value = (e as Error).message }
  finally { loading.value = false }
}

function onScroll() {
  if (loading.value || (total.value && offset.value >= total.value)) return
  if (window.innerHeight + window.scrollY > document.body.scrollHeight - 500) void loadMore()
}

onMounted(() => { void loadMore(); window.addEventListener('scroll', onScroll) })
</script>

<template>
  <main class="page">
    <div class="page-head"><h1>照片</h1><span class="badge" v-if="total">{{ total }} 张</span></div>
    <p v-if="err" class="err">{{ err }}</p>
    <div v-if="!groups.length && !loading" class="card empty">还没有照片（自动备份的手机照片会出现在这里）</div>
    <div v-for="g in groups" :key="g.date">
      <div class="phdate">{{ g.date }}</div>
      <div class="photogrid">
        <div v-for="p in g.photos" :key="p.path" class="ph" @click="preview = p">
          <img :src="p.thumb" loading="lazy" alt="" />
        </div>
      </div>
    </div>
    <div class="empty" v-if="loading">加载中…</div>

    <div v-if="preview" class="overlay" @click.self="preview = null">
      <div style="max-width:94vw;max-height:90vh;text-align:center">
        <img :src="`/preview/${encodeURI(preview.path)}`" style="max-width:94vw;max-height:82vh;border-radius:12px" />
        <div style="color:#fff;margin-top:8px;font-size:13px;text-shadow:0 1px 4px rgba(0,0,0,.6)">
          {{ preview.name }} · {{ preview.taken }}
        </div>
      </div>
    </div>
  </main>
</template>
