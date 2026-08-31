/* LAN Share Service Worker（PWA 阶段 0，保守策略）
   - 同源 GET 且属于带哈希的 /assets/ 或 /static/：缓存优先（资产名带哈希，内容不可变）
   - 页面导航：网络优先，断网时回退缓存的 index.html
   - 其余（/api /dav /thumb /preview /files 等）：完全透传，绝不缓存 */
const CACHE = 'ls-pwa-v1'
const ASSETS_CACHE_FIRST = ['/assets/', '/static/']

self.addEventListener('install', (e) => { self.skipWaiting() })

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  )
})

self.addEventListener('fetch', (e) => {
  const req = e.request
  if (req.method !== 'GET') return
  const url = new URL(req.url)
  if (url.origin !== location.origin) return

  if (ASSETS_CACHE_FIRST.some(p => url.pathname.startsWith(p))) {
    e.respondWith(
      caches.open(CACHE).then(async c => {
        const hit = await c.match(req)
        if (hit) return hit
        const res = await fetch(req)
        if (res.ok) c.put(req, res.clone())
        return res
      })
    )
    return
  }

  if (req.mode === 'navigate') {
    e.respondWith(
      fetch(req).catch(async () => {
        const c = await caches.open(CACHE)
        return (await c.match('/')) || Response.error()
      })
    )
  }
  // 其余请求不拦截：直连网络
})
