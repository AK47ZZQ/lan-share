<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api, type TrashItem } from '../api'
import { confirmDlg, toast } from '../ui'

const items = ref<TrashItem[]>([])
const err = ref('')

async function load() {
  try { items.value = await api.trash() } catch (e) { err.value = (e as Error).message }
}
async function restore(t: TrashItem) {
  try {
    const r = await api.trashRestore(t.id)
    toast(`已恢复到 ${r.path}`, 'ok')
    await load()
  } catch (e) { toast((e as Error).message, 'err') }
}
async function remove(t: TrashItem) {
  if (!(await confirmDlg(`彻底删除「${t.name}」？不可恢复`, { danger: true, okText: '彻底删除' }))) return
  try { await api.trashDelete(t.id); await load() } catch (e) { toast((e as Error).message, 'err') }
}
async function empty() {
  if (!(await confirmDlg('清空回收站？其中所有项目将永久删除', { danger: true, okText: '清空' }))) return
  try { await api.trashEmpty(); await load(); toast('回收站已清空', 'ok') } catch (e) { toast((e as Error).message, 'err') }
}

onMounted(load)
</script>

<template>
  <main class="page">
    <div class="page-head">
      <h1>回收站</h1>
      <button class="btn danger sm" @click="empty" v-if="items.length">清空回收站</button>
    </div>
    <p v-if="err" class="err">{{ err }}</p>
    <div class="card flist" style="padding:8px">
      <div v-if="!items.length" class="empty">回收站是空的</div>
      <div v-for="t in items" :key="t.id" class="frow" style="cursor:default">
        <span class="icon">🗑️</span>
        <span class="name">
          <div>{{ t.name }}</div>
          <small>原位置：{{ t.orig_path }} · 剩 {{ t.days_left }} 天</small>
        </span>
        <span class="meta">{{ t.size_human }}</span>
        <span class="ops" style="opacity:1">
          <button class="btn ghost sm" @click="restore(t)">恢复</button>
          <button class="iconbtn danger" title="彻底删除" @click="remove(t)">✕</button>
        </span>
      </div>
    </div>
  </main>
</template>
