<script setup lang="ts">
// 系统设置（v3.39 从 AdminView 拆出）：公网地址 / 事件推送 Webhook / 每日健康日报。
import { onMounted, ref } from 'vue'
import { api } from '../api'
import { toast } from '../ui'

const err = ref('')

const pubUrl = ref('')
const pubAlt = ref('')
async function savePubUrl() {
  try { await api.setPublicUrl(pubUrl.value.trim(), pubAlt.value.trim()); toast('公网地址已保存', 'ok') } catch (e) { toast((e as Error).message, 'err') }
}

// 事件推送（通知 bot）
const hookUrl = ref('')
const hookBusy = ref(false)
async function saveHook() {
  hookBusy.value = true
  try { await api.setWebhook(hookUrl.value.trim()); toast('推送地址已保存', 'ok') }
  catch (e) { toast((e as Error).message, 'err') } finally { hookBusy.value = false }
}
async function testHook() {
  try { await api.testWebhook(); toast('测试消息已发送，去手机上看看', 'ok') }
  catch (e) { toast((e as Error).message, 'err') }
}

// 每日健康日报
const hrEnabled = ref(true)
const hrTime = ref('09:00')
const hrBusy = ref(false)
const hrLastReport = ref('')
async function saveHr() {
  hrBusy.value = true
  try {
    const r = await api.setHealthReport(hrEnabled.value, hrTime.value.trim())
    hrTime.value = r.time
    toast(`健康日报已${r.enabled ? `开启（每天 ${r.time}）` : '关闭'}`, 'ok')
  } catch (e) { toast((e as Error).message, 'err') } finally { hrBusy.value = false }
}
async function testHr() {
  hrBusy.value = true
  try {
    const r = await api.testHealthReport()
    hrLastReport.value = r.report
    toast(r.sent === null ? 'Webhook 未配置——报告内容见下方预览'
          : r.sent ? '日报已发送，去手机上看看' : 'Webhook 发送失败（见服务器审计日志）',
          r.sent === false ? 'err' : 'ok')
  } catch (e) { toast((e as Error).message, 'err') } finally { hrBusy.value = false }
}

onMounted(async () => {
  try { const r = await api.getPublicUrl(); pubUrl.value = r.public_base_url; pubAlt.value = r.public_base_url_alt || '' } catch { /* */ }
  try { hookUrl.value = (await api.getWebhook()).url } catch { /* */ }
  try { const r = await api.getHealthReport(); hrEnabled.value = r.enabled; hrTime.value = r.time } catch { /* */ }
})
</script>

<template>
  <main class="page">
    <div class="page-head">
      <h1>系统设置</h1>
      <span style="font-size:12.5px;color:var(--text-3)">公网入口 · 事件推送 · 健康日报</span>
    </div>
    <p v-if="err" class="err">{{ err }}</p>

    <!-- 公网地址设置 -->
    <div class="card" style="padding:14px 16px;display:grid;gap:8px">
      <div style="font-size:13.5px;font-weight:600">🌐 公网访问地址（主/备隧道）</div>
      <div style="display:flex;gap:8px;flex-wrap:wrap">
        <input v-model="pubUrl" placeholder="主隧道，如 https://your-frp-domain.com:xxxxx" style="flex:1;min-width:220px;font-size:14px" />
        <input v-model="pubAlt" placeholder="备用隧道（可空），如 https://your-frp-domain.com:xxxxx" style="flex:1;min-width:220px;font-size:14px" />
        <button class="btn sm" @click="savePubUrl">保存</button>
      </div>
      <p style="font-size:12px;color:var(--text-3);margin:0">保存后，分享卡片提供局域网/公网复制按钮，App 登录页展示主/备隧道快捷入口；备用入口的防爆破分账与 healthz 收敛同步生效。</p>
    </div>

    <!-- 事件推送（通知 bot） -->
    <div class="card" style="padding:14px 16px;margin-top:12px;display:grid;gap:8px">
      <div style="font-size:13.5px;font-weight:600">📮 事件推送（通知 bot）</div>
      <div style="display:flex;gap:8px;flex-wrap:wrap">
        <input v-model="hookUrl" placeholder="GET 模板 URL，需含 {title}/{body} 占位符" style="flex:1;min-width:260px;font-size:14px" />
        <button class="btn sm" :disabled="hookBusy" @click="saveHook">保存</button>
        <button class="btn ghost sm" :disabled="hookBusy || !hookUrl" @click="testHook">发送测试</button>
      </div>
      <p style="font-size:12px;color:var(--text-3);margin:0">
        定向分享、新成员加入时推送到站外（GET 模板，占位符经 URL 编码后替换）。示例：<span v-pre>ntfy <code>https://ntfy.sh/你的主题/{title}</code> · Bark <code>https://api.day.app/你的key/{title}/{body}</code> · Server酱 <code>https://sctapi.ftqq.com/你的key.send?title={title}&desp={body}</code></span>
      </p>
    </div>

    <!-- 每日健康日报 -->
    <div class="card" style="padding:14px 16px;margin-top:12px;display:grid;gap:8px">
      <div style="font-size:13.5px;font-weight:600">🩺 每日健康日报</div>
      <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
        <label style="display:flex;align-items:center;gap:6px;font-size:13.5px">
          <input v-model="hrEnabled" type="checkbox" style="width:auto" /> 每天推送
        </label>
        <input v-model="hrTime" placeholder="09:00" style="width:110px;font-size:14px" />
        <button class="btn sm" :disabled="hrBusy" @click="saveHr">保存</button>
        <button class="btn ghost sm" :disabled="hrBusy" @click="testHr">立即采集一次</button>
      </div>
      <p style="font-size:12px;color:var(--text-3);margin:0">
        汇总数据盘水位、昨晚备份产物新鲜度、证书剩余天数、公网隧道探活与成员/照片统计，推到上面的 Webhook。
        磁盘 ≥85%、备份超 26h 未更新、证书剩余 &lt;14 天、隧道探活失败会标 ⚠️。
      </p>
      <pre v-if="hrLastReport" style="font-size:12px;background:var(--glass);border:1px solid var(--line);border-radius:8px;padding:10px;margin:0;white-space:pre-wrap">{{ hrLastReport }}</pre>
    </div>
  </main>
</template>
