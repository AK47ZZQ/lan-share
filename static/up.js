// 手机直传页（CSP: script-src 'self'）
const CHUNK = 5 * 1024 * 1024;
let sessions = {};   // key -> session id（页面内断点续传）
let seq = 0;

const $list = document.getElementById('list');

async function checkAuth() {
  try {
    const r = await fetch('/api/auth/me');
    if (r.status === 401) location.href = '/login?next=/up';
  } catch { location.href = '/login?next=/up'; }
}

function addItem(name) {
  const id = 'f' + (++seq);
  const div = document.createElement('div');
  div.className = 'item';
  div.innerHTML = `<div class="row"><div class="name">${name.replace(/</g,'&lt;')}</div><div class="st" id="${id}-st">等待</div></div><div class="bar"><div class="fill" id="${id}-fill"></div></div>`;
  $list.prepend(div);
  return id;
}
const setStatus = (id, s, cls) => { const e = document.getElementById(id + '-st'); e.textContent = s; e.className = 'st ' + (cls || ''); };
const setPct = (id, p) => document.getElementById(id + '-fill').style.width = p + '%';

async function sha256Hex(file) {
  const buf = await file.arrayBuffer();
  const h = await crypto.subtle.digest('SHA-256', buf);
  return [...new Uint8Array(h)].map(b => b.toString(16).padStart(2, '0')).join('');
}
async function withRetry(n, fn) { let e; for (let i = 0; i < n; i++) { try { return await fn(); } catch (x) { e = x; await new Promise(r => setTimeout(r, 800 * (i + 1))); } } throw e; }

async function uploadFile(file) {
  const id = addItem(file.name);
  const DEST = 'incoming';
  try {
    let sha = null;
    if (file.size <= 100 * 1024 * 1024 && file.size > 0) { try { sha = await sha256Hex(file); } catch {} }
    const key = `up:${DEST}/${file.name}:${file.size}`;
    const initReq = s => fetch('/api/upload/init', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ dest_dir: DEST, filename: file.name, size: file.size, sha256: s }) }).then(async r => { if (r.status === 401) { location.href = '/login?next=/up'; throw new Error('need login'); } if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || 'init failed'); return r.json(); });
    let session = null, received = [];
    if (sessions[key]) {
      const st = await fetch(`/api/upload/state/${sessions[key]}`).then(r => r.ok ? r.json() : null).catch(() => null);
      if (st) { session = st.session; received = st.received; } else delete sessions[key];
    }
    if (!session) {
      const init = await initReq(sha);
      if (init.instant) { setPct(id, 100); setStatus(id, '⚡ 完成（内容已存在）', 'ok'); return; }
      session = init.session; received = init.received || []; sessions[key] = session;
    }
    const total = Math.ceil(file.size / CHUNK);
    for (let i = 0; i < total; i++) {
      if (received.includes(i)) { setPct(id, ((i + 1) / total) * 100); continue; }
      const blob = file.slice(i * CHUNK, Math.min((i + 1) * CHUNK, file.size));
      await withRetry(3, () => fetch(`/api/upload/chunk/${session}?index=${i}`, { method: 'POST', body: blob }).then(r => { if (!r.ok) throw new Error('chunk ' + i); return r; }));
      setPct(id, ((i + 1) / total) * 100);
      setStatus(id, '上传中 ' + Math.round(((i + 1) / total) * 100) + '%');
    }
    await fetch(`/api/upload/finish/${session}`, { method: 'POST' }).then(r => { if (!r.ok) throw new Error('finish failed'); });
    delete sessions[key];
    setStatus(id, '✅ 已传到电脑', 'ok');
  } catch (e) {
    setStatus(id, '❌ ' + e.message + '（重选可续传）', 'err');
  }
}

async function handleFiles(files) {
  for (const f of files) await uploadFile(f);
}

const drop = document.getElementById('drop');
const input = document.getElementById('file-input');
drop.addEventListener('click', () => input.click());
input.addEventListener('change', async e => { await handleFiles([...e.target.files]); input.value = ''; });
document.body.addEventListener('dragover', e => { e.preventDefault(); drop.classList.add('dragover'); });
document.body.addEventListener('dragleave', e => { if (!e.relatedTarget) drop.classList.remove('dragover'); });
document.body.addEventListener('drop', async e => { e.preventDefault(); drop.classList.remove('dragover'); await handleFiles([...e.dataTransfer.files]); });

checkAuth();
