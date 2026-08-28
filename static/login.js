// LAN Share 登录/初始化页（CSP: script-src 'self'）
let needsSetup = false;
const $ = id => document.getElementById(id);

async function init() {
  try {
    const r = await fetch('/api/auth/state');
    const s = await r.json();
    if (s.logged_in) { location.href = '/'; return; }
    needsSetup = s.needs_setup;
    if (needsSetup) {
      $('title').textContent = '初始化管理员';
      $('sub').textContent = '首次使用，请创建管理员账号';
      $('pwd-label').textContent = '设置密码（至少 8 位）';
      $('btn').textContent = '创建并登录';
    }
  } catch { $('error').textContent = '无法连接服务器'; }
}

async function submit() {
  const btn = $('btn');
  btn.disabled = true;
  $('error').textContent = '';
  try {
    const body = { username: $('username').value.trim(), password: $('password').value };
    if (needsSetup && body.password.length < 8) { $('error').textContent = '密码至少 8 位'; return; }
    const r = await fetch(needsSetup ? '/api/setup' : '/api/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    });
    if (r.ok) {
      const next = new URLSearchParams(location.search).get('next');
      // 只允许站内路径，排除 // 开头的协议相对地址（开放重定向）
      location.href = next && next.startsWith('/') && !next.startsWith('//') ? next : '/';
      return;
    }
    const e = await r.json().catch(() => ({ detail: '登录失败' }));
    $('error').textContent = e.detail || '登录失败';
  } catch { $('error').textContent = '网络错误'; }
  finally { btn.disabled = false; }
}

$('btn').addEventListener('click', submit);
document.addEventListener('keydown', e => { if (e.key === 'Enter') submit(); });
init();
