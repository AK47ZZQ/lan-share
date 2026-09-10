// 分块上传引擎（v3.48 从 api.ts 按域拆分）：秒传指纹/并发分块/断点续传/
// 公网大文件提醒。进度平滑与重试策略保留原实现。
import { confirmDlg } from '../ui'
import { handle } from './core'

export const CHUNK = 5 * 1024 * 1024

async function sha256Hex(buf: ArrayBuffer): Promise<string> {
  const d = await crypto.subtle.digest('SHA-256', buf)
  return Array.from(new Uint8Array(d)).map(b => b.toString(16).padStart(2, '0')).join('')
}

/** 当前是否局域网访问（非私网地址即视为公网/FRP 隧道）。?net=public|lan 可强制指定（测试用）。 */
export function isLanAccess(): boolean {
  const q = new URLSearchParams(location.search).get('net')
  if (q === 'public') return false
  if (q === 'lan') return true
  const h = location.hostname
  if (!h || h === 'localhost' || h === '127.0.0.1' || h.endsWith('.local')) return true
  if (/^192\.168\.\d+\.\d+$/.test(h) || /^10\.\d+\.\d+\.\d+$/.test(h)) return true
  if (/^172\.(1[6-9]|2\d|3[01])\.\d+\.\d+$/.test(h)) return true
  if (h.startsWith('fe80:') || /^fd[0-9a-f]{2}:/i.test(h)) return true
  return false
}

function fmtDur(sec: number): string {
  if (!isFinite(sec) || sec < 0) return '--'
  sec = Math.round(sec)
  if (sec < 60) return `${sec} 秒`
  if (sec < 3600) return `${Math.floor(sec / 60)} 分 ${String(sec % 60).padStart(2, '0')} 秒`
  return `${Math.floor(sec / 3600)} 小时 ${Math.round((sec % 3600) / 60)} 分`
}

const GB = 1024 ** 3
const PUBLIC_WARN_BYTES = 2 * GB          // 公网上传提醒阈值（局域网不提醒）

/** 公网上传大文件前弹窗提醒（2/5/10GB 三档措辞）；局域网或小文件直接放行。返回 false=用户取消 */
export async function confirmLargeUpload(file: File): Promise<boolean> {
  // ?warnmb= 覆盖提醒阈值（MB）——浏览器自动化/UI 验证用测试钩子，与 ?net= 配套
  const w = Number(new URLSearchParams(location.search).get('warnmb'))
  const threshold = w > 0 ? w * 1024 * 1024 : PUBLIC_WARN_BYTES
  if (isLanAccess() || file.size < threshold) return true
  const gb = (file.size / GB).toFixed(1)
  const est = fmtDur(file.size / (3.75 * 1024 * 1024)) // 按家庭上行 30Mbps ≈ 3.75MB/s 估算
  let tip: string
  if (file.size >= 10 * GB) {
    tip = `这是一个 ${gb} GB 的大文件，当前正在通过公网上传，预计需要 ${est} 甚至更久（家庭上行带宽通常远低于局域网）。中断后可断点续传，但强烈建议等回到局域网 Wi-Fi 再传。`
  } else if (file.size >= 5 * GB) {
    tip = `这是一个 ${gb} GB 的大文件，当前正在通过公网上传，预计需要 ${est} 左右（按上行 30Mbps 估算）。回到局域网内传输会快得多，确定现在继续吗？`
  } else {
    tip = `文件 ${gb} GB，当前正在通过公网上传，速度受家庭上行带宽限制（预计 ${est}）。可等回到局域网再传，也可现在继续。`
  }
  return confirmDlg(tip, { title: '🌐 公网上传大文件提醒', okText: '继续上传' })
}

/** XHR 发送单个分块（fetch 不支持上传进度；XHR upload.onprogress 可逐字节回报）。
 *  120s 超时：半开连接（断网/休眠唤醒）否则会永久卡死并发 worker */
function xhrSendChunk(url: string, blob: Blob, signal: AbortSignal | undefined,
                      onSent: (loaded: number) => void): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) { reject(new DOMException('Aborted', 'AbortError')); return }
    const xhr = new XMLHttpRequest()
    xhr.open('POST', url)
    xhr.timeout = 120_000
    signal?.addEventListener('abort', () => xhr.abort(), { once: true })
    xhr.upload.onprogress = e => { if (e.lengthComputable) onSent(e.loaded) }
    xhr.onload = () => xhr.status >= 200 && xhr.status < 300 ? resolve() : reject(new Error(`HTTP ${xhr.status}`))
    xhr.onerror = () => reject(new Error('网络错误'))
    xhr.ontimeout = () => reject(new Error('超时'))
    xhr.onabort = () => reject(new DOMException('Aborted', 'AbortError'))
    xhr.send(blob)
  })
}

export interface UploadHooks {
  signal?: AbortSignal
  resumeSid?: string                       // 断点续传：复用既有会话（跨刷新）
  onSession?: (sid: string) => void        // 会话建立后回调（调用方持久化）
}

/** 分块上传：≤100MB 计算 sha256 走秒传；自适应分片（服务端下发）；≥256MB 并发 3 路；
 *  每块重试 3 次；显示速度/剩余时间；支持 resumeSid 断点续传（会话失效自动回落新会话） */
export async function uploadResumable(
  destDir: string, file: File,
  onProgress: (sent: number, total: number, stage: string) => void,
  hooks: UploadHooks = {},
): Promise<{ instant: boolean }> {
  const total = file.size
  const { signal } = hooks
  let sha: string | undefined
  if (!hooks.resumeSid && total <= 100 * 1024 * 1024) {
    signal?.throwIfAborted()
    onProgress(0, total, '计算指纹…')
    sha = await sha256Hex(await file.arrayBuffer())
  }
  signal?.throwIfAborted()

  let sid = hooks.resumeSid ?? ''
  let chunkSize = CHUNK
  let received = new Set<number>()
  if (sid) {
    // 续传：校验会话仍存活，拿已收块与分片大小
    try {
      const st = await fetch(`/api/upload/state/${sid}`)
        .then(r => handle<{ received: number[]; chunk_size: number }>(r))
      for (const i of st.received) received.add(i)
      chunkSize = st.chunk_size
    } catch {
      sid = ''          // 会话已过期/被清理 → 回落新会话
      received = new Set()
    }
  }
  if (!sid) {
    const init = await fetch('/api/upload/init', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ dest_dir: destDir, filename: file.name, size: total, sha256: sha }),
    }).then(r => handle<{ instant: boolean; session?: string; chunk_size?: number }>(r))
    if (init.instant) { onProgress(total, total, '秒传'); return { instant: true } }
    sid = init.session!
    chunkSize = init.chunk_size ?? CHUNK
  }
  hooks.onSession?.(sid)

  const chunks = Math.ceil(total / chunkSize)

  let doneBytes = 0
  for (const i of received) doneBytes += Math.min(chunkSize, total - i * chunkSize)
  const inflight = new Map<number, number>()   // 进行中分块已发送字节（平滑进度）
  let nextIndex = 0
  let lastReport = 0
  const samples: { t: number; b: number }[] = []

  const report = (stage: string) => {
    const now = performance.now()
    if (now - lastReport < 400) return
    lastReport = now
    let sent = doneBytes
    for (const v of inflight.values()) sent += v
    sent = Math.min(sent, total)
    samples.push({ t: now, b: sent })
    while (samples.length > 2 && now - samples[0].t > 3000) samples.shift()
    let s = stage
    const dt = (now - samples[0].t) / 1000
    const db = sent - samples[0].b
    if (dt > 0.5 && db > 0) {
      const speed = db / dt
      s += ` · ${speed >= 1024 * 1024 ? (speed / 1024 / 1024).toFixed(1) + ' MB/s' : Math.round(speed / 1024) + ' KB/s'}`
      if (sent < total) s += ` · 剩余 ${fmtDur((total - sent) / speed)}`
    }
    onProgress(sent, total, s)
  }

  // 内部联动中止：任一 worker 失败即停其余（防僵尸 worker 后台空跑整个文件）；
  // 用户 signal 中止时同样联动
  const inner = new AbortController()
  const onOuterAbort = () => inner.abort()
  signal?.addEventListener('abort', onOuterAbort, { once: true })

  const sendChunk = async (i: number): Promise<void> => {
    const blob = file.slice(i * chunkSize, Math.min((i + 1) * chunkSize, total))
    for (let retry = 0; retry < 3; retry++) {
      try {
        inner.signal.throwIfAborted()
        await xhrSendChunk(`/api/upload/chunk/${sid}?index=${i}`, blob, inner.signal,
          loaded => { inflight.set(i, loaded); report(received.size ? '续传中' : '上传中') })
        doneBytes += blob.size
        inflight.delete(i)
        report(received.size ? '续传中' : '上传中')
        return
      } catch (e) {
        inflight.delete(i)
        if ((e as Error).name === 'AbortError') throw e
        if (retry === 2) { inner.abort(); throw new Error(`分块 ${i} 上传失败`) }
      }
    }
  }

  if (total >= 256 * 1024 * 1024 && chunks > 2) {
    // 大文件并发 3 路：减少高延迟链路空转
    const worker = async () => {
      while (true) {
        if (inner.signal.aborted) return
        const i = nextIndex++
        if (i >= chunks) return
        if (received.has(i)) continue
        await sendChunk(i)
      }
    }
    try {
      await Promise.all(Array.from({ length: 3 }, () => worker()))
    } finally {
      signal?.removeEventListener('abort', onOuterAbort)
    }
  } else {
    try {
      for (let i = 0; i < chunks; i++) {
        if (received.has(i)) continue
        await sendChunk(i)
      }
    } finally {
      signal?.removeEventListener('abort', onOuterAbort)
    }
  }

  signal?.throwIfAborted()
  onProgress(total, total, '合并校验…')
  const fin = await fetch(`/api/upload/finish/${sid}`, { method: 'POST', signal })
    .then(r => handle<{ ok: boolean; deduped: boolean }>(r))
  return { instant: fin.deduped }
}
