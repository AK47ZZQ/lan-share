<!-- 反馈（v4.0.9 P1+P4）：提交 / 我的反馈列表 / 详情。
     与 App 的 feedback_page.dart 字段对齐，只是渲染层不同。
     上报语义：**本地落盘永远成功**，上报是附带动作 —— 界面用「已保存」而不是「提交成功」，
     因为上报失败不该让用户重填。 -->
<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { feedbackApi, TYPE_LABEL, statusClass, type FeedbackItem, type FeedbackType } from '../api/feedback'
import { toast } from '../ui'

const items = ref<FeedbackItem[]>([])
const err = ref('')
const busy = ref(false)
const loading = ref(true)

// —— 新建表单 ——
const title = ref('')
const type = ref<FeedbackType>('idea')
const body = ref('')
const files = ref<File[]>([])
const pubToGh = ref(false)
const composing = ref(false)

// —— 详情 ——
const openId = ref<string | null>(null)
const detail = ref<FeedbackItem | null>(null)
const reply = ref('')

const TYPES: FeedbackType[] = ['idea', 'bug', 'question', 'other']

async function load() {
  loading.value = true
  try {
    items.value = await feedbackApi.list()
    err.value = ''
  } catch (e) { err.value = (e as Error).message } finally { loading.value = false }
}

function pickFiles(ev: Event) {
  const input = ev.target as HTMLInputElement
  const picked = Array.from(input.files ?? [])
  const room = 3 - files.value.length
  if (picked.length > room) toast(`附件最多 3 个，只收了前 ${room} 个`, 'err')
  files.value = [...files.value, ...picked.slice(0, Math.max(0, room))]
  input.value = ''
}

function dropFile(i: number) { files.value = files.value.filter((_, k) => k !== i) }

async function submit() {
  if (!title.value.trim() || busy.value) return
  busy.value = true
  try {
    const r = await feedbackApi.create({
      title: title.value.trim(),
      type: type.value,
      body: body.value,
      app_ver: 'web',
      device: navigator.userAgent.slice(0, 80),
      files: files.value,
      public_to_github: pubToGh.value,
    })
    // 说「已保存」而不是「提交成功」：本地一定落盘，上报可能还没走完
    toast(pubToGh.value ? `已保存（${r.id}），公开同步可能需要几秒` : `已保存（${r.id}）`, 'ok')
    title.value = ''; body.value = ''; files.value = []; pubToGh.value = false
    composing.value = false
    await load()
  } catch (e) { toast((e as Error).message, 'err') } finally { busy.value = false }
}

const IMG_RE = /\.(jpe?g|png|webp)$/i

function isImg(src: string): boolean {
  return IMG_RE.test(src.split('?')[0])
}

function fileName(src: string): string {
  try { return decodeURIComponent(src.split('/').pop() || src) }
  catch { return src }
}

async function open(fid: string) {
  if (openId.value === fid) { openId.value = null; detail.value = null; return }
  openId.value = fid
  detail.value = null
  try { detail.value = await feedbackApi.detail(fid) }
  catch (e) { toast((e as Error).message, 'err') }
}

async function sendReply() {
  const t = reply.value.trim()
  if (!t || !openId.value) return
  try {
    await feedbackApi.append(openId.value, t)
    reply.value = ''
    detail.value = await feedbackApi.detail(openId.value)
    toast('已补充', 'ok')
  } catch (e) { toast((e as Error).message, 'err') }
}

const openCount = computed(() =>
  items.value.filter(i => i.status !== '已完成' && i.status !== '不处理').length)

onMounted(load)
</script>

<template>
  <main class="page">
    <div class="page-head">
      <h1>反馈</h1>
      <small style="opacity:.6">想法、问题、疑问都可以提 —— 先存本机，再按设置上报</small>
    </div>
    <p v-if="err" class="err">{{ err }}</p>

    <!-- 折叠的发布入口：默认收起，避免占满首屏 -->
    <div class="card" style="padding:14px 16px;margin-bottom:12px">
      <div v-if="!composing" style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">
        <button class="btn sm" @click="composing = true">＋ 提一条反馈</button>
        <span style="font-size:12.5px;color:var(--text-3)">
          共 {{ items.length }} 条<template v-if="openCount"> · 未完成 {{ openCount }} 条</template>
        </span>
      </div>

      <div v-else style="display:grid;gap:10px">
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          <input v-model="title" placeholder="一句话说清问题或建议（不超过 80 字）"
                 style="flex:1;min-width:240px;font-size:14px" />
          <select v-model="type" style="max-width:120px">
            <option v-for="t in TYPES" :key="t" :value="t">{{ TYPE_LABEL[t] }}</option>
          </select>
        </div>
        <textarea v-model="body" rows="5" placeholder="详细描述：发生了什么、你期望什么、什么时候开始出现的"
                  style="width:100%;font-size:14px;resize:vertical"></textarea>

        <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
          <label class="btn ghost sm" style="cursor:pointer">
            选择截图（最多 3 张）
            <input type="file" accept="image/*,.txt,.md" multiple style="display:none" @change="pickFiles" />
          </label>
          <span v-for="(f, i) in files" :key="i" style="font-size:12.5px;color:var(--text-3)">
            {{ f.name }}
            <button class="iconbtn" title="移除" @click="dropFile(i)">✕</button>
          </span>
        </div>

        <!-- P4：公开到 GitHub —— 必须把「公开」两个字说在前面 -->
        <label style="display:flex;align-items:flex-start;gap:8px;font-size:13px;cursor:pointer">
          <input v-model="pubToGh" type="checkbox" style="width:auto;margin-top:3px" />
          <span>
            同时公开到 GitHub（<code>AK47ZZQ/lan-share</code>）
            <br />
            <small style="color:var(--text-3)">
              ⚠️ 勾选后这条反馈、以及你的版本和设备信息会<strong>公开在互联网上，任何人可见</strong>。
              正文里别写密码、内网地址、公司名。截图不会上传。
            </small>
          </span>
        </label>

        <div style="display:flex;gap:8px">
          <button class="btn sm" :disabled="busy || !title.trim()" @click="submit">
            {{ busy ? '提交中…' : '提交' }}
          </button>
          <button class="btn ghost sm" @click="composing = false">取消</button>
        </div>
      </div>
    </div>

    <!-- 列表 -->
    <div v-if="loading" class="empty">加载中…</div>
    <div v-else-if="!items.length" class="empty">还没有反馈</div>
    <div v-else class="card flist" style="padding:8px">
      <div v-for="it in items" :key="it.id">
        <div class="frow" style="cursor:pointer" @click="open(it.id)">
          <span class="icon">{{ it.type === 'bug' ? '🐞' : it.type === 'idea' ? '💡' : it.type === 'question' ? '❓' : '📝' }}</span>
          <span class="name">
            <div>{{ it.title }}</div>
            <small>
              {{ TYPE_LABEL[it.type] || it.type }} · {{ it.created }}
              <template v-if="it.note"> · 开发者：{{ it.note }}</template>
            </small>
          </span>
          <span class="ops">
            <span :class="['fb-status', statusClass(it.status)]">{{ it.status }}</span>
          </span>
        </div>

        <!-- 展开的详情 -->
        <div v-if="openId === it.id" style="padding:10px 14px 16px;border-top:1px solid var(--line)">
          <div v-if="!detail" class="empty" style="padding:6px">加载中…</div>
          <template v-else>
            <pre style="white-space:pre-wrap;font-family:inherit;font-size:13.5px;margin:0 0 10px">{{ detail.body || '（无正文）' }}</pre>
            <div v-if="detail.images?.length" style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px">
              <template v-for="src in detail.images" :key="src">
                <img v-if="isImg(src)" :src="src" alt="截图"
                     style="width:120px;height:90px;object-fit:cover;border-radius:8px;border:1px solid var(--line)" />
                <a v-else :href="src" target="_blank" rel="noopener"
                   style="display:inline-flex;align-items:center;gap:4px;padding:6px 10px;border-radius:8px;border:1px solid var(--line);font-size:13px">📄 {{ fileName(src) }}</a>
              </template>
            </div>
            <div style="font-size:12px;color:var(--text-3);margin-bottom:8px">
              版本 {{ detail.app || '-' }} · 设备 {{ detail.device || '-' }} · 编号 {{ detail.id }}
              <a v-if="detail.issue_url" :href="detail.issue_url" target="_blank" rel="noopener"
                 style="margin-left:8px">在 GitHub 查看 ↗</a>
            </div>
            <div style="display:flex;gap:8px">
              <input v-model="reply" placeholder="补充说明…" style="flex:1;font-size:13.5px"
                     @keydown.enter="sendReply" />
              <button class="btn sm" :disabled="!reply.trim()" @click="sendReply">补充</button>
            </div>
          </template>
        </div>
      </div>
    </div>
  </main>
</template>

<style scoped>
.fb-status {
  font-size: 12px;
  padding: 2px 8px;
  border-radius: 999px;
  white-space: nowrap;
}
.st-new   { background: rgba(140, 140, 150, .16); color: var(--text-3); }
.st-seen  { background: rgba(70, 130, 220, .16); color: #4a7fd6; }
.st-doing { background: rgba(230, 150, 40, .18); color: #c98421; }
.st-done  { background: rgba(50, 170, 100, .18); color: #2e9e60; }
.st-skip  { background: transparent; border: 1px dashed var(--line); color: var(--text-3); }
</style>
