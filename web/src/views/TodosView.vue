<!-- 效率舱 · 待办（v1.1 M1）：Web 只读列表 + 勾选（最终计划 §5 M1）。
     分组口径与 App 一致：今天（含逾期）/ 未来 7 天 / 以后 / 已完成折叠。 -->
<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { todosApi, type TodoItem, type TodoListInfo, type TodoMember } from '../api/todos'
import { toast } from '../ui'

const todos = ref<TodoItem[]>([])
const lists = ref<TodoListInfo[]>([])
const members = ref<TodoMember[]>([])
const err = ref('')
const showDone = ref(false)
const newTitle = ref('')
const newListId = ref<number | null>(null)
const busy = ref(false)

async function load() {
  try {
    const [a, l] = await Promise.all([todosApi.all(), todosApi.lists()])
    todos.value = a.todos
    members.value = a.members
    lists.value = l.lists
    if (newListId.value == null || !l.lists.some(x => x.id === newListId.value)) {
      newListId.value = l.lists[0]?.id ?? null
    }
    err.value = ''
  } catch (e) { err.value = (e as Error).message }
}

function name(uid: number | null): string {
  if (uid == null || uid <= 0) return ''
  const m = members.value.find(x => x.id === uid)
  if (!m) return '已停用成员'
  return m.nickname || m.username
}

const todayStart = () => { const d = new Date(); d.setHours(0, 0, 0, 0); return d.getTime() / 1000 }

const groups = computed(() => {
  const ts = todayStart()
  const weekEnd = ts + 7 * 86400
  const g: Record<string, TodoItem[]> = { '今天（含逾期）': [], '未来 7 天': [], 以后: [], 已完成: [] }
  for (const t of todos.value) {
    if (t.status === 'done') g['已完成'].push(t)
    else if (t.due_at != null && t.due_at < weekEnd) (t.due_at < ts ? g['今天（含逾期）'] : g['未来 7 天']).push(t)
    else g['以后'].push(t)
  }
  return g
})

const doneToday = computed(() => todos.value.filter(t => t.status === 'done' && (t.done_at ?? 0) >= todayStart()).length)
const openToday = computed(() => groups.value['今天（含逾期）'].length)

function dueText(t: TodoItem): string {
  if (t.due_at == null) return ''
  const d = new Date(t.due_at * 1000)
  const diff = Math.round((new Date(d).setHours(0, 0, 0, 0) - new Date().setHours(0, 0, 0, 0)) / 86400000)
  if (diff === 0) return '今天'
  if (diff === 1) return '明天'
  if (diff === -1) return '昨天'
  return `${d.getMonth() + 1}/${d.getDate()}`
}

async function toggle(t: TodoItem) {
  const target = t.status === 'done' ? 'todo' : 'done'
  try {
    await todosApi.patch(t.id, { status: target })
    if (target === 'done') toast(`已完成「${t.title}」`, 'ok')
    await load()
  } catch (e) { toast((e as Error).message, 'err') }
}

async function addTodo() {
  const title = newTitle.value.trim()
  if (!title || newListId.value == null || busy.value) return
  busy.value = true
  try {
    await todosApi.create(newListId.value, title)
    newTitle.value = ''
    await load()
  } catch (e) { toast((e as Error).message, 'err') } finally { busy.value = false }
}

async function removeTodo(t: TodoItem) {
  try { await todosApi.remove(t.id); await load() } catch (e) { toast((e as Error).message, 'err') }
}

onMounted(load)
</script>

<template>
  <main class="page">
    <div class="page-head">
      <h1>待办</h1>
      <small style="opacity:.6">效率舱 · 个人待办仅自己可见，家庭清单全家共享</small>
    </div>
    <p v-if="err" class="err">{{ err }}</p>

    <div class="card" style="padding:12px; margin-bottom:12px">
      <strong style="font-size:20px">{{ doneToday }} / {{ doneToday + openToday }}</strong>
      <span style="opacity:.6; margin-left:8px">今日待办（已完成 / 总数）</span>
    </div>

    <div class="card" style="padding:8px; margin-bottom:12px; display:flex; gap:8px">
      <select v-model="newListId" style="max-width:180px">
        <option v-for="l in lists" :key="l.id" :value="l.id">{{ l.kind === 'family' ? '全家·' : '' }}{{ l.name }}</option>
      </select>
      <input v-model="newTitle" placeholder="添加待办，回车提交" style="flex:1"
             @keydown.enter="addTodo" />
      <button class="btn sm" :disabled="busy || !newTitle.trim()" @click="addTodo">添加</button>
    </div>

    <div v-for="(items, g) in groups" :key="g">
      <template v-if="g !== '已完成' || showDone">
        <h3 style="margin:12px 0 4px; opacity:.7">{{ g }}</h3>
        <div class="card flist" style="padding:8px">
          <div v-if="!items.length" class="empty" style="padding:8px">没有待办</div>
          <div v-for="t in items" :key="t.id" class="frow" style="cursor:default">
            <span class="icon" style="cursor:pointer" @click="toggle(t)">
              {{ t.status === 'done' ? '✅' : '⬜' }}
            </span>
            <span class="name" :style="t.status === 'done' ? 'text-decoration:line-through;opacity:.6' : ''">
              <div>{{ t.title }}</div>
              <small>
                {{ t.list_kind === 'family' ? '全家' : '个人' }} · {{ t.list_name }}
                <template v-if="t.assignee_id != null"> · 指派：{{ name(t.assignee_id) }}</template>
                <template v-if="t.due_at != null">
                  · <span :style="t.status !== 'done' && t.due_at < todayStart() ? 'color:var(--err,#d33)' : ''">截止 {{ dueText(t) }}</span>
                </template>
              </small>
            </span>
            <span class="ops" style="opacity:1">
              <button class="iconbtn danger" title="删除" @click="removeTodo(t)">✕</button>
            </span>
          </div>
        </div>
      </template>
    </div>

    <button class="btn ghost sm" style="margin-top:8px" @click="showDone = !showDone">
      {{ showDone ? '收起已完成' : `展开已完成（${groups['已完成'].length}）` }}
    </button>
  </main>
</template>
