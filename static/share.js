// 分享取件页（CSP: script-src 'self'）
const token = window.location.pathname.split('/s/')[1];
let needsPassword = false;

// 扩展名 → 徽章配色（与主界面文件类型徽章同一套配色）
function getExt(name) {
  const map = {'.apk':'ft-apk','.pdf':'ft-doc','.mp4':'ft-vid','.mkv':'ft-vid','.mov':'ft-vid','.zip':'ft-zip','.rar':'ft-zip','.7z':'ft-zip','.jpg':'ft-img','.jpeg':'ft-img','.png':'ft-img','.gif':'ft-img','.webp':'ft-img','.mp3':'ft-aud','.wav':'ft-aud','.flac':'ft-aud','.txt':'ft-doc','.md':'ft-doc'};
  return map[(name.split('.').pop()||'').toLowerCase()] || 'ft-other';
}

function showError(msg) {
  document.getElementById('error').textContent = msg;
  document.getElementById('error').style.display = 'block';
  document.getElementById('dl-btn').disabled = true;
}

async function load() {
  try {
    const res = await fetch(`/api/share/meta/${token}`);
    if (res.status === 410) { showError('链接已过期'); return; }
    if (!res.ok) { showError('链接无效或已过期'); return; }
    const s = await res.json();
    document.getElementById('filename').textContent = s.name;
    const icon = document.getElementById('icon');
    icon.className = 'ft-badge ' + getExt(s.name);
    icon.textContent = (s.name.split('.').pop() || 'FILE').toUpperCase().slice(0, 4);
    const bits = [];
    if (s.size_human && s.size_human !== '-') bits.push(s.size_human);
    if (s.expires_in) bits.push(`${s.expires_in}后过期`);
    if (s.needs_password) bits.push('🔒 需提取码');
    document.getElementById('meta').textContent = bits.join(' · ');
    needsPassword = s.needs_password;
    if (needsPassword) {
      document.getElementById('pwd-section').style.display = 'block';
      document.getElementById('pwd').focus();
    }
    document.getElementById('dl-btn').disabled = false;
  } catch {
    showError('网络错误，请检查连接');
  }
}

async function download() {
  const pwd = document.getElementById('pwd')?.value || '';
  if (needsPassword && !pwd) { showError('请输入提取码'); return; }
  const btn = document.getElementById('dl-btn');
  btn.disabled = true;
  try {
    // 密码走 POST body，不出现在 URL（避免进访问日志）；
    // 验证通过后换短时效下载票据，再用 <a> 触发浏览器原生下载（大文件不进内存）
    const res = await fetch(`/api/share/${token}/prepare`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password: pwd || null })
    });
    if (res.status === 401) { showError('此文件需要提取码'); return; }
    if (res.status === 403) { showError('提取码错误'); return; }
    if (res.status === 410) { showError((await res.json().catch(() => ({}))).detail || '链接已过期'); return; }
    if (res.status === 429) { showError((await res.json().catch(() => ({}))).detail || '尝试过于频繁'); return; }
    if (!res.ok) { showError((await res.json().catch(() => ({}))).detail || '下载失败，请稍后重试'); return; }
    const r = await res.json();
    const a = document.createElement('a');
    a.href = r.url;
    a.download = r.name;
    a.style.display = 'none';
    document.body.appendChild(a);
    a.click();
    setTimeout(() => a.remove(), 200);
  } catch {
    showError('网络错误');
  } finally {
    btn.disabled = false;
  }
}

document.getElementById('dl-btn').addEventListener('click', download);
document.getElementById('pwd').addEventListener('keydown', e => { if (e.key === 'Enter') download(); });
load();
