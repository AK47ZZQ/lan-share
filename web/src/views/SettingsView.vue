<script setup lang="ts">
// 系统设置（v3.39 从 AdminView 拆出）：公网地址 / 事件推送 Webhook / 每日健康日报。
import { onMounted, ref } from 'vue'
import { api } from '../api'
import type { FeedbackConfig } from '../api/console'
import { toast } from '../ui'

const err = ref('')

const pubUrl = ref('')
async function savePubUrl() {
  try { await api.setPublicUrl(pubUrl.value.trim()); toast('公网地址已保存', 'ok') } catch (e) { toast((e as Error).message, 'err') }
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

// 反馈上报（v4.0.9 P2）：照更新源那套 —— 本机可填自己的接收端，
// 没填就走公开通道。三个 token 字段读回来是脱敏文案，所以只在用户
// 真的输入了才提交，否则会把「已设置（12 位）」原样写进库。
const fb = ref<FeedbackConfig | null>(null)
const fbUrl = ref('')
const fbToken = ref('')
const fbSource = ref('')
const fbEnabled = ref(false)
const fbBusy = ref(false)

async function loadFb() {
  try {
    const r = await api.getFeedbackConfig()
    fb.value = r
    fbUrl.value = r.url
    fbSource.value = r.source
    fbEnabled.value = r.enabled
  } catch (e) { /* 读不到就保持空态，不打断整页加载 */ }
}

async function saveFb() {
  fbBusy.value = true
  try {
    const body: Record<string, unknown> = {
      enabled: fbEnabled.value,
      url: fbUrl.value.trim(),
      source: fbSource.value.trim(),
    }
    // 只有真的打字了才提交 token —— 空的表示「保持不变」
    if (fbToken.value.trim()) body.token = fbToken.value.trim()
    await api.setFeedbackConfig(body)
    fbToken.value = ''
    toast(fbEnabled.value ? '反馈上报已保存' : '已关闭上报（反馈仍会保存在本机）', 'ok')
    await loadFb()
  } catch (e) { toast((e as Error).message, 'err') } finally { fbBusy.value = false }
}

onMounted(async () => {
  try { const r = await api.getPublicUrl(); pubUrl.value = r.public_base_url } catch { /* */ }
  try { hookUrl.value = (await api.getWebhook()).url } catch { /* */ }
  try { const r = await api.getHealthReport(); hrEnabled.value = r.enabled; hrTime.value = r.time } catch { /* */ }
  await loadFb()
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
        <button class="btn sm" @click="savePubUrl">保存</button>
      </div>
      <p style="font-size:12px;color:var(--text-3);margin:0">保存后，分享卡片提供局域网/公网复制按钮，App 登录页展示公网入口快捷方式。公网入口的防爆破分账与 healthz 收敛同步生效。</p>
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

    <!-- 反馈上报（v4.0.9 P2） -->
    <div class="card" style="padding:14px 16px;margin-top:12px;display:grid;gap:10px">
      <div style="font-size:13.5px;font-weight:600">💬 反馈上报</div>

      <p v-if="fb && !fb.available" style="font-size:13px;color:var(--err,#d33);margin:0">
        {{ fb.error }}
      </p>

      <template v-else>
        <label style="display:flex;align-items:center;gap:6px;font-size:13.5px">
          <input v-model="fbEnabled" type="checkbox" style="width:auto" />
          把本机收到的反馈上报到下面这个地址
        </label>

        <div style="display:flex;gap:8px;flex-wrap:wrap">
          <input v-model="fbUrl" placeholder="https://your-frp.example.com:48429/api/feedback/inbox"
                 style="flex:1;min-width:280px;font-size:14px" />
          <button class="btn sm" :disabled="fbBusy" @click="saveFb">保存</button>
        </div>

        <div style="display:flex;gap:8px;flex-wrap:wrap">
          <input v-model="fbToken" type="password" autocomplete="new-password"
                 :placeholder="fb?.token ? `Token：${fb.token}（留空不改）` : 'Token（与接收端的 feedback_inbox_token 一致）'"
                 style="flex:1;min-width:280px;font-size:14px" />
          <input v-model="fbSource" placeholder="本机标识，如 zzq-pc" style="width:200px;font-size:14px" />
        </div>

        <p style="font-size:12px;color:var(--text-3);margin:0;line-height:1.6">
          <strong>反馈永远先存在本机</strong>（<code>data/反馈/待处理/</code>），上报只是附带动作 ——
          地址填错或对方关机，你的反馈也不会丢。<br />
          接收端地址必须是 https（明文 http 只接受 127.0.0.1）。未勾选上面的开关时，一个请求都不会发出去。
        </p>

        <!-- 公开通道：开发端自用，普通用户不需要配 -->
        <details style="font-size:12.5px;color:var(--text-3)">
          <summary style="cursor:pointer">公开通道（GitHub Issue，一般不用配）</summary>
          <div style="display:grid;gap:8px;margin-top:8px">
            <p style="margin:0;line-height:1.6">
              配了 <code>owner/repo</code> + token 后，反馈会直接变成 issue。
              <br />
              分发给别人时<strong>不要</strong>预置 token —— 那等于把仓库写权限送出去。
              别人勾「公开」时由中继端代发，见 docs/反馈系统设计.md。
            </p>
            <div style="display:flex;gap:8px;flex-wrap:wrap">
              <input :value="fb?.gh_repo || ''" disabled placeholder="AK47ZZQ/lan-share"
                     style="flex:1;min-width:200px;font-size:13px" />
              <span style="font-size:12.5px;align-self:center">
                {{ fb?.gh_token || '未配置 token' }}
              </span>
            </div>
            <p style="margin:0">对应的服务端配置项：<code>feedback_gh_repo</code> / <code>feedback_gh_token</code>。当前只在后端可改。</p>
          </div>
        </details>
      </template>
    </div>
  </main>
</template>
