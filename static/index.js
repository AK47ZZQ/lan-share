// LAN Share 主界面逻辑（CSP: script-src 'self' —— 所有交互经事件委托分发，无内联处理器）
let currentPath='',currentFiles=[],selectedPaths=new Set(),ctxMenu=null,selectMode=false;
let sortKey='name',sortAsc=true,activeTab='files',activeFilter='all',searchQuery='';
let uploadSessions={};  // 分块上传会话：key = dir/name/size → session id（页面内断点续传）

// ─── SVG 图标 ───
const SVG={
  home:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m3 9 9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><path d="M9 22V12h6v10"/></svg>',
  folder:'<svg viewBox="0 0 24 24" fill="currentColor"><path d="M4 20h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13c0 1.1.9 2 2 2Z"/></svg>',
  open:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 14l1.5-2.9A2 2 0 0 1 9.24 10H20a2 2 0 0 1 1.94 2.5l-1.54 6a2 2 0 0 1-1.95 1.5H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h3.9a2 2 0 0 1 1.69.9l.81 1.2a2 2 0 0 0 1.67.9H18a2 2 0 0 1 2 2v2"/></svg>',
  download:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m7 10 5 5 5-5"/><path d="M12 15V3"/></svg>',
  eye:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/></svg>',
  share:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>',
  rename:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/></svg>',
  trash:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>',
  inbox:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M22 12h-6l-2 3h-4l-2-3H2"/><path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/></svg>',
  image:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-3.1-3.1a2 2 0 0 0-2.8 0L6 21"/></svg>',
  clock:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></svg>',
};

// ─── API ───
async function api(method,url,body){
  const o={method,headers:{}};
  if(body instanceof FormData)o.body=body;
  else if(body){o.headers['Content-Type']='application/json';o.body=JSON.stringify(body)}
  const r=await fetch(url,o);
  if(r.status===401){location.href='/login';throw new Error('请先登录')}
  if(!r.ok){const e=await r.json().catch(()=>({detail:r.statusText}));throw new Error(e.detail||'Error')}
  return r;
}

async function logout(){
  try{await fetch('/api/logout',{method:'POST'})}catch{}
  location.href='/login';
}

// ─── Load ───
async function loadDir(path){
  currentPath=path;renderBackBtn();renderBreadcrumb();
  showLoading();
  try{
    const r=await(await api('GET',`/api/files/${path}`)).json();
    currentFiles=r.items;applySortAndFilter();renderFiles(true);
  }catch(e){
    document.getElementById('file-list').innerHTML=`<div class="empty"><div class="e-ico">${SVG.inbox}</div><div class="title">加载失败</div><div class="hint">${esc(e.message)}</div></div>`;
  }
}
async function loadRecent(){
  showLoading();
  try{
    const r=await(await api('GET','/api/recent?limit=30')).json();
    currentFiles=r.map(f=>({...f,is_dir:false,path:f.path}));
    renderFiles(true);
  }catch(e){document.getElementById('file-list').innerHTML=`<div class="empty"><div class="e-ico">${SVG.clock}</div><div class="title">${esc(e.message)}</div></div>`}
}
async function loadDisk(){
  try{const r=await(await api('GET','/api/disk')).json();const e=document.getElementById('disk-info');e.textContent=r.free;e.className='badge'+(r.warning?' warn':'')}catch{}
}
function showLoading(){
  const n='<div class="skel-i"><div class="skel-icon"></div><div class="skel-lines"><div class="skel-line"></div><div class="skel-line"></div></div></div>';
  document.getElementById('file-list').innerHTML=`<div class="skel">${n}${n}${n}</div>`;
}

// ─── Tabs ───
let photoOffset=0,photoItems=[],photoTotal=0;
function switchTab(t){
  activeTab=t;
  document.querySelectorAll('.tab').forEach(e=>e.classList.toggle('active',e.dataset.tab===t));
  const fb=document.getElementById('filter-bar');
  const sb=document.getElementById('sub-bar');
  if(t==='recent'){fb.classList.remove('show');sb.style.display='none';loadRecent();clearSearch()}
  else if(t==='photos'){fb.classList.remove('show');sb.style.display='none';clearSearch(true);photoOffset=0;photoItems=[];loadPhotos()}
  else{sb.style.display='';loadDir(currentPath)}
}
async function loadPhotos(){
  const el=document.getElementById('file-list');
  if(!photoItems.length)el.innerHTML='<div class="skel"><div class="skel-i"><div class="skel-icon"></div><div class="skel-lines"><div class="skel-line"></div><div class="skel-line"></div></div></div></div>';
  try{
    const r=await(await api('GET',`/api/photos?limit=120&offset=${photoOffset}`)).json();
    photoTotal=r.total;
    photoItems=photoItems.concat(r.items);
    photoOffset+=r.items.length;
    if(!photoItems.length){
      el.innerHTML='<div class="empty"><div class="e-ico">'+SVG.image+'</div><div class="title">还没有照片</div><div class="hint">图片文件（jpg/png/webp/bmp）会出现在这里，按拍摄日期排列</div></div>';
      return;
    }
    let html='',lastDate=null;
    for(const p of photoItems){
      if(p.taken!==lastDate){
        if(lastDate!==null)html+='</div>';
        html+=`<div class="photo-date">${p.taken}</div><div class="photo-grid">`;
        lastDate=p.taken;
      }
      html+=`<img src="${p.thumb}" loading="lazy" data-act="preview" data-p="${esc(p.path)}" data-n="${esc(p.name)}">`;
    }
    html+='</div>';
    if(photoOffset<photoTotal)html+=`<button class="photo-more" data-act="loadMorePhotos">加载更多（还有 ${photoTotal-photoOffset} 张）</button>`;
    else html+=`<div style="text-align:center;padding:16px;font-size:12px;color:var(--text2)">共 ${photoTotal} 张照片</div>`;
    el.innerHTML=html;
  }catch(e){
    el.innerHTML=`<div class="empty"><div class="e-ico">${SVG.inbox}</div><div class="title">${esc(e.message)}</div></div>`;
  }
}

// ─── Sort ───
const sortLabels={name:'名称',size:'大小',modified:'日期'};
function cycleSort(){
  const keys=['name','size','modified'];
  const i=keys.indexOf(sortKey);
  if(i<keys.length-1){sortKey=keys[i+1];sortAsc=true}else if(sortAsc){sortAsc=false}else{sortKey='name';sortAsc=true}
  document.getElementById('sort-btn').textContent=`↕ ${sortLabels[sortKey]}${sortAsc?' ↑':' ↓'}`;
  applySortAndFilter();renderFiles();
}
function applySortAndFilter(){
  let items=[...currentFiles];
  if(activeFilter!=='all')items=items.filter(f=>{
    if(activeFilter==='folder')return f.is_dir;
    if(activeFilter==='image')return /\.(jpg|jpeg|png|gif|webp|bmp|svg)$/i.test(f.name);
    if(activeFilter==='video')return /\.(mp4|mkv|mov|avi|wmv|flac|webm)$/i.test(f.name);
    if(activeFilter==='doc')return /\.(pdf|doc|docx|xls|xlsx|ppt|pptx|txt|md|json|yaml|yml)$/i.test(f.name);
    if(activeFilter==='archive')return /\.(zip|rar|7z|tar|gz|bz2)$/i.test(f.name);
    if(activeFilter==='apk')return /\.apk$/i.test(f.name);
    return true;
  });
  items.sort((a,b)=>{
    if(a.is_dir!==b.is_dir)return a.is_dir?-1:1;
    let va=a[sortKey]||'',vb=b[sortKey]||'';
    if(sortKey==='size'){va=a.size||0;vb=b.size||0}
    if(sortKey==='modified'){va=a.modified;vb=b.modified}
    if(typeof va==='string')return sortAsc?va.localeCompare(vb):vb.localeCompare(va);
    return sortAsc?va-vb:vb-va;
  });
  currentFiles=items;
}
function setFilter(f){
  activeFilter=f;
  document.querySelectorAll('.chip').forEach(e=>e.classList.toggle('active',e.dataset.filter===f));
  applySortAndFilter();renderFiles();
}

// ─── Search ───
function toggleSearch(){
  const bar=document.getElementById('search-bar');
  bar.classList.toggle('show');
  if(bar.classList.contains('show'))document.getElementById('search-input').focus();
}
let searchTimer=null;
function doSearch(q){
  clearTimeout(searchTimer);
  if(!q){clearSearch();return}
  searchTimer=setTimeout(async()=>{
    showLoading();
    try{
      const r=await(await api('GET',`/api/search?q=${encodeURIComponent(q)}`)).json();
      currentFiles=r.map(f=>({...f,is_dir:false}));renderFiles();
    }catch(e){toast(e.message,true)}
  },300);
}
function clearSearch(silent){
  document.getElementById('search-input').value='';
  document.getElementById('search-bar').classList.remove('show');
  if(activeTab==='files'&&!silent)loadDir(currentPath);
}

// ─── Render ───
function renderBackBtn(){document.getElementById('back-btn').classList.toggle('show',currentPath!==''&&activeTab==='files')}
function renderBreadcrumb(){
  const el=document.getElementById('breadcrumb');
  const parts=currentPath?currentPath.split('/'):[];
  let h=`<span data-act="loadDir" data-p="">${SVG.home}</span>`;
  let acc='';
  for(const p of parts){acc+=(acc?'/':'')+p;const pp=acc;h+=`<span class="sep">›</span><span data-act="loadDir" data-p="${esc(pp)}">${esc(p)}</span>`}
  el.innerHTML=h;
}

// 文件类型徽章：分类配色 + 扩展名标签（替代 emoji，风格统一）
const FT_CATS={
  image:{cls:'ft-img',label:e=>(e||'IMG').slice(0,4)},
  video:{cls:'ft-vid',label:e=>(e||'VID').slice(0,4)},
  audio:{cls:'ft-aud',label:e=>(e||'AUD').slice(0,4)},
  apk:{cls:'ft-apk',label:()=>'APK'},
  archive:{cls:'ft-zip',label:e=>(e||'ZIP').slice(0,4)},
  doc:{cls:'ft-doc',label:e=>(e||'DOC').slice(0,4)},
  code:{cls:'ft-code',label:e=>(e||'<>').slice(0,4)},
  other:{cls:'ft-other',label:e=>(e||'FILE').slice(0,4)},
};
function ftCat(f){
  const e=(f.ext||'').toLowerCase();
  if(f.is_dir)return null;
  if(/\.(jpg|jpeg|png|gif|webp|bmp|svg|ico|heic)$/.test(e))return FT_CATS.image;
  if(/\.(mp4|mkv|mov|avi|wmv|webm|flv|ts)$/.test(e))return FT_CATS.video;
  if(/\.(mp3|wav|flac|ogg|m4a|aac)$/.test(e))return FT_CATS.audio;
  if(/\.apk$/.test(e))return FT_CATS.apk;
  if(/\.(zip|rar|7z|tar|gz|bz2|iso)$/.test(e))return FT_CATS.archive;
  if(/\.(pdf|doc|docx|xls|xlsx|ppt|pptx|txt|md|csv)$/.test(e))return FT_CATS.doc;
  if(/\.(js|ts|py|json|yaml|yml|html|css|sh|bat|c|cpp|java|go|rs)$/.test(e))return FT_CATS.code;
  return FT_CATS.other;
}
function typeBadge(f){
  if(f.is_dir)return `<div class="ft-icon ft-folder">${SVG.folder}</div>`;
  const cat=ftCat(f);
  const ext=(f.ext||'').replace('.','');
  // 图片/视频直接上真实缩略图（放徽章容器内），加载失败由捕获委托回退为扩展名标签
  if(cat===FT_CATS.image||cat===FT_CATS.video){
    return `<div class="ft-icon ${cat.cls} ft-media"><img class="file-thumb" src="/thumb/${f.path}" loading="lazy" data-ext="${esc(ext)}"></div>`;
  }
  return `<div class="ft-icon ${cat.cls}">${cat.label(ext).toUpperCase()}</div>`;
}

function renderFiles(stagger){
  const el=document.getElementById('file-list');
  const ua=document.getElementById('upload-area');
  const ct=document.getElementById('file-count');
  const dirs=currentFiles.filter(f=>f.is_dir).length;
  const files=currentFiles.filter(f=>!f.is_dir).length;
  const ps=[];if(dirs)ps.push(`${dirs} 文件夹`);if(files)ps.push(`${files} 文件`);
  ct.textContent=ps.length?ps.join(' · '):(activeTab==='recent'?'最近文件':'');
  if(!currentFiles.length){
    el.innerHTML=`<div class="empty"><div class="e-ico">${SVG.inbox}</div><div class="title">${activeTab==='recent'?'暂无文件':'空空如也'}</div><div class="hint">${activeTab==='recent'?'还没有上传过文件':'点击下方「上传」添加文件'}</div></div>`;
    ua.style.display=activeTab==='files'?'block':'none';return;
  }
  ua.style.display='none';
  el.className='file-list'+(selectMode?' selecting':'');
  el.innerHTML=currentFiles.map((f,idx)=>{
    const icon=typeBadge(f);
    const canPreview=!f.is_dir&&/\.(jpg|jpeg|png|gif|webp|mp4|mkv|mov|webm|mp3|wav|flac|ogg|m4a|pdf|txt|md|json|yaml|yml|csv|log)$/i.test(f.name);
    const act=selectMode?'toggleSelect':(f.is_dir?'loadDir':(canPreview?'preview':'downloadFile'));
    const dataAttrs=`data-act="${act}" data-p="${esc(f.path)}" data-n="${esc(f.name)}" data-d="${f.is_dir?1:0}"`;
    const longPress=activeTab==='files'&&!selectMode?` data-press="1" data-p="${esc(f.path)}" data-d="${f.is_dir?1:0}"`:'';
    const selected=selectedPaths.has(f.path)?' selected':'';
    const delay=stagger?` style="animation-delay:${Math.min(idx*22,240)}ms"`:'';
    return `<div class="file-card${f.is_dir?' folder':''}${selected}" ${dataAttrs}${longPress}${delay}>
      <div class="file-check">✓</div>
      ${icon}
      <div class="file-info">
        <div class="file-name">${esc(f.name)}</div>
        <div class="file-meta">
          <span class="tag">${f.is_dir?'文件夹':f.size_human}</span>
          <span>${formatDate(f.modified)}</span>
          ${canPreview&&!selectMode?'<span class="tag" style="color:var(--accent)">预览</span>':''}
        </div>
      </div>
      <div class="file-arrow">${f.is_dir?'›':'›'}</div>
    </div>`;
  }).join('');
  if(stagger){el.classList.add('fresh');setTimeout(()=>el.classList.remove('fresh'),650)}
}

function formatDate(i){const d=new Date(i),now=new Date(),df=now-d;if(df<60000)return'刚刚';if(df<3600000)return`${Math.floor(df/60000)}分钟前`;if(df<86400000)return`${Math.floor(df/3600000)}小时前`;if(df<604800000)return`${Math.floor(df/86400000)}天前`;return d.toLocaleDateString('zh-CN')}
function esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;')}

// ─── Navigation ───
function goBack(){if(!currentPath)return;const p=currentPath.split('/');p.pop();loadDir(p.join('/'))}
window.addEventListener('popstate',()=>{if(activeTab==='files')loadDir(decodeURIComponent(location.hash.slice(1)))});

// ─── Long press（触屏长按出菜单，触摸事件委托）───
let pressTimer=null;
function showCtx(x,y,path,isDir){
  removeCtx();
  const mw=190,mh=isDir?130:230;
  if(x+mw>window.innerWidth)x=window.innerWidth-mw-10;
  if(y+mh>window.innerHeight)y=window.innerHeight-mh-10;
  const it=(act,p,extra,icon,label,danger)=>`<div class="ctx-item${danger?' danger':''}" data-act="${act}" data-p="${esc(p)}"${extra}><span class="ci">${icon}</span>${label}</div>`;
  const items=isDir
    ?it('loadDir',path,'',SVG.open,'打开')+it('showRenameModal',path,'',SVG.rename,'重命名')+'<div class="ctx-sep"></div>'+it('deleteItem',path,' data-d="1"',SVG.trash,'删除',true)
    :it('downloadFile',path,'',SVG.download,'下载')+it('preview',path,` data-n="${esc(path.split('/').pop())}"`,SVG.eye,'预览')+it('shareFile',path,'',SVG.share,'分享')+it('showRenameModal',path,'',SVG.rename,'重命名')+'<div class="ctx-sep"></div>'+it('deleteItem',path,' data-d="0"',SVG.trash,'删除',true);
  ctxMenu=document.createElement('div');ctxMenu.className='ctx-menu';ctxMenu.style.left=x+'px';ctxMenu.style.top=y+'px';ctxMenu.innerHTML=items;document.body.appendChild(ctxMenu);
  setTimeout(()=>document.addEventListener('click',removeCtx,{once:true}),10);
}
function removeCtx(){if(ctxMenu){ctxMenu.remove();ctxMenu=null}}

// ─── Select mode ───
function enterSelectMode(){selectMode=true;selectedPaths.clear();document.getElementById('action-bar').style.display='none';document.getElementById('batch-bar').classList.add('show');syncBatchUI();renderFiles()}
function exitSelectMode(){selectMode=false;selectedPaths.clear();document.getElementById('action-bar').style.display='';document.getElementById('batch-bar').classList.remove('show');renderFiles()}
function toggleSelect(path){selectedPaths.has(path)?selectedPaths.delete(path):selectedPaths.add(path);syncBatchUI();renderFiles()}
function toggleSelectAll(){
  // 全选/全不选切换：当前目录已全选则清空，否则全选
  const all=currentFiles.length>0&&currentFiles.every(f=>selectedPaths.has(f.path));
  if(all)selectedPaths.clear();else currentFiles.forEach(f=>selectedPaths.add(f.path));
  syncBatchUI();renderFiles();
}
function syncBatchUI(){
  document.getElementById('batch-n').textContent=selectedPaths.size;
  const all=currentFiles.length>0&&currentFiles.every(f=>selectedPaths.has(f.path));
  const b=document.getElementById('selall-btn');
  if(b)b.textContent=all?'全不选':'全选';
}
async function batchDelete(){
  if(!selectedPaths.size)return;
  if(!confirm(`确定删除 ${selectedPaths.size} 个文件？将移入回收站`))return;
  let ok=0,fail=0;
  for(const p of selectedPaths){try{await api('DELETE',`/api/files/${p}`);ok++}catch{fail++}}
  toast(`${ok} 个已移入回收站`+(fail?`, ${fail} 个失败`:''));exitSelectMode();loadDir(currentPath);
}
async function batchDownload(){
  const paths=[...selectedPaths];
  if(paths.length===1){downloadFile(paths[0]);return}
  // 多选走服务端打包 zip（大文件不进内存，浏览器原生下载）
  toast('📦 正在打包...');
  try{
    const r=await(await api('POST','/api/zip',{paths})).json();
    const a=document.createElement('a');
    a.href=r.url;a.download=r.name;a.style.display='none';
    document.body.appendChild(a);a.click();setTimeout(()=>a.remove(),200);
  }catch(e){toast(e.message,true)}
}

// ─── Preview ───
function previewFile(path,name){
  const isImg=/\.(jpg|jpeg|png|gif|webp|bmp|svg)$/i.test(name);
  const isVid=/\.(mp4|mkv|mov|webm|avi)$/i.test(name);
  const isAud=/\.(mp3|wav|flac|ogg|m4a)$/i.test(name);
  const isPdf=/\.pdf$/i.test(name);
  const isTxt=/\.(txt|md|json|yaml|yml|csv|log|py|js|ts|html|css)$/i.test(name);
  // 预览统一走 /preview/ 端点（inline），不走 /files/（attachment）
  const previewUrl='/preview/'+path;
  let inner='';
  if(isImg)inner=`<img src="${previewUrl}" alt="${esc(name)}" style="max-width:100%;max-height:80vh;border-radius:10px">`;
  else if(isVid)inner=`<video src="${previewUrl}" controls playsinline style="max-width:100%;max-height:80vh;border-radius:10px"></video>`;
  else if(isAud)inner=`<div style="text-align:center"><audio src="${previewUrl}" controls playsinline></audio></div>`;
  else if(isPdf)inner=`<iframe src="${previewUrl}" style="width:100%;height:80vh;border:none;border-radius:10px;background:#fff"></iframe>`;
  else if(isTxt)inner=`<div style="width:100%;max-width:700px;background:#1a2032;border-radius:12px;padding:16px;max-height:80vh;overflow:auto;font-family:monospace;font-size:13px;white-space:pre-wrap;color:#c8cde0" id="preview-text"></div>`;
  else inner=`<div style="text-align:center;color:var(--text2);padding:40px"><div class="e-ico" style="margin:0 auto 14px">${SVG.inbox}</div>不支持预览此文件类型<br><small>请下载后查看</small></div>`;

  const overlay=document.createElement('div');overlay.className='preview-overlay';
  overlay.innerHTML=`<button class="close-btn" data-act="closePreview">✕</button>${inner}<div class="preview-name">${esc(name)}</div><div class="preview-ops"><button style="background:var(--accent-grad);color:#fff" data-act="downloadFile" data-p="${esc(path)}">下载</button><button style="background:rgba(255,255,255,.1);color:var(--text)" data-act="shareFile" data-p="${esc(path)}">分享</button></div>`;
  document.body.appendChild(overlay);
  overlay.addEventListener('click',(e)=>{if(e.target===overlay)overlay.remove()});
  if(isTxt){fetch(previewUrl).then(r=>r.text()).then(t=>{const el=document.getElementById('preview-text');if(el)el.textContent=t}).catch(()=>{})}
}

// ─── Actions ───
function downloadFile(path){
  const a=document.createElement('a');
  a.href='/files/'+path;
  a.download=path.split('/').pop();
  a.style.display='none';
  document.body.appendChild(a);
  a.click();
  setTimeout(()=>a.remove(),100);
}
async function deleteItem(path,isDir){
  const name=path.split('/').pop();
  if(!confirm(`确定删除「${name}」？将移入回收站，保留 30 天`))return;
  try{await api('DELETE',`/api/files/${path}`);toast('已移入回收站');loadDir(currentPath)}catch(e){toast(e.message,true)}
}
function triggerUpload(){document.getElementById('file-input').click()}
document.getElementById('file-input').addEventListener('change',async(e)=>{
  for(const f of e.target.files)await uploadFile(f);
  e.target.value='';loadDir(currentPath);loadDisk();
});

async function uploadFile(file){
  const CHUNK=5*1024*1024;
  const dir=currentPath;
  showProgress(file.name,0);
  const initReq=(sha)=>api('POST','/api/upload/init',{dest_dir:dir,filename:file.name,size:file.size,sha256:sha}).then(r=>r.json());
  try{
    // 秒传预检：≤100MB 时本地算 SHA256（服务端 finish 时仍会校验，哈希不匹配会被拒）
    let sha=null;
    if(file.size<=100*1024*1024&&file.size>0){
      try{sha=await sha256Hex(file)}catch{sha=null}
    }
    // 分块上传 + 断点续传（先查本页会话，再 init——init 同时承担秒传判定）
    const key=`up:${dir}/${file.name}:${file.size}`;
    let session=null,received=[];
    if(uploadSessions[key]){
      const st=await api('GET',`/api/upload/state/${uploadSessions[key]}`).then(r=>r.json()).catch(()=>null);
      if(st){session=st.session;received=st.received}else delete uploadSessions[key];
    }
    if(!session){
      const init=await initReq(sha);
      if(init.instant){setProgress(100);toast(`⚡ ${file.name} 秒传完成（内容已存在）`);return}
      session=init.session;received=init.received||[];uploadSessions[key]=session;
    }
    const total=Math.ceil(file.size/CHUNK);
    for(let i=0;i<total;i++){
      if(received.includes(i)){setProgress(((i+1)/total)*100);continue}
      const blob=file.slice(i*CHUNK,Math.min((i+1)*CHUNK,file.size));
      await withRetry(3,()=>fetch(`/api/upload/chunk/${session}?index=${i}`,{method:'POST',body:blob}).then(r=>{
        if(!r.ok)throw new Error(`分块 ${i} 上传失败(${r.status})`);return r}));
      setProgress(((i+1)/total)*100);
    }
    await api('POST',`/api/upload/finish/${session}`);
    delete uploadSessions[key];
    toast(`✅ ${file.name} 上传完成`);
  }catch(e){
    toast(`❌ ${e.message} —— 保留进度，重新选择该文件即可续传`,true);
  }
  hideProgress();
}
async function withRetry(n,fn){let err;for(let i=0;i<n;i++){try{return await fn()}catch(e){err=e;await new Promise(r=>setTimeout(r,800*(i+1)))}}throw err}
async function sha256Hex(file){
  const buf=await file.arrayBuffer();
  const h=await crypto.subtle.digest('SHA-256',buf);
  return [...new Uint8Array(h)].map(b=>b.toString(16).padStart(2,'0')).join('');
}

// Progress bar
function showProgress(name,pct){const w=document.getElementById('progress-wrap');w.classList.add('show');document.getElementById('progress-file').textContent=`${name}`;setProgress(pct)}
function setProgress(pct){document.getElementById('progress-fill').style.width=pct+'%'}
function hideProgress(){document.getElementById('progress-wrap').classList.remove('show');document.getElementById('progress-fill').style.width='0'}

// Drag & drop
document.body.addEventListener('dragover',(e)=>{e.preventDefault();document.getElementById('upload-area').classList.add('dragover');document.getElementById('upload-area').style.display='block'});
document.body.addEventListener('dragleave',(e)=>{if(!e.relatedTarget)document.getElementById('upload-area').classList.remove('dragover')});
document.body.addEventListener('drop',async(e)=>{
  e.preventDefault();document.getElementById('upload-area').classList.remove('dragover');
  for(const f of e.dataTransfer.files)await uploadFile(f);
  loadDir(currentPath);loadDisk();
});

// ─── Shares ───
async function showShares(){
  try{
    const r=await(await api('GET','/api/shares')).json();
    const tokens=Object.keys(r);
    let html=`<h3>🔗 活跃分享 (${tokens.length})</h3>`;
    if(!tokens.length){html+=`<div class="empty" style="padding:24px"><div class="e-ico">${SVG.share}</div><div class="title">暂无分享</div><div class="hint">长按文件或点击文件旁的分享按钮创建</div></div>`}
    else{
      html+=`<div class="share-list">`;
      for(const t of tokens){
        const s=r[t];
        const dl=s.max_downloads?`${s.downloads}/${s.max_downloads} 次`:`${s.downloads} 次`;
        html+=`<div class="share-item"><div class="si-info"><div class="si-name">${esc(s.name)}</div><div class="si-meta">${s.size_human} · ${s.expires_in}后过期 · 已取 ${dl}${s.has_password?' · 🔒':''}</div></div><button class="si-revoke blue" data-act="showShareHistory" data-t="${esc(t)}">记录</button><button class="si-revoke" data-act="revokeShare" data-t="${esc(t)}">撤销</button></div>`;
      }
      html+=`</div>`;
    }
    html+=`<div class="actions" style="margin-top:12px"><button class="btn btn-ghost" data-act="closeModal">关闭</button></div>`;
    showModal(html);
  }catch(e){toast(e.message,true)}
}
async function revokeShare(token){
  if(!confirm('确定撤销此分享？'))return;
  try{await api('DELETE',`/api/shares/${token}`);toast('已撤销');showShares()}catch(e){toast(e.message,true)}
}

// ─── Disk detail ───
async function showDiskDetail(){
  try{
    const r=await(await api('GET','/api/disk/detail')).json();
    const cats=r.categories;const total=r.used;
    const colors=['#6d9cff','#34d399','#fb923c','#fb7185','#a78bfa','#fbbf24','#22d3ee','#f472b6'];
    let segmentsHtml='',legendHtml='',i=0;
    const sorted=Object.entries(cats).sort((a,b)=>b[1].size-a[1].size);
    for(const [name,data] of sorted){
      const pct=total>0?(data.size/total*100):0;
      const c=colors[i%colors.length];i++;
      if(pct>0.5)segmentsHtml+=`<div style="width:${pct}%;background:${c};height:100%;min-width:3px" title="${esc(name)} ${data.size_human}"></div>`;
      legendHtml+=`<div class="disk-legend-item"><div style="width:10px;height:10px;border-radius:3px;background:${c};flex-shrink:0"></div><span style="color:var(--text2);flex:1">${esc(name)}</span><span style="font-weight:600">${data.size_human}</span></div>`;
    }
    const usedPct=total>0?(r.used/total*100).toFixed(1):'0';
    showModal(`<h3>💾 磁盘用量</h3>
      <div style="text-align:center;padding:8px 0 16px">
        <div style="font-size:32px;font-weight:800;letter-spacing:-.5px">${r.free_human}</div>
        <div style="color:var(--text2);font-size:13px;margin-top:4px">可用 / ${r.total_human} 总计 · 已用 ${usedPct}%</div>
      </div>
      <div style="height:14px;background:var(--surface3);border-radius:7px;overflow:hidden;display:flex;margin-bottom:18px">
        ${segmentsHtml}
      </div>
      <div class="disk-legend">
        ${legendHtml}
      </div>
      <div style="margin-top:16px"><button class="btn btn-ghost" style="width:100%;justify-content:center;display:flex;padding:10px;border-radius:12px" data-act="closeModal">关闭</button></div>`);
  }catch(e){toast(e.message,true)}
}

// ─── Mkdir ───
function showMkdirModal(){
  showModal(`<h3>📁 新建文件夹</h3><input type="text" id="mkdir-name" placeholder="输入文件夹名称"><div class="actions"><button class="btn btn-ghost" data-act="closeModal">取消</button><button class="btn btn-primary" data-act="doMkdir">创建</button></div>`);
  const inp=document.getElementById('mkdir-name');inp.focus();
  inp.addEventListener('keydown',(e)=>{if(e.key==='Enter')doMkdir()});
}
async function doMkdir(){
  const name=document.getElementById('mkdir-name').value.trim();if(!name)return;
  closeModal();const p=currentPath?`${currentPath}/${name}`:name;
  try{await api('POST',`/api/mkdir/${p}`);toast('✅ 已创建');loadDir(currentPath)}catch(e){toast(e.message,true)}
}

// ─── Share ───
async function shareFile(path){
  const name=path.split('/').pop();
  showModal(`<h3>🔗 分享「${esc(name)}」</h3><label>有效期（小时）</label><input type="number" id="share-hours" value="72" min="1" max="720"><label>提取码（可选）</label><input type="text" id="share-pwd" placeholder="留空则无需提取码"><label>下载次数上限（可选）</label><input type="number" id="share-maxdl" placeholder="留空则不限次数" min="1"><div class="actions"><button class="btn btn-ghost" data-act="closeModal">取消</button><button class="btn btn-primary" data-act="doShare" data-p="${esc(path)}">生成链接</button></div>`);
  document.getElementById('share-hours').focus();
}
async function doShare(path){
  const hours=parseInt(document.getElementById('share-hours').value)||72;
  const pwd=document.getElementById('share-pwd').value||null;
  const maxdl=parseInt(document.getElementById('share-maxdl').value)||null;
  closeModal();
  try{
    const form=new FormData();form.append('path',path);form.append('expires_hours',hours);if(pwd)form.append('password',pwd);if(maxdl)form.append('max_downloads',maxdl);
    const r=await(await api('POST','/api/share',form)).json();
    const url=`${location.origin}/s/${r.token}`;
    showModal(`<h3>✅ 分享已创建</h3><div class="share-result"><img class="qr" src="/api/share/${r.token}/qr" alt="QR"><div class="url" data-act="copy" data-v="${esc(url)}">${url}</div><p style="margin-top:8px;font-size:11px;color:var(--text2)">有效期 ${r.expires_in}${maxdl?` · 上限 ${maxdl} 次下载`:''}</p></div><div class="actions" style="justify-content:center;margin-top:8px"><button class="btn btn-primary" data-act="copy" data-v="${esc(url)}">📋 复制链接</button><button class="btn btn-ghost" data-act="closeModal">关闭</button></div>`);
  }catch(e){toast(e.message,true)}
}

// ─── Share history ───
async function showShareHistory(token){
  try{
    const list=await(await api('GET',`/api/shares/${token}/history`)).json();
    let html=`<h3>📋 取件记录</h3>`;
    if(!list.length){html+=`<div class="empty" style="padding:24px"><div class="e-ico">${SVG.clock}</div><div class="title">还没有取件</div></div>`}
    else{
      html+=`<div class="share-list">`;
      for(const h of list){
        const ok=h.action==='share_download';
        html+=`<div class="share-item"><div class="si-info"><div class="si-name">${ok?'✅ 下载成功':'⚠️ 提取码错误'}</div><div class="si-meta">${h.ts.replace('T',' ').slice(0,19)} · ${esc(h.ip||'未知')}</div></div></div>`;
      }
      html+=`</div>`;
    }
    html+=`<div class="actions" style="margin-top:12px"><button class="btn btn-ghost" data-act="showShares">返回</button><button class="btn btn-ghost" data-act="closeModal">关闭</button></div>`;
    showModal(html);
  }catch(e){toast(e.message,true)}
}

// ─── Phone upload QR ───
async function showPhoneQR(){
  const url=`${location.origin}/up`;
  showModal(`<h3>📲 手机传文件到电脑</h3><div class="share-result"><img class="qr" style="max-width:180px" src="/api/qr?text=${encodeURIComponent(url)}" alt="QR"><div class="url" data-act="copy" data-v="${esc(url)}">${url}</div><p style="margin-top:8px;font-size:11px;color:var(--text2)">手机扫码打开（需登录），文件将上传到电脑的 incoming/ 目录</p></div><div class="actions" style="justify-content:center;margin-top:8px"><button class="btn btn-ghost" data-act="closeModal">关闭</button></div>`);
}

// ─── Rename ───
function showRenameModal(path){
  const name=path.split('/').pop();
  showModal(`<h3>✏️ 重命名</h3><input type="text" id="rename-input" value="${esc(name)}"><div class="actions"><button class="btn btn-ghost" data-act="closeModal">取消</button><button class="btn btn-primary" data-act="doRename" data-p="${esc(path)}">确认</button></div>`);
  const inp=document.getElementById('rename-input');inp.focus();
  const dot=name.lastIndexOf('.');if(dot>0)inp.setSelectionRange(0,dot);else inp.select();
  inp.addEventListener('keydown',(e)=>{if(e.key==='Enter')doRename(path)});
}
async function doRename(path){
  const newName=document.getElementById('rename-input').value.trim();
  if(!newName)return;
  closeModal();
  try{await api('POST','/api/rename',{path,new_name:newName});toast('✅ 已重命名');loadDir(currentPath)}catch(e){toast(e.message,true)}
}

// ─── Trash ───
async function showTrash(){
  try{
    const r=await(await api('GET','/api/trash')).json();
    let html=`<h3>🗑️ 回收站（${r.length}）<span style="font-size:11px;color:var(--text2);font-weight:400"> · 保留 30 天</span></h3>`;
    if(!r.length){html+=`<div class="empty" style="padding:24px"><div class="e-ico">${SVG.trash}</div><div class="title">回收站是空的</div><div class="hint">删除的文件会在这里保留 30 天</div></div>`}
    else{
      html+=`<div class="share-list">`;
      for(const t of r){
        html+=`<div class="share-item"><div class="si-info"><div class="si-name">${esc(t.name)}</div><div class="si-meta">${t.size_human} · ${t.days_left} 天后自动清除 · 原 ${esc(t.orig_path)}</div></div><button class="si-revoke blue" data-act="restoreTrash" data-id="${t.id}">恢复</button><button class="si-revoke" data-act="deleteTrashItem" data-id="${t.id}">删除</button></div>`;
      }
      html+=`</div><div class="actions" style="margin-top:12px"><button class="btn btn-danger" style="flex:1;display:flex;justify-content:center;padding:10px;border:none;border-radius:12px;background:linear-gradient(135deg,#fb7185,#f43f5e);color:#fff;cursor:pointer" data-act="emptyTrash">清空回收站</button></div>`;
    }
    html+=`<div class="actions" style="margin-top:12px"><button class="btn btn-ghost" data-act="closeModal">关闭</button></div>`;
    showModal(html);
  }catch(e){toast(e.message,true)}
}
async function restoreTrash(id){
  try{await api('POST',`/api/trash/${id}/restore`);toast('✅ 已恢复到原位置');showTrash()}catch(e){toast(e.message,true)}
}
async function deleteTrashItem(id){
  if(!confirm('彻底删除该文件？不可恢复'))return;
  try{await api('DELETE',`/api/trash/${id}`);showTrash()}catch(e){toast(e.message,true)}
}
async function emptyTrash(){
  if(!confirm('清空回收站？全部文件将不可恢复'))return;
  try{await api('DELETE','/api/trash');toast('已清空');showTrash()}catch(e){toast(e.message,true)}
}

// ─── Password ───
function showPasswordModal(){
  showModal(`<h3>🔑 修改密码</h3><label>原密码</label><input type="password" id="pw-old"><label>新密码（至少 8 位）</label><input type="password" id="pw-new"><div class="actions"><button class="btn btn-ghost" data-act="closeModal">取消</button><button class="btn btn-primary" data-act="doChangePassword">确认修改</button></div>`);
  document.getElementById('pw-old').focus();
}
async function doChangePassword(){
  const old=document.getElementById('pw-old').value, nw=document.getElementById('pw-new').value;
  if(nw.length<8){toast('新密码至少 8 位',true);return}
  try{
    await api('POST','/api/auth/password',{old_password:old,new_password:nw});
    closeModal();toast('✅ 密码已修改，其他设备需重新登录');
  }catch(e){toast(e.message,true)}
}

// ─── Modal ───
function showModal(html){const o=document.createElement('div');o.className='modal-overlay';o.onclick=(e)=>{if(e.target===o)closeModal()};o.innerHTML=`<div class="modal">${html}</div>`;document.body.appendChild(o)}
function closeModal(){const m=document.querySelector('.modal-overlay');if(m)m.remove()}

// ─── Toast ───
function toast(msg,err){const e=document.getElementById('toast');e.textContent=msg;e.className='toast show'+(err?' error':'');clearTimeout(e._t);e._t=setTimeout(()=>e.className='toast',2500)}

// ─── 事件委托（CSP 禁止内联 onclick：统一 data-act 分发）───
const ACTIONS={
  switchTab:el=>switchTab(el.dataset.t),
  setFilter:el=>setFilter(el.dataset.f),
  clearSearch:()=>clearSearch(),
  cycleSort:()=>cycleSort(),
  goBack:()=>goBack(),
  triggerUpload:()=>triggerUpload(),
  enterSelectMode:()=>enterSelectMode(),
  exitSelectMode:()=>exitSelectMode(),
  selectAll:()=>toggleSelectAll(),
  batchDownload:()=>batchDownload(),
  batchDelete:()=>batchDelete(),
  showMkdirModal:()=>showMkdirModal(),
  doMkdir:()=>doMkdir(),
  logout:()=>logout(),
  showShares:()=>showShares(),
  showTrash:()=>showTrash(),
  showPhoneQR:()=>showPhoneQR(),
  showDiskDetail:()=>showDiskDetail(),
  toggleSearch:()=>toggleSearch(),
  showPasswordModal:()=>showPasswordModal(),
  doChangePassword:()=>doChangePassword(),
  closeModal:()=>closeModal(),
  loadDir:el=>loadDir(el.dataset.p||''),
  loadMorePhotos:()=>loadPhotos(),
  preview:el=>previewFile(el.dataset.p,el.dataset.n),
  toggleSelect:el=>toggleSelect(el.dataset.p),
  downloadFile:el=>downloadFile(el.dataset.p),
  shareFile:el=>shareFile(el.dataset.p),
  deleteItem:el=>deleteItem(el.dataset.p,el.dataset.d==='1'),
  showRenameModal:el=>showRenameModal(el.dataset.p),
  doRename:el=>doRename(el.dataset.p),
  doShare:el=>doShare(el.dataset.p),
  copy:el=>{navigator.clipboard.writeText(el.dataset.v).then(()=>toast('✅ 已复制')).catch(()=>toast('复制失败',true))},
  showShareHistory:el=>showShareHistory(el.dataset.t),
  revokeShare:el=>revokeShare(el.dataset.t),
  restoreTrash:el=>restoreTrash(+el.dataset.id),
  deleteTrashItem:el=>deleteTrashItem(+el.dataset.id),
  emptyTrash:()=>emptyTrash(),
  closePreview:el=>el.closest('.preview-overlay').remove(),
};
document.addEventListener('click',e=>{
  const el=e.target.closest('[data-act]');
  if(!el)return;
  const fn=ACTIONS[el.dataset.act];
  if(!fn)return;
  if(el.closest('.ctx-menu'))removeCtx();
  fn(el);
});

// 长按（触屏）：触摸事件委托到 [data-press]
document.addEventListener('touchstart',e=>{
  const el=e.target.closest('[data-press]');
  if(!el)return;
  const t0=e.touches[0];
  pressTimer=setTimeout(()=>{e.preventDefault();showCtx(t0.clientX,t0.clientY,el.dataset.p,el.dataset.d==='1')},500);
},{passive:false});
document.addEventListener('touchend',()=>clearTimeout(pressTimer));
document.addEventListener('touchcancel',()=>clearTimeout(pressTimer));

// 图片加载失败（onerror 不冒泡，用捕获阶段委托）：
// 文件列表缩略图 → 回退为扩展名标签；照片墙 → 半透明占位
document.addEventListener('error',e=>{
  const t=e.target;
  if(t.tagName!=='IMG')return;
  if(t.classList.contains('file-thumb')){
    const box=t.parentElement;
    if(box){box.classList.remove('ft-media');box.textContent=(t.dataset.ext||'FILE').toUpperCase().slice(0,4)}
  }else if(t.closest('.photo-grid'))t.style.opacity=.2;
},true);

// 搜索输入
document.getElementById('search-input').addEventListener('input',e=>doSearch(e.target.value));

// ─── Init ───
loadDir('');loadDisk();
