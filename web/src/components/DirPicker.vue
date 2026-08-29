<script setup lang="ts">
// 目录选择器（移动到…）：导航 + 新建文件夹
import { onMounted, ref } from 'vue'
import { api, type Entry } from '../api'

const emit = defineEmits<{ pick: [path: string]; close: [] }>()
const cur = ref('')
const items = ref<Entry[]>([])
const err = ref('')

async function load(p: string) {
  try {
    const r = await api.list(p)
    cur.value = r.path
    items.value = r.items.filter(e => e.is_dir)
  } catch (e) { err.value = (e as Error).message }
}

async function newFolder() {
  const name = prompt('新文件夹名称')
  if (!name) return
  try {
    await api.mkdir([cur.value, name].filter(Boolean).join('/'))
    await load(cur.value)
  } catch (e) { err.value = (e as Error).message }
}

onMounted(() => load(''))
</script>

<template>
  <div class="crumbs" style="margin-bottom:4px">
    <a href="#" @click.prevent="load('')">根目录</a>
    <template v-for="(seg, i) in cur.split('/').filter(Boolean)" :key="i">
      <span class="sep">/</span>
      <a href="#" @click.prevent="load(cur.split('/').slice(0, i + 1).join('/'))">{{ seg }}</a>
    </template>
  </div>
  <div class="dirlist card" style="padding:6px">
    <div v-if="cur" class="dirrow" @click="load(cur.split('/').slice(0, -1).join('/'))">↰ 上一级</div>
    <div v-for="d in items" :key="d.path" class="dirrow" @click="load(d.path)">📁 {{ d.name }}</div>
    <div v-if="!items.length && cur" class="empty" style="padding:14px">（无子目录）</div>
  </div>
  <p v-if="err" class="err">{{ err }}</p>
  <div class="actions" style="display:flex;justify-content:space-between;margin-top:14px">
    <button class="btn ghost sm" @click="newFolder">＋ 新建文件夹</button>
    <div style="display:flex;gap:8px">
      <button class="btn ghost sm" @click="emit('close')">取消</button>
      <button class="btn sm" @click="emit('pick', cur)">移动到这里</button>
    </div>
  </div>
</template>
