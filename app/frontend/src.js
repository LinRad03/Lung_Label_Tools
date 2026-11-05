import { Niivue, NVMesh } from '@niivue/niivue';
import { createFallback, setFallbackWindow, setFallbackMode, setFallbackAnnotations, setFallbackTool, setFallbackMask, setFallbackZoom, setFallbackSlice, panFallback, cancelFallbackDrawing, focusFallback } from './fallback.js';
import {createGpuOverlay,setGpuAnnotations,setGpuTool,setGpuZoom,renderGpuOverlay,cancelGpuDrawing} from './gpu_overlay.js';
import {loadLobeMask,inferLobes,classifyLobeMark,partitionLobeMarks,LOBES,LOBE_BOUND_DISEASES} from './lobe_mask.js';
import {highlightReport} from './report_highlight.js';
import {automaticBlocks,blockKey,fromKey,expandBlocks,shrinkBlocks,compareBlocks} from './block_selection.js';
import {buildBlockMesh} from './block_mesh.js';
import './style.css';

const app = document.getElementById('app');
let current = null, queue = null, nv = null, fallback = null, gpuOverlay = null, maskData = null, maskPromise = null, point = null, meta = null, dirty = false, autosaveTimer = null, maskVolume = null, grid = null, caseReady = false, drawingTool = 'box', readOnly = false, tasksCache = [], draftSavePromise = null, editSerial = 0, submitting = false, crosshairTimer = null;
let blockPreviews=new Map(), finePreviews=new Map(), selectedGridSize=4, selectedLesionId=null, selectedBlockView='global', selectedBlockLayer=0, renderedBlockSelectionKey='', revisionMode=false, revisionReason='', resumeKey='', zoomKey='', viewerZoom=1, blockMesh=null, isRender3D=false, reviewKind='all', reportAvailable=false, reportCompareSequence=0, rightDrag=null, lastRightWasDrag=false;
let payload = {state:null, read_complete:false, lesions:[], rejected_lesions:[], viewed_report:false, viewed_mask:false};

async function api(path, method='GET', body) {
  const res = await fetch(path, {method, credentials:'same-origin', headers:{'Content-Type':'application/json'}, body: body ? JSON.stringify(body):undefined});
  const value = await res.json().catch(()=>({detail:'SERVER_ERROR'}));
  if (!res.ok) throw new Error(value.detail || 'SERVER_ERROR');
  return value;
}
function escapeHtml(s) { return String(s ?? '').replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function axisAligned(affine){const axes=[];for(let row=0;row<3;row++){const values=[0,1,2].map(col=>Math.abs(affine[row][col]));const axis=values.indexOf(Math.max(...values));if(!values[axis]||values.some((v,i)=>i!==axis&&v/values[axis]>.01))return false;axes.push(axis);}return new Set(axes).size===3;}
function status(s, bad=false) { document.getElementById('status').textContent=s; document.getElementById('status').className=bad?'bad':''; }
function hideTransientCrosshair(){clearTimeout(crosshairTimer);crosshairTimer=null;const stack=document.querySelector('.canvas-stack');if(stack)stack.dataset.crosshairVisible='no';if(nv?.gl)nv.setCrosshairColor([1,0.65,0,0]);}
function showTransientCrosshair(){if(!nv?.gl||!caseReady)return;nv.setCrosshairColor([1,0.65,0,1]);document.querySelector('.canvas-stack').dataset.crosshairVisible='yes';clearTimeout(crosshairTimer);crosshairTimer=setTimeout(hideTransientCrosshair,1200);}
function markView(name){document.querySelectorAll('[data-view]').forEach(el=>el.classList.toggle('selected',el.dataset.view===name));const canvas=document.getElementById('gl');if(canvas)canvas.dataset.viewMode=name;updateSliceRail();}
function slicePhysicalAxis(){const selected=document.querySelector('[data-view].selected')?.dataset.view;return selected==='sagittal'?0:selected==='coronal'?1:2;}
function sliceGeometry(){if(!meta)return null;const physical=slicePhysicalAxis();const spans=meta.affine_ras[physical].slice(0,3).map(Math.abs);const voxel=spans.indexOf(Math.max(...spans));const rasCount=Number(nv?.volumes?.[0]?.dimsRAS?.[physical+1]);return {physical,voxel,total:Math.max(1,!fallback&&rasCount>1?rasCount:Number(meta.shape_ijk[voxel]))};}
function updateSliceRail(){const rail=document.getElementById('slice-rail'),slider=document.getElementById('slice-slider'),label=document.getElementById('slice-position');if(!rail||!slider)return;rail.hidden=!caseReady||isRender3D;const g=sliceGeometry();if(!g||!caseReady)return;const fraction=fallback?fallback.index[g.voxel]/Math.max(1,g.total-1):Number(nv?.scene?.crosshairPos?.[g.physical]??.5);const index=Math.max(1,Math.min(g.total,Math.round(fraction*(g.total-1))+1));slider.max=String(g.total);slider.value=String(index);slider.disabled=isRender3D;label.textContent=`${index} / ${g.total} 层`;}
function setSliceFromRail(value){const g=sliceGeometry();if(!g||!caseReady||isRender3D)return;if(fallback)setFallbackSlice(fallback,g.physical,value-1);else if(nv?.scene?.crosshairPos){nv.scene.crosshairPos[g.physical]=(value-1)/Math.max(1,g.total-1);nv.drawScene();if(gpuOverlay)renderGpuOverlay(gpuOverlay);}updateSliceRail();}
function cancelActiveDrawing(){if(fallback)cancelFallbackDrawing(fallback);if(gpuOverlay)cancelGpuDrawing(gpuOverlay);}
function panViewer(dx,dy){
  if(fallback){panFallback(fallback,dx,dy);return;}
  if(!nv?.volumes?.length)return;
  const canvas=document.getElementById('gl');
  if(isRender3D){const scale=(1.6*(nv.furthestFromPivot||150))/Math.max(1,canvas.clientHeight*(nv.scene.volScaleMultiplier||1));const p=nv.position||[0,0,0];nv.position=[p[0]+dx*scale,p[1]-dy*scale,p[2]];canvas.dataset.panOffset=nv.position.join(',');}
  else{
    const view=document.querySelector('[data-view].selected')?.dataset.view;
    const horizontal=view==='sagittal'?1:0,vertical=view==='axial'||view==='multi'?1:2;
    const extent=axis=>{const row=meta?.affine_ras?.[axis]?.slice(0,3)||[1,1,1];return Math.max(1,row.reduce((sum,v,i)=>sum+Math.abs(v)*(Number(meta?.shape_ijk?.[i])||1),0));};
    const pan=nv.scene.pan2Dxyzmm;pan[horizontal]+=dx*extent(horizontal)/Math.max(1,canvas.clientWidth*viewerZoom);pan[vertical]-=dy*extent(vertical)/Math.max(1,canvas.clientHeight*viewerZoom);canvas.dataset.panOffset=pan.slice(0,3).join(',');
  }
  nv.drawScene();if(gpuOverlay)renderGpuOverlay(gpuOverlay);
}
function resetCase() { hideTransientCrosshair();if(nv&&blockMesh){nv.removeMesh(blockMesh);blockMesh=null;}isRender3D=false;current=null;point=null;meta=null;maskVolume=null;maskData=null;maskPromise=null;fallback?.dispose();fallback=null;gpuOverlay?.dispose();gpuOverlay=null;blockPreviews.clear();finePreviews.clear();selectedGridSize=4;if(document.getElementById("grid-resolution"))document.getElementById("grid-resolution").value="4";selectedLesionId=null;selectedBlockView='global';selectedBlockLayer=0;renderedBlockSelectionKey='';revisionMode=false;revisionReason='';caseReady=false;dirty=false;readOnly=false;window.boxStart=null;payload={state:null,read_complete:false,lesions:[],viewed_report:false,viewed_mask:false};if(document.getElementById('coords'))document.getElementById('coords').textContent='选中 RAS mm —';if(document.getElementById('hover-coords'))document.getElementById('hover-coords').textContent='鼠标 RAS mm —';if(document.getElementById('lobe-info'))document.getElementById('lobe-info').textContent='肺叶伪标签待加载';if(document.getElementById('lesions'))document.getElementById('lesions').textContent='';if(document.getElementById('report'))document.getElementById('report').textContent='';if(document.getElementById('mask-toggle')){document.getElementById('mask-toggle').checked=false;document.getElementById('mask-toggle').dataset.loaded='no';}if(document.getElementById('grid-info'))document.getElementById('grid-info').textContent='';if(document.getElementById('zoom-slider'))document.getElementById('zoom-slider').value=String(Math.round(viewerZoom*10)*10);if(document.getElementById('zoom-value'))document.getElementById('zoom-value').textContent=Math.round(viewerZoom*10)*10+'%';if(document.getElementById('block-editor'))document.getElementById('block-editor').hidden=true;if(document.getElementById('revise-case'))document.getElementById('revise-case').hidden=true;if(nv?.volumes?.length){nv.volumes=[];nv.updateGLVolume();}updateSliceRail(); }

async function loginPage() {
  const mode=await api('/api/access-mode').catch(()=>({access_link_required:false}));
  if(mode.access_link_required){app.innerHTML='<main class="login"><h1>胸部 CT 研究标注</h1><p>请打开项目管理员提供的专属访问链接。无需注册或输入密码。</p></main>';return;}
  app.innerHTML=`<main class="login"><h1>胸部 CT 研究标注</h1><p>仅限获准项目内使用</p><form id="login"><label>账号<input id="username" autocomplete="username" required></label><label>密码<input id="password" type="password" autocomplete="current-password" required></label><button>登录</button></form><p id="login-error" class="bad"></p></main>`;
  document.getElementById('login').onsubmit=async e=>{e.preventDefault();try{await api('/api/login','POST',{username:document.getElementById('username').value,password:document.getElementById('password').value});await workspace();}catch(err){document.getElementById('login-error').textContent=err.message;}};
}

async function workspace() {
  const me=await api('/api/me');
  if(me.role==='admin'){adminPage();return;}
  app.innerHTML=`<header><strong>Lung-Vol-JEPA 研究标注</strong><span id="who"></span><select id="queue"><option value="">选择项目 / 队列 / 疾病</option></select><button id="load">领取下一例</button><button id="logout">退出</button></header>
  <div class="bar"><span id="case-title">请选择队列</span><button id="previous-case" type="button">上一例</button><button id="next-case" type="button">下一例</button><select id="review-jump"><option value="">快速浏览队列病例</option></select><label class="case-scrub">队列浏览 <input id="case-slider" type="range" min="1" max="1" value="1" disabled><output id="case-index">—</output></label><div id="review-progress" class="review-progress" role="button" tabindex="0" aria-expanded="false" aria-label="查看待复核病例"><div class="progress-label">审阅进度 <output id="review-progress-text">请选择队列</output></div><progress id="review-progress-bar" max="1" value="0" aria-label="已提交审阅任务占当前队列的比例"></progress></div><span id="status">待命</span><section id="review-panel" class="review-panel" hidden><div class="review-panel-title"><strong>待复核病例</strong><button id="review-close" type="button">关闭</button></div><div class="review-filters"><button type="button" data-review-kind="skipped">曾跳过</button><button type="button" data-review-kind="discordant">与报告不一致</button></div><p id="review-panel-note">与报告不一致包含自动提示及医生改判；点击病例可复核。</p><div id="review-panel-list"></div></section></div>
  <main class="layout"><section class="viewer"><div class="tools"><button data-window="lung">肺窗</button><button data-window="mediastinal">纵隔窗</button><button data-window="bone">骨窗</button><button data-view="multi">三视图</button><button data-view="axial">轴位</button><button data-view="coronal">冠状位</button><button data-view="sagittal">矢状位</button><button data-view="render">3D 肺叶＋JEPA 块</button><button id="viewer-compat" type="button">兼容阅片</button><span id="coords">选中 RAS mm —</span><span id="hover-coords">鼠标 RAS mm —</span></div><div class="draw-tools"><button data-drawing="box" class="selected" type="button">框选 · 拖动</button><button data-drawing="point" type="button">点选 · 单击</button><span>当前工具：<strong id="drawing-name">框选</strong></span><span id="gpu-backend"></span><span id="render-mode">正在初始化阅片器</span></div><div class="canvas-stack"><canvas id="gl"></canvas><canvas id="markup" hidden></canvas></div><div class="zoom-control"><label for="zoom-slider">影像缩放</label><input id="zoom-slider" type="range" min="100" max="800" step="10" value="100" aria-label="影像缩放"><output id="zoom-value" for="zoom-slider">100%</output></div><small>拖动框选、单击点选；触控板双指滚动翻层；捏合、Ctrl+滚动或右键上下拖动缩放。3D 左键拖动旋转；右键单击切换肺叶显示；左右键同时按住拖动平移影像。CT 半透明、五肺叶为 nnUNet 伪标签、青色为 JEPA 块预览。</small></section>
  <aside class="report" id="report-panel"><h2>原始报告</h2><small>当前病种关键词高亮；疾病状态仍由阅片者判定。</small><p id="report">请选择病例</p></aside>
  <aside class="task"><h2 id="disease">疾病任务</h2><p id="stage"></p><div id="adjudication" hidden></div><button id="revise-case" type="button" hidden>修改已提交标注（保留历史）</button><fieldset><legend>疾病状态</legend><label><input type="radio" name="state" value="positive">阳性</label><label><input type="radio" name="state" value="negative">阴性</label><label><input type="radio" name="state" value="uncertain">无法判定</label></fieldset>
  <label id="read-complete-control" hidden><input type="checkbox" id="read-complete">已完整阅片（阴性需确认）</label><fieldset id="report-comparison" hidden><legend>与原始报告结论</legend><label><input type="radio" name="report-agreement" value="agree">一致</label><label><input type="radio" name="report-agreement" value="disagree">不一致</label><small id="report-agreement-hint">自动比较结果将在选择疾病状态后显示；可手动修改。</small></fieldset><div class="buttons primary-actions"><button id="save">保存草稿</button><button id="submit" disabled>提交并下一例</button></div><small id="submit-hint">请先选择疾病状态。已提交病例可点击上方按钮修订，原版本保留。</small><label id="mask-control" hidden><input type="checkbox" id="mask-toggle">显示五肺叶伪标签（仅辅助）</label><div id="lobe-legend" class="lobe-legend"></div><p id="lobe-info">肺叶伪标签待加载</p><div class="tools"><button id="add-point">记录当前点</button><button id="box-start">框起点</button><button id="box-end">框终点</button><button id="map-point">预览JEPA目标块</button></div><p id="grid-info"></p><p id="box-note">肺内任务的新勾画若完全落在五肺叶伪标签外，会从当前草稿移除并保留撤销记录。原 CT RAS 坐标和医生部位分开保存。</p><div id="lesions"></div>
  <section id="block-editor" class="block-editor" hidden><h3>JEPA 块预览</h3><p>原 CT 坐标为主记录；彩色块仅表示相交区域。</p><label>病灶 <select id="block-lesion"></select></label><label>视图 <select id="block-view"></select></label><label>块层 <input id="block-layer" type="range" min="0" max="3" value="0"><output id="block-layer-value">1/4</output></label><div id="block-grid" class="block-grid"></div><div class="buttons"><button id="block-expand" type="button">联通扩展一层</button><button id="block-shrink" type="button">收缩一层</button><button id="block-auto" type="button">恢复自动块</button></div><small id="block-summary"></small><div id="bulla-location" class="bulla-location" hidden><h3>肺大泡肺叶位置记录</h3><small id="bulla-suggestion"></small><div id="bulla-lobe-choices"></div><button id="bulla-confirm" type="button">确认此病灶肺叶</button><small id="bulla-confirmed"></small></div></section>
  <label>部位/侧别<input id="region" placeholder="例如右上肺；可留空"></label><div class="patient-nav"><button id="patient-prev" type="button">← 上一位患者</button><button id="patient-next" type="button">下一位患者 →</button></div></aside></main>`;
  document.getElementById('who').textContent=me.username+' · '+me.role;
  const reportPanel=document.getElementById('report-panel');
  const reportSource=document.createElement('section');reportSource.className='report-source';
  for(const node of [...reportPanel.children])reportSource.appendChild(node);
  const markPanel=document.createElement('section');markPanel.className='annotation-overview';
  markPanel.innerHTML='<h2>本例已标注位置 <output id="lesion-count">0</output></h2>';
  markPanel.appendChild(document.getElementById('lesions'));
  reportPanel.append(reportSource,markPanel);
  const blockPanel=document.getElementById('block-editor');
  const gridLabel=document.createElement('label');
  gridLabel.innerHTML='空间网格 <select id="grid-resolution"><option value="4">JEPA 模型块 4³</option><option value="16">标注细网格 16³</option><option value="32">标注细网格 32³</option></select>';
  blockPanel.insertBefore(gridLabel,blockPanel.querySelector('label'));
  const reviewPanel=document.getElementById('review-panel');
  const counts=document.createElement('div');counts.id='review-counts';counts.className='review-counts';
  counts.innerHTML='<button type="button" data-review-kind="all">全部 <span>—</span></button><button type="button" data-review-kind="agree">一致 <span>—</span></button><button type="button" data-review-kind="discordant">不一致 <span>—</span></button><button type="button" data-review-kind="skipped">跳过 <span>—</span></button><small id="review-undetermined"></small>';
  reviewPanel.querySelector('.review-filters').replaceWith(counts);
  document.getElementById('review-progress').title='点击展开提交情况和病例跳转';
  const rail=document.createElement('div');rail.id='slice-rail';rail.className='slice-rail';
  rail.innerHTML='<output id="slice-position">层位 —</output><input id="slice-slider" type="range" min="1" max="1" value="1" orient="vertical" aria-label="快速切换影像层位" disabled>';
  document.querySelector('.canvas-stack').appendChild(rail);
  resumeKey='jepa_last_review_v1_'+me.username;
  zoomKey='jepa_viewer_zoom_v1_'+me.username;
  const savedZoom=Number(localStorage.getItem(zoomKey));
  viewerZoom=Number.isFinite(savedZoom)&&savedZoom>=1&&savedZoom<=8?savedZoom:1;
  document.getElementById('logout').onclick=async()=>{await api('/api/logout','POST');location.reload();};
  document.getElementById('load').onclick=()=>nextCase();
  document.getElementById('previous-case').onclick=()=>browseRelative(-1);
  document.getElementById('next-case').onclick=()=>browseRelative(1);
  document.getElementById('patient-prev').onclick=()=>browseRelative(-1);
  document.getElementById('patient-next').onclick=()=>browseRelative(1);
  document.getElementById('review-jump').onchange=e=>{if(e.target.value)nextCase(Number(e.target.value));};
  const reviewToggle=document.getElementById('review-progress');
  reviewToggle.onclick=()=>toggleReviewPanel();
  reviewToggle.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();toggleReviewPanel();}};
  document.getElementById('review-close').onclick=e=>{e.stopPropagation();closeReviewPanel();};
  document.querySelectorAll('[data-review-kind]').forEach(el=>el.onclick=e=>{e.stopPropagation();loadReviewList(el.dataset.reviewKind);});
  document.getElementById('slice-slider').oninput=e=>setSliceFromRail(Number(e.target.value));
  document.querySelector('.canvas-stack').addEventListener('wheel',()=>requestAnimationFrame(updateSliceRail));
  document.getElementById('review-panel-list').onclick=e=>{const button=e.target.closest('[data-review-task]');if(!button)return;closeReviewPanel();nextCase(Number(button.dataset.reviewTask));};
  document.getElementById('case-slider').oninput=e=>{document.getElementById('case-index').textContent=e.target.value+'/'+tasksCache.length;};
  document.getElementById('case-slider').onchange=e=>{const item=tasksCache[Number(e.target.value)-1];if(item)nextCase(item.task_id);};
  document.getElementById('viewer-compat').onclick=()=>{localStorage.setItem('jepa_viewer_preference',fallback?'webgl':'cpu');location.reload();};
  document.getElementById('revise-case').onclick=startRevision;
  document.getElementById('block-lesion').onchange=e=>{selectedLesionId=e.target.value;selectedBlockView='global';renderBlockEditor();loadSelectedFinePreview();};
  document.getElementById('grid-resolution').onchange=e=>{selectedGridSize=Number(e.target.value);selectedBlockLayer=0;renderedBlockSelectionKey='';renderBlockEditor();loadSelectedFinePreview();};
  document.getElementById('block-view').onchange=e=>{selectedBlockView=e.target.value;renderBlockEditor();};
  document.getElementById('block-layer').oninput=e=>{selectedBlockLayer=Number(e.target.value);renderBlockGrid();};
  document.getElementById('block-expand').onclick=()=>editBlocks('expand');
  document.getElementById('block-shrink').onclick=()=>editBlocks('shrink');
  document.getElementById('block-auto').onclick=()=>editBlocks('auto');
  document.getElementById('bulla-confirm').onclick=confirmBullaLobes;
  const queues=await api('/api/queues');
  grid=await api('/api/model-grid');
  document.getElementById('queue').innerHTML='<option value="">选择项目 / 队列 / 疾病</option>'+queues.map(q=>`<option value="${escapeHtml(q.queue_id)}">${escapeHtml(q.project)} / ${escapeHtml(q.cohort)} / ${escapeHtml(q.disease)} / ${escapeHtml(q.mode)}</option>`).join('');
  document.getElementById('queue').onchange=e=>{if(dirty&&!confirm('当前修改尚未保存，仍切换队列？')){e.target.value=queue||'';return;}queue=e.target.value;closeReviewPanel();resetCase();syncSubmitState();document.getElementById('case-title').textContent='请选择领取下一例';if(queue)localStorage.setItem(resumeKey,JSON.stringify({queue,task_id:null}));refreshTaskList();};
  const updateLocation=(mm)=>{if(mm&&mm.length>=3){point=Array.from(mm).slice(0,3).map(Number);document.getElementById('coords').textContent='选中 RAS mm '+point.map(x=>x.toFixed(1)).join(', ');}};
  const updateHover=(mm)=>{if(mm&&mm.length>=3){document.getElementById('hover-coords').textContent='鼠标 RAS mm '+Array.from(mm).slice(0,3).map(x=>Number(x).toFixed(1)).join(', ');if(maskData){const hit=inferLobes(maskData,{kind:'point',ras_mm:Array.from(mm).slice(0,3)});document.getElementById('lobe-info').textContent='鼠标位置：'+(hit.lobes[0]?.name||'五肺叶外/未识别')+'（nnUNet伪标签）';}}};
  document.getElementById('lobe-legend').innerHTML=Object.values(LOBES).map(l=>`<span><i style="background:rgb(${l.color.join(',')})"></i>${l.name}</span>`).join('');
  try {
    if(localStorage.getItem('jepa_viewer_preference')==='cpu')throw Error('USER_SELECTED_CPU_VIEWER');
    nv=new Niivue({crosshairColor:[1,0.65,0,0],show3Dcrosshair:false,backColor:[0,0,0,1],textHeight:0.025,isRadiologicalConvention:true});
    await nv.attachToCanvas(document.getElementById('gl'));
    nv.setRadiologicalConvention(true);
    nv.setSliceType(nv.sliceTypeAxial);
    nv.setMultiplanarLayout(2);
    nv.setCornerOrientationText(true);
    const gpuExt=nv.gl.getExtension('WEBGL_debug_renderer_info');
    const renderer=gpuExt?String(nv.gl.getParameter(gpuExt.UNMASKED_RENDERER_WEBGL)):'WebGL2';
    document.getElementById('gpu-backend').textContent=/SwiftShader|llvmpipe|software/i.test(renderer)?'图形后端：软件 WebGL':'图形后端：WebGL GPU';
    nv.onLocationChange=(loc)=>{updateLocation(loc.mm);if(gpuOverlay)renderGpuOverlay(gpuOverlay);};
    document.getElementById('gl').addEventListener('pointerup',()=>setTimeout(()=>{if(nv?.volumes?.length)updateLocation(nv.frac2mm(nv.scene.crosshairPos));},0));
  } catch (_) {
    nv=null;
    const old=document.getElementById('gl');
    const fresh=document.createElement('canvas');fresh.id='gl';old.replaceWith(fresh);
    status('WebGL 不可用；已切换 CPU 阅片');
    document.getElementById('gpu-backend').textContent='图形后端：CPU';
  }
  window.fallbackPoint=updateLocation;
  window.fallbackHover=updateHover;
  const syncZoom=zoom=>{viewerZoom=Math.max(1,Math.min(8,Number(zoom)||1));const pct=Math.round(viewerZoom*10)*10;document.getElementById('zoom-slider').value=String(pct);document.getElementById('zoom-value').textContent=pct+'%';if(zoomKey)localStorage.setItem(zoomKey,String(viewerZoom));};
  window.syncViewerZoom=syncZoom;
  syncZoom(viewerZoom);
  document.getElementById('zoom-slider').oninput=e=>{const value=Number(e.target.value)/100;if(fallback)setFallbackZoom(fallback,value);else if(gpuOverlay)setGpuZoom(gpuOverlay,value,isRender3D);else syncZoom(value);};
  document.querySelectorAll('[data-window]').forEach(el=>el.onclick=()=>setWindow(el.dataset.window));
  document.querySelectorAll('[data-view]').forEach(el=>el.onclick=()=>setView(el.dataset.view));
  document.querySelectorAll('[data-drawing]').forEach(el=>el.onclick=()=>{drawingTool=el.dataset.drawing;document.querySelectorAll('[data-drawing]').forEach(b=>b.classList.toggle('selected',b===el));document.getElementById('drawing-name').textContent=drawingTool==='box'?'框选':'点选';if(fallback)setFallbackTool(fallback,drawingTool,ras=>addLesion('point',ras));if(gpuOverlay)setGpuTool(gpuOverlay,drawingTool,ras=>addLesion('point',ras));});
  document.querySelectorAll('input[name=state]').forEach(el=>el.onchange=()=>{change(p=>{if(p.state!==el.value){p.report_agreement=null;p.report_agreement_source=null;p.report_agreement_reason=null;if(el.value==='negative')p.read_complete=false;else if(el.value==='positive'&&p.lesions.length)p.read_complete=true;}p.state=el.value;});renderPayload();refreshAutoReportAgreement();});
  document.querySelectorAll('input[name=report-agreement]').forEach(el=>el.onchange=()=>{reportCompareSequence++;change(p=>{p.report_agreement=el.value;p.report_agreement_source='reader';p.report_agreement_reason=null;});renderPayload();});
  document.getElementById('read-complete').onchange=e=>change(p=>p.read_complete=e.target.checked);
  document.getElementById('add-point').onclick=()=>addPoint();
  document.getElementById('map-point').onclick=previewJepa;
  document.getElementById('mask-toggle').onchange=toggleMask;
  document.querySelector('.canvas-stack').addEventListener('pointerup',e=>{if(e.button===0)showTransientCrosshair();});
  const stack=document.querySelector('.canvas-stack');
  stack.addEventListener('pointerdown',e=>{
    if(e.target.closest('#slice-rail'))return;
    if(e.button!==2&&!(rightDrag&&e.button===0&&(e.buttons&3)===3))return;
    lastRightWasDrag=false;
    if(!rightDrag)rightDrag={pointerId:e.pointerId,x:e.clientX,y:e.clientY,startY:e.clientY,moved:false,pan:false};
    if((e.buttons&3)===3){rightDrag.pan=true;cancelActiveDrawing();}
    e.preventDefault();e.stopPropagation();
  },true);
  stack.addEventListener('pointermove',e=>{
    if(!rightDrag&&((e.buttons&3)===3)){rightDrag={pointerId:e.pointerId,x:e.clientX,y:e.clientY,startY:e.clientY,moved:false,pan:true};cancelActiveDrawing();}
    if(!rightDrag||e.pointerId!==rightDrag.pointerId)return;
    if((e.buttons&3)===3&&!rightDrag.pan){rightDrag.pan=true;cancelActiveDrawing();}
    if(rightDrag.pan&&(e.buttons&3)!==3){e.preventDefault();e.stopPropagation();return;}
    const dx=e.clientX-rightDrag.x,dy=e.clientY-rightDrag.y;rightDrag.x=e.clientX;rightDrag.y=e.clientY;
    if(rightDrag.pan){if(dx||dy){rightDrag.moved=true;panViewer(dx,dy);}}
    else if(Math.abs(e.clientY-rightDrag.startY)>5){rightDrag.moved=true;const next=viewerZoom*Math.exp(-dy*.012);if(fallback)setFallbackZoom(fallback,next);else if(gpuOverlay)setGpuZoom(gpuOverlay,next,isRender3D);else syncZoom(next);}
    e.preventDefault();e.stopPropagation();
  },true);
  stack.addEventListener('pointerup',e=>{
    if(!rightDrag||e.pointerId!==rightDrag.pointerId)return;
    if(rightDrag.pan){lastRightWasDrag=true;cancelActiveDrawing();if((e.buttons&3)===0)rightDrag=null;e.preventDefault();e.stopPropagation();return;}
    if(e.button!==2)return;
    lastRightWasDrag=rightDrag.moved;rightDrag=null;e.preventDefault();e.stopPropagation();
    if(!lastRightWasDrag&&current&&caseReady){if(!current.mask_available)status('此病例没有可显示的肺叶伪标签',true);else{const toggle=document.getElementById('mask-toggle');toggle.checked=!toggle.checked;toggleMask({target:toggle});}}
  },true);
  stack.addEventListener('contextmenu',e=>{e.preventDefault();e.stopPropagation();});
  document.getElementById('box-start').onclick=()=>{if(!point)return status('请先在 CT 中选择位置',true);window.boxStart=[...point];status('已选择框起点');};
  document.getElementById('box-end').onclick=()=>{if(!point||!window.boxStart)return status('先选择框起点和终点',true);addLesion('box',window.boxStart,[...point]);window.boxStart=null;};
  document.getElementById('save').onclick=()=>saveDraft().catch(()=>{});
  document.getElementById('submit').onclick=submit;
  window.addEventListener('beforeunload',e=>{if(dirty){e.preventDefault();e.returnValue='';}});
  document.addEventListener('keydown',e=>{if(e.key==='s'&&e.ctrlKey){e.preventDefault();saveDraft();}});
  let saved=null;try{saved=JSON.parse(localStorage.getItem(resumeKey)||'null');}catch(_){saved=null;}
  if(saved?.queue&&queues.some(q=>q.queue_id===saved.queue)){
    queue=saved.queue;document.getElementById('queue').value=queue;
    await refreshTaskList();
    if(saved.task_id&&tasksCache.some(t=>t.task_id===saved.task_id))nextCase(saved.task_id);
  }
}
function adminPage(){
  app.innerHTML=`<header><strong>标注项目管理</strong><button id="logout">退出</button></header><main class="login admin"><h1>冻结病例队列</h1><p>按患者组和固定种子抽样，不按报告标签筛选。</p><form id="queue-form"><label>队列 ID<input name="queue_id" required pattern="[A-Za-z0-9_-]+"></label><label>项目<input name="project" required></label><label>队列<input name="cohort" required></label><label>疾病<select name="disease"><option value="pulmonary_nodule">肺结节</option><option value="atelectasis">肺不张</option><option value="consolidation">实变</option><option value="bronchiectasis">支气管扩张</option><option value="pleural_effusion">胸腔积液</option><option value="lymphadenopathy">淋巴结肿大</option><option value="pericardial_effusion">心包积液</option><option value="pulmonary_bulla">肺大泡／肺大疱</option></select></label><label>模式<select name="mode"><option value="blind">独立测试盲读</option><option value="assisted">开发报告辅助</option></select></label><label>随机种子<input name="seed" type="number" value="20261029"></label><label>独立患者预算<input name="patient_budget" type="number" value="100" min="1"></label><label>读者 1 账号<input name="reader1" required></label><label>读者 2 账号<input name="reader2" required></label><button type="button" id="preview">预览聚合数量</button><button type="button" id="freeze">冻结队列</button></form><pre id="admin-output"></pre><h2>仲裁任务</h2><label>已完成盲读队列<input id="adj-queue"></label><label>仲裁者账号<input id="adj-user"></label><button id="make-adj">生成已完成双读者病例的仲裁任务</button></main>`;
  document.getElementById('logout').onclick=async()=>{await api('/api/logout','POST');location.reload();};
  const request=()=>{let f=new FormData(document.getElementById('queue-form'));return {queue_id:f.get('queue_id'),project:f.get('project'),cohort:f.get('cohort'),disease:f.get('disease'),mode:f.get('mode'),seed:Number(f.get('seed')),patient_budget:Number(f.get('patient_budget')),readers:[f.get('reader1'),f.get('reader2')]};};
  for(const action of ['preview','freeze'])document.getElementById(action).onclick=async()=>{try{let r=await api('/api/queues/'+action,'POST',request());document.getElementById('admin-output').textContent=JSON.stringify(r,null,2);}catch(err){document.getElementById('admin-output').textContent=err.message;}};
  document.getElementById('make-adj').onclick=async()=>{try{let q=document.getElementById('adj-queue').value;let r=await api('/api/queues/'+encodeURIComponent(q)+'/prepare-adjudication','POST',{adjudicator:document.getElementById('adj-user').value});document.getElementById('admin-output').textContent=JSON.stringify(r);}catch(err){document.getElementById('admin-output').textContent=err.message;}};
}
async function refreshTaskList(){
  tasksCache=[];const select=document.getElementById('review-jump');
  select.innerHTML='<option value="">快速浏览队列病例</option>';
  if(!queue){setReviewProgress(0,0,0);return;}
  try{const result=await api('/api/queues/'+encodeURIComponent(queue)+'/task-list');tasksCache=result.tasks;
    select.innerHTML='<option value="">快速浏览队列病例</option>'+tasksCache.map((t,i)=>`<option value="${t.task_id}">${i+1}/${tasksCache.length} · ${escapeHtml(t.case_id)} · ${escapeHtml(t.status)}</option>`).join('');
    const scrub=document.getElementById('case-slider');scrub.max=String(Math.max(1,tasksCache.length));scrub.disabled=!tasksCache.length;
    document.getElementById('case-index').textContent=tasksCache.length?'1/'+tasksCache.length:'—';await updateProgress();}
  catch(err){status('病例列表读取失败：'+err.message,true);}
}
function browseRelative(delta){
  if(!tasksCache.length)return status('请先选择队列',true);
  const index=current?tasksCache.findIndex(t=>t.task_id===current.task_id):-1;
  const target=Math.max(0,Math.min(tasksCache.length-1,index+delta));
  nextCase(tasksCache[target].task_id);
}
function closeReviewPanel(){const panel=document.getElementById('review-panel');if(panel)panel.hidden=true;const toggle=document.getElementById('review-progress');if(toggle)toggle.setAttribute('aria-expanded','false');}
function toggleReviewPanel(){
  if(!queue)return status('请先选择队列',true);
  const panel=document.getElementById('review-panel');panel.hidden=!panel.hidden;
  document.getElementById('review-progress').setAttribute('aria-expanded',String(!panel.hidden));
  if(!panel.hidden)loadReviewList(reviewKind);
}
async function loadReviewList(kind){
  reviewKind=kind;
  document.querySelectorAll('[data-review-kind]').forEach(el=>el.classList.toggle('selected',el.dataset.reviewKind===kind));
  const list=document.getElementById('review-panel-list');list.textContent='正在读取…';
  const activeQueue=queue;
  try{
    const data=await api('/api/queues/'+encodeURIComponent(activeQueue)+'/review-list?kind='+encodeURIComponent(kind));
    if(queue!==activeQueue||document.getElementById('review-panel').hidden)return;
    const c=data.counts||{};
    for(const key of ['agree','discordant','skipped']){const target=document.querySelector(`[data-review-kind="${key}"] span`);if(target)target.textContent=String(c[key]||0);}
    document.querySelector('[data-review-kind="all"] span').textContent=String(Object.values(c).reduce((a,b)=>a+Number(b||0),0));
    document.getElementById('review-undetermined').textContent=c.undetermined?`报告一致性待判定 ${c.undetermined} 例`:'点击分类查看并跳转病例';
    list.innerHTML=data.tasks.length?data.tasks.map(x=>`<button type="button" data-review-task="${Number(x.task_id)}">${escapeHtml(x.case_id)} · ${escapeHtml({agree:'一致',discordant:'不一致',skipped:'跳过',undetermined:'待判定'}[x.review_category]||x.status)} · ${escapeHtml(x.status)}</button>`).join(''):'当前分类没有病例';
  }catch(err){list.textContent='读取失败：'+err.message;}
}
function syncSubmitState(){
  const submitButton=document.getElementById('submit'),hint=document.getElementById('submit-hint');
  if(!submitButton||!hint)return;
  const selected=['positive','negative','uncertain'].includes(payload.state);
  const negativeIncomplete=payload.state==='negative'&&payload.read_complete!==true;
  submitButton.disabled=!current||!caseReady||readOnly||!selected||negativeIncomplete;
  hint.textContent=!selected?'请先选择疾病状态。':negativeIncomplete?'提交阴性前，请勾选“已完整阅片”。':readOnly?'已提交标注可点击上方按钮修改，原版本保留。':'疾病状态已选择，可以提交。';
}
function change(fn){if(!current||readOnly||submitting)return;fn(payload);editSerial++;dirty=true;syncSubmitState();status(revisionMode?'修订未提交':'未保存');clearTimeout(autosaveTimer);if(!revisionMode)autosaveTimer=setTimeout(()=>saveDraft().catch(()=>{}),2500);}
function setWindow(name){const range={lung:[-1350,150],mediastinal:[-160,240],bone:[-500,1500]}[name];if(fallback){setFallbackWindow(fallback,range);return;}if(!nv?.volumes?.length)return;nv.volumes[0].cal_min=range[0];nv.volumes[0].cal_max=range[1];nv.updateGLVolume();if(gpuOverlay)requestAnimationFrame(()=>renderGpuOverlay(gpuOverlay));}
async function setView(name){
  if(fallback){if(name==='render')return switchTo3D();setFallbackMode(fallback,name);markView(name);return;}
  if(!nv||!nv.volumes?.length)return;
  isRender3D=name==='render';
  if(isRender3D){
    nv.setOpacity(0,.35);
    nv.setSliceType(nv.sliceTypeRender);
    nv.setRenderAzimuthElevation(30,20);
    if(gpuOverlay)setGpuZoom(gpuOverlay,viewerZoom,true);
    document.getElementById('markup').hidden=true;
    document.getElementById('render-mode').textContent='WebGL 3D 阅片';
    const toggle=document.getElementById('mask-toggle');
    if(current?.mask_available&&!toggle.checked){toggle.checked=true;await toggleMask({target:toggle});}
    else if(maskVolume)nv.setOpacity(1,.65);
    renderBlock3D();
    markView(name);
  }else{
    if(blockMesh){nv.removeMesh(blockMesh);blockMesh=null;}
    nv.setOpacity(0,1);
    if(maskVolume)nv.setOpacity(1,document.getElementById('mask-toggle').checked?.5:0);
    const types={multi:nv.sliceTypeMultiplanar,axial:nv.sliceTypeAxial,coronal:nv.sliceTypeCoronal,sagittal:nv.sliceTypeSagittal};
    nv.setSliceType(types[name]);document.getElementById('markup').hidden=false;
    document.getElementById('render-mode').textContent='WebGL 阅片';
    if(gpuOverlay)requestAnimationFrame(()=>renderGpuOverlay(gpuOverlay));
    markView(name);
  }
}
async function switchTo3D(){
  if(!current||!caseReady)return status('请先加载病例',true);
  if(revisionMode&&dirty)return status('请先提交当前修订，再切换到 3D',true);
  if(dirty){await saveDraft();if(dirty)return status('草稿未保存，已保留当前阅片视图',true);}
  const probe=document.createElement('canvas');
  const gl=probe.getContext('webgl2');
  if(!gl)return status('当前浏览器未提供 WebGL2；请用项目 Chrome 3D 启动器打开 8767',true);
  gl.getExtension('WEBGL_lose_context')?.loseContext();
  sessionStorage.setItem('jepa_pending_3d_task',String(current.task_id));
  localStorage.setItem('jepa_viewer_preference','webgl');
  status('正在切换到 3D 阅片');
  location.reload();
}
async function nextCase(taskId=null){
  if(!queue)return status('请先选择队列',true);
  if(dirty&&!confirm('修改尚未保存，仍进入下一例？'))return;
  clearTimeout(autosaveTimer);resetCase();reportCompareSequence++;reportAvailable=false;syncSubmitState();document.querySelectorAll('input[name=state],input[name=report-agreement]').forEach(el=>{el.checked=false;el.disabled=true;});status('正在领取');
  try{
    const item=taskId?await api('/api/tasks/'+taskId):await api('/api/queues/'+encodeURIComponent(queue)+'/next');
    if(item.complete){document.getElementById('case-title').textContent='队列已完成';status('无待标注任务');return;}
    current=item;payload={state:null,read_complete:false,lesions:[],rejected_lesions:[],viewed_report:false,viewed_mask:false,...item.draft};
    const recoveredDrawingState=item.status!=='submitted'&&payload.lesions.length>0&&
      (payload.state!=='positive'||payload.read_complete!==true);
    if(recoveredDrawingState){payload.state='positive';payload.read_complete=true;payload.report_agreement=null;payload.report_agreement_source=null;payload.report_agreement_reason=null;}
    if(item.status!=='submitted'&&payload.report_agreement==='uncertain'){
      payload.report_agreement=null;payload.report_agreement_source=null;payload.report_agreement_reason='legacy_uncertain';
    }
    readOnly=item.status==='submitted';
    document.getElementById('revise-case').hidden=item.status!=='submitted';
    document.getElementById('case-title').textContent='研究编号 '+item.case_id+(readOnly?' · 已提交，可修订':item.status==='skipped'?' · 曾跳过，待重新标注':'');
    document.getElementById('review-jump').value=String(item.task_id);
    for(const id of ['save','submit','add-point','box-start','box-end'])document.getElementById(id).disabled=readOnly;
    document.getElementById('submit').textContent='提交并下一例';
    document.querySelectorAll('input[name=state],input[name=report-agreement],#read-complete,#region').forEach(el=>el.disabled=readOnly);
    document.getElementById('disease').textContent=item.definition.label;
    document.getElementById('stage').textContent=item.stage==='A'?(item.report_available!==undefined?'报告辅助标注':'独立盲读 · A 阶段'):'报告核验 · B 阶段';
    if(item.stage==='adjudication')document.getElementById('stage').textContent='双读者仲裁 · 独立参考';
    document.getElementById('report-panel').style.display='flex';
    document.getElementById('report-comparison').hidden=!item.report_available;
    document.getElementById('report').textContent=item.report_available!==undefined?'正在加载报告辅助资料':'独立阅片阶段不显示报告';
    if(item.report_available){
      try {const r=await api('/api/tasks/'+item.task_id+'/report');if(r.status==='available')highlightReport(document.getElementById('report'),r.text,item.disease);else document.getElementById('report').textContent='此检查未找到可靠报告';payload.viewed_report=r.status==='available';reportAvailable=r.status==='available';if(reportAvailable)refreshAutoReportAgreement();}
      catch (_) {document.getElementById('report').textContent='报告读取失败，请勿依据此栏作判断';}
    } else if(item.report_available===false) document.getElementById('report').textContent='此检查未找到可靠报告';
    const info=await api('/api/tasks/'+item.task_id+'/image-meta');meta=info;
    if(!info.orientation.every(x=>['L','R','A','P','S','I'].includes(x)))throw Error('UNSUPPORTED_ORIENTATION');
    if(nv){
      try {await nv.loadVolumes([{url:'/api/tasks/'+item.task_id+'/volume',name:'research_ct.'+info.format,colormap:'gray',cal_min:-1350,cal_max:150}]);nv.setSliceType(nv.sliceTypeAxial);}
      catch (_) {nv=null;const old=document.getElementById('gl');const fresh=document.createElement('canvas');fresh.id='gl';old.replaceWith(fresh);}
    }
    if(nv){try{const markup=document.getElementById('markup');markup.hidden=false;if(nv.scene.pan2Dxyzmm)nv.scene.pan2Dxyzmm[3]=1;gpuOverlay=createGpuOverlay(markup,nv,window.fallbackPoint,(lo,hi)=>addLesion('box',lo,hi),window.fallbackHover,window.syncViewerZoom);setGpuTool(gpuOverlay,drawingTool,ras=>addLesion('point',ras));}
      catch(_){nv=null;gpuOverlay=null;document.getElementById('gpu-backend').textContent='图形后端：CPU';const old=document.getElementById('gl');const fresh=document.createElement('canvas');fresh.id='gl';old.replaceWith(fresh);}}
    if(!nv){document.getElementById('markup').hidden=true;fallback=await createFallback(document.getElementById('gl'),'/api/tasks/'+item.task_id+'/volume',info,window.fallbackPoint,window.fallbackHover,(lo,hi)=>addLesion('box',lo,hi),window.syncViewerZoom);setFallbackTool(fallback,drawingTool,ras=>addLesion('point',ras));}
    if(fallback)setFallbackZoom(fallback,viewerZoom);
    else if(gpuOverlay)setGpuZoom(gpuOverlay,viewerZoom);
    document.getElementById('render-mode').textContent=fallback?'CPU 阅片':'WebGL 阅片';
    markView('axial');
    document.getElementById('viewer-compat').textContent=fallback?'尝试 WebGL':'使用兼容阅片';
    document.getElementById('mask-control').hidden=!item.mask_available;
    const adjBox=document.getElementById('adjudication');adjBox.hidden=item.stage!=='adjudication';
    if(item.stage==='adjudication'){const r=await api('/api/tasks/'+item.task_id+'/adjudication');adjBox.textContent=r.independent_annotations.map(x=>`${x.reader}: ${x.annotation.state}; ${x.annotation.lesions?.length||0} 处标记`).join(' / ');}
    renderPayload();updateProgress();caseReady=true;updateSliceRail();syncSubmitState();status((readOnly?'只读回顾 · ':'已加载 · ')+(fallback?'CPU 阅片模式':'WebGL 阅片模式'));
    if(recoveredDrawingState){change(()=>{});renderPayload();refreshAutoReportAgreement();}
    const taskIndex=tasksCache.findIndex(t=>t.task_id===item.task_id);
    if(taskIndex>=0){document.getElementById('case-slider').value=String(taskIndex+1);document.getElementById('case-index').textContent=(taskIndex+1)+'/'+tasksCache.length;}
    localStorage.setItem(resumeKey,JSON.stringify({queue,task_id:item.task_id}));
    if(payload.lesions.length)loadExistingBlockPreviews(item.task_id,payload.lesions);
    if(item.mask_available&&info.mask_format){const expectedTask=item.task_id;maskPromise=loadLobeMask('/api/tasks/'+item.task_id+'/mask',info).then(mask=>{if(current?.task_id!==expectedTask)return null;maskData=mask;document.getElementById('lobe-info').textContent='五肺叶伪标签已加载；勾选显示';discardOutsideLobeDraftMarks();renderPayload();return mask;}).catch(err=>{if(current?.task_id===expectedTask)document.getElementById('lobe-info').textContent='肺叶伪标签加载失败：'+err.message;return null;});}
    else document.getElementById('lobe-info').textContent='此病例没有可用五肺叶伪标签';
    if(sessionStorage.getItem('jepa_pending_3d_task')===String(item.task_id)){
      sessionStorage.removeItem('jepa_pending_3d_task');
      if(nv)await setView('render');
      else status('WebGL2 初始化失败；请用项目 Chrome 3D 启动器打开 8767',true);
    }
  }catch(err){status('加载失败：'+err.message,true);}
}
async function toggleMask(e){
  if(!current)return;
  const taskId=current.task_id;
  try{
    if(e.target.checked){
      if(!maskData)await maskPromise;
      if(!maskData||current?.task_id!==taskId)throw Error('MASK_NOT_READY');
      if(fallback)setFallbackMask(fallback,maskData,true);
      if(nv){
        if(!maskVolume){
          const colors=Array.from({length:11},(_,i)=>LOBES[i]?.color||[0,0,0]);
          nv.addColormap('jepa_five_lobes',{
            R:colors.map(c=>c[0]),G:colors.map(c=>c[1]),B:colors.map(c=>c[2]),
            A:colors.map((_,i)=>LOBES[i]?190:0),I:colors.map((_,i)=>Math.round(i*255/10))});
          maskVolume=await nv.addVolumeFromUrl({url:'/api/tasks/'+taskId+'/mask',name:'five_lobe_pseudolabel.'+meta.mask_format,
            colormap:'jepa_five_lobes',opacity:isRender3D?.65:.5,cal_min:0,cal_max:10,ignoreZeroVoxels:true});
        }else{nv.setOpacity(1,isRender3D?.65:.5);}
        if(gpuOverlay)renderGpuOverlay(gpuOverlay);
      }
      e.target.dataset.loaded='yes';change(p=>p.viewed_mask=true);
    }else{
      if(fallback)setFallbackMask(fallback,maskData,false);
      if(maskVolume){nv.setOpacity(1,0);if(gpuOverlay)renderGpuOverlay(gpuOverlay);}
      e.target.dataset.loaded='no';
    }
  }catch(err){e.target.checked=false;status('伪标签显示失败：'+err.message,true);}
}
function setReviewProgress(submitted,total,skipped){
  const bar=document.getElementById('review-progress-bar');
  const label=document.getElementById('review-progress-text');
  if(!bar||!label)return;
  bar.max=Math.max(1,total);bar.value=submitted;
  label.textContent=total?`${Math.round(100*submitted/total)}% · ${submitted}/${total} 已提交`:'请选择队列';
  bar.title=total?`已提交 ${submitted} 项，共 ${total} 项；跳过 ${skipped} 项不计为完成`:'请选择队列';
}
async function updateProgress(){
  if(!queue){setReviewProgress(0,0,0);return;}
  const activeQueue=queue;
  const p=await api('/api/queues/'+encodeURIComponent(activeQueue)+'/summary');
  if(queue!==activeQueue)return;
  const total=p.counts.reduce((sum,x)=>sum+Number(x.n),0);
  const submitted=p.counts.filter(x=>x.status==='submitted').reduce((sum,x)=>sum+Number(x.n),0);
  const skipped=p.counts.filter(x=>x.status==='skipped').reduce((sum,x)=>sum+Number(x.n),0);
  setReviewProgress(submitted,total,skipped);
}
async function refreshAutoReportAgreement(){
  if(!current||!reportAvailable||!['positive','negative','uncertain'].includes(payload.state)||payload.report_agreement_source==='reader')return;
  const taskId=current.task_id,state=payload.state,sequence=++reportCompareSequence;
  try{
    const result=await api('/api/tasks/'+taskId+'/report-consistency','POST',{state});
    if(sequence!==reportCompareSequence||current?.task_id!==taskId||payload.state!==state||payload.report_agreement_source==='reader'||submitting)return;
    const suggestedAgreement=result.agreement==='uncertain'?null:result.agreement;
    const changed=payload.report_agreement!==suggestedAgreement||payload.report_agreement_reason!==result.reason;
    payload.report_agreement=suggestedAgreement;
    payload.report_agreement_source='auto_v1';
    payload.report_agreement_reason=result.reason;
    payload.report_consistency_version=result.version;
    payload.report_consistency_scope=result.scope;
    if(changed&&!readOnly&&dirty)change(()=>{});
    renderPayload();
  }catch(_){if(current?.task_id===taskId)document.getElementById('report-agreement-hint').textContent='自动比较暂不可用，请人工判断。';}
}
function renderPayload(){
  document.querySelectorAll('input[name=state]').forEach(el=>el.checked=el.value===payload.state);
  document.querySelectorAll('input[name=report-agreement]').forEach(el=>el.checked=el.value===payload.report_agreement);
  const comparisonHint=document.getElementById('report-agreement-hint');
  if(comparisonHint)comparisonHint.textContent=payload.report_agreement==='uncertain'?'旧版标注记录为无法判断；可修订为一致或不一致。':payload.report_agreement_source==='reader'?'医生已手动修改判断；可再次选择。':payload.report_agreement_source==='auto_v1'?({positive_not_mentioned:payload.report_consistency_scope==='conclusion'?'自动提示：已标阳性，但报告结论未提及该疾病；可手动修改。':'自动提示：已标阳性，但报告正文未提及该疾病；可手动修改。',states_conflict:'自动提示：标注状态与报告疾病表述相反；请核对，可手动修改。',states_match:'自动提示：疾病状态与报告表述一致；可手动修改。',uncertain_or_conflicting:'自动提示：报告表述疑似、既往或冲突；如需记录一致性，请手动选择。',not_mentioned_is_not_negative:'自动提示：报告未提及不等于明确阴性；如需记录一致性，请手动选择。'}[payload.report_agreement_reason]||'自动比较结果仅供复核，可手动修改。'):'自动比较结果将在选择疾病状态后显示；可手动修改。';
  document.getElementById('read-complete').checked=!!payload.read_complete;
  document.getElementById('read-complete-control').hidden=payload.state!=='negative';
  document.getElementById('box-note').textContent=payload.rejected_lesions?.length?`本例 ${payload.rejected_lesions.length} 处新勾画完全落在五肺叶伪标签外，已从有效标注移除并保留草稿记录。`:'肺内任务的新勾画若完全落在五肺叶伪标签外，会从当前草稿移除并保留记录。';
  syncSubmitState();
  document.getElementById('lesions').innerHTML=payload.lesions.map((l,i)=>{const inferred=maskData?inferLobes(maskData,l):null;const names=inferred?.lobes.map(x=>`${x.name} ${Math.round(x.fraction*100)}%`).join('、')||'未落入五肺叶/伪标签未就绪';return `<div class="lesion"><strong>${escapeHtml(l.kind)} ${i+1}</strong> RAS ${l.ras_mm.map(x=>x.toFixed(1)).join(', ')} · 医生部位：${escapeHtml(l.region||'未填写')} <div class="pseudo-lobe">nnUNet肺叶提示：${escapeHtml(names)}</div><button data-focus="${i}">定位</button> <button data-delete="${i}">删除</button></div>`;}).join('');
  document.getElementById('lesion-count').textContent=String(payload.lesions.length);
  document.querySelectorAll('[data-delete]').forEach(el=>{el.disabled=readOnly;el.onclick=()=>{if(readOnly)return;const removed=payload.lesions.splice(Number(el.dataset.delete),1)[0];blockPreviews.delete(removed.id);if(selectedLesionId===removed.id)selectedLesionId=payload.lesions[0]?.id||null;change(()=>{});renderPayload();};});
  document.querySelectorAll('[data-focus]').forEach(el=>el.onclick=()=>{const lesion=payload.lesions[Number(el.dataset.focus)];if(fallback)focusFallback(fallback,lesion.ras_mm);else if(nv?.volumes?.length){nv.scene.crosshairPos=nv.mm2frac(lesion.ras_mm);nv.drawScene();if(gpuOverlay)renderGpuOverlay(gpuOverlay);}status('已定位到标注层面');});
  if(fallback)setFallbackAnnotations(fallback,payload.lesions);
  if(gpuOverlay)setGpuAnnotations(gpuOverlay,payload.lesions);
  renderBlockEditor();
}
function addPoint(){if(!point)return status('先在 CT 中选择位置',true);addLesion('point',[...point]);}
function discardOutsideLobeDraftMarks(){
  if(!maskData||!current||readOnly||!LOBE_BOUND_DISEASES.has(current.disease))return;
  const {kept,removed}=partitionLobeMarks(maskData,current.disease,payload.lesions);
  if(!removed.length)return;
  payload.lesions=kept;
  payload.rejected_lesions??=[];
  for(const lesion of removed){payload.rejected_lesions.push({id:lesion.id,kind:lesion.kind,ras_mm:lesion.ras_mm,...(lesion.ras_hi_mm?{ras_hi_mm:lesion.ras_hi_mm}:{}),reason:'outside_lobe_pseudomask'});blockPreviews.delete(lesion.id);}
  selectedLesionId=kept[0]?.id||null;
  change(()=>{});
  status(`${removed.length} 处肺叶外勾画已从有效标注移除，原坐标留在草稿记录`,true);
}
async function addLesion(kind,lo,hi){
  if(!current||readOnly)return;
  const taskId=current.task_id;
  const lesion={id:'lesion_'+crypto.randomUUID().replaceAll('-',''),kind,ras_mm:lo,region:document.getElementById('region').value};
  if(hi)lesion.ras_hi_mm=hi;
  if(LOBE_BOUND_DISEASES.has(current.disease)&&current.mask_available){
    if(!maskData&&maskPromise){status('正在核对肺叶伪标签');await maskPromise;}
    if(current?.task_id!==taskId||readOnly||submitting)return;
    if(classifyLobeMark(maskData,current.disease,lesion)==='reject'){
      payload.rejected_lesions??=[];
      payload.rejected_lesions.push({id:lesion.id,kind,ras_mm:lo,...(hi?{ras_hi_mm:hi}:{}),reason:'outside_lobe_pseudomask'});
      change(()=>{});renderPayload();
      if(!revisionMode)await saveDraft();
      status('新勾画未落入五肺叶伪标签，已从有效标注移除；原坐标保留在草稿记录',true);
      return;
    }
    if(!maskData)status('肺叶伪标签未就绪；本次勾画未做肺叶外校验',true);
  }
  payload.lesions.push(lesion);selectedLesionId=lesion.id;
  change(p=>{if(p.state!=='positive'){p.report_agreement=null;p.report_agreement_source=null;p.report_agreement_reason=null;}p.state='positive';p.read_complete=true;});
  renderPayload();status(revisionMode?'已勾画，疾病状态自动设为阳性 · 修订未提交':'已勾画，疾病状态自动设为阳性 · 未保存');
  refreshAutoReportAgreement();
  loadOneBlockPreview(taskId,lesion);
  loadSelectedFinePreview();
}
async function loadOneBlockPreview(taskId,lesion){
  try{const result=await api('/api/tasks/'+taskId+'/jepa-preview','POST',{ras_mm:lesion.ras_mm,ras_hi_mm:lesion.ras_hi_mm||null});
    if(current?.task_id===taskId&&payload.lesions.some(x=>x.id===lesion.id)){blockPreviews.set(lesion.id,result);renderBlockEditor();}}
  catch(_){if(current?.task_id===taskId)document.getElementById('block-summary').textContent='JEPA 块预览暂不可用；原 CT 标注已保留';}
}
async function loadExistingBlockPreviews(taskId,lesions){
  for(let i=0;i<lesions.length;i+=40){
    const batch=lesions.slice(i,i+40);
    try{const result=await api('/api/tasks/'+taskId+'/jepa-preview-batch','POST',{lesions:batch.map(x=>({ras_mm:x.ras_mm,ras_hi_mm:x.ras_hi_mm||null}))});
      if(current?.task_id!==taskId)return;
      batch.forEach((lesion,j)=>blockPreviews.set(lesion.id,result.previews[j]));renderBlockEditor();}
    catch(_){if(current?.task_id===taskId)document.getElementById('block-summary').textContent='既往块预览暂不可用；原 CT 标注已保留';return;}
  }
}
function selectedPreview(){return selectedGridSize===4?blockPreviews.get(selectedLesionId):finePreviews.get(selectedLesionId+':'+selectedGridSize);}
async function loadSelectedFinePreview(){
  if(selectedGridSize===4||!current||!selectedLesionId)return;
  const lesion=payload.lesions.find(x=>x.id===selectedLesionId),taskId=current.task_id,gridSize=selectedGridSize;
  if(!lesion||finePreviews.has(lesion.id+':'+gridSize))return;
  try{const result=await api('/api/tasks/'+taskId+'/jepa-preview','POST',{ras_mm:lesion.ras_mm,ras_hi_mm:lesion.ras_hi_mm||null,grid_size:gridSize});
    if(current?.task_id===taskId){finePreviews.set(lesion.id+':'+gridSize,result);if(selectedLesionId===lesion.id&&selectedGridSize===gridSize)renderBlockEditor();}}
  catch(_){if(current?.task_id===taskId)document.getElementById('block-summary').textContent='细网格预览失败；原 CT 标注与真实 JEPA 块均已保留';}
}
function selectedBlockData(){
  const lesion=payload.lesions.find(x=>x.id===selectedLesionId),preview=selectedPreview();
  const view=preview?.views.find(x=>x.view===selectedBlockView);
  const manual=selectedGridSize===4&&preview&&lesion?.jepa_blocks?.model_version===preview.model_version?lesion.jepa_blocks.views?.[selectedBlockView]:undefined;
  return {lesion,preview,blocks:manual||automaticBlocks(view),manual:manual!==undefined};
}
function renderBlockEditor(){
  const panel=document.getElementById('block-editor');if(!panel)return;
  panel.hidden=!payload.lesions.length;if(panel.hidden)return;
  if(!payload.lesions.some(x=>x.id===selectedLesionId))selectedLesionId=payload.lesions[0].id;
  const lesionSelect=document.getElementById('block-lesion');
  lesionSelect.innerHTML=payload.lesions.map((x,i)=>`<option value="${escapeHtml(x.id)}">病灶 ${i+1} · ${x.kind==='box'?'框':'点'}</option>`).join('');lesionSelect.value=selectedLesionId;
  const preview=selectedPreview();
  const viewSelect=document.getElementById('block-view');
  viewSelect.innerHTML=(preview?.views||[]).map(x=>`<option value="${x.view}">${x.view}</option>`).join('');
  if(!preview?.views.some(x=>x.view===selectedBlockView))selectedBlockView=preview?.views[0]?.view||'global';
  viewSelect.value=selectedBlockView;
  const key=selectedLesionId+':'+selectedGridSize+':'+selectedBlockView+':'+(preview?'ready':'loading');
  if(key!==renderedBlockSelectionKey){selectedBlockLayer=selectedBlockData().blocks[0]?.[2]||0;renderedBlockSelectionKey=key;}
  renderBlockGrid();
  renderBullaLocation();
}
function renderBullaLocation(){
  const section=document.getElementById('bulla-location');if(!section)return;
  section.hidden=current?.disease!=='pulmonary_bulla';if(section.hidden)return;
  const lesion=payload.lesions.find(x=>x.id===selectedLesionId);
  if(!lesion)return;
  const codes=['LUL','LLL','RUL','RML','RLL'];
  const suggested=maskData?inferLobes(maskData,lesion).lobes:[];
  document.getElementById('bulla-suggestion').textContent=suggested.length?'伪标签建议：'+suggested.map(x=>x.name).join('、'):'伪标签未提供可靠肺叶建议；可手动选择';
  const selected=lesion.bulla_location_confirmed?lesion.bulla_lobes||[]:suggested.map(x=>codes[x.id-1]);
  document.getElementById('bulla-lobe-choices').innerHTML=codes.map((code,i)=>`<label><input type="checkbox" data-bulla-lobe="${code}" ${selected.includes(code)?'checked':''} ${readOnly?'disabled':''}>${LOBES[i+1].name}</label>`).join('');
  document.getElementById('bulla-confirm').disabled=readOnly;
  document.getElementById('bulla-confirmed').textContent=lesion.bulla_location_confirmed?'已确认 · 待本例提交/修订后进入训练 CSV':'未确认；不会进入训练位置 CSV';
}
function confirmBullaLobes(){
  if(readOnly||current?.disease!=='pulmonary_bulla')return;
  const lesion=payload.lesions.find(x=>x.id===selectedLesionId);
  if(!lesion)return;
  const codes=[...document.querySelectorAll('#bulla-lobe-choices input:checked')].map(x=>x.dataset.bullaLobe);
  if(!codes.length)return status('请至少选择一个肺叶；位置无法确定可不确认',true);
  lesion.bulla_lobes=codes;lesion.bulla_location_confirmed=true;
  change(()=>{});renderBullaLocation();status('肺叶位置已记录，提交本例后写入受控训练 CSV');
}
function renderBlockGrid(){
  const surface=document.getElementById('block-grid'),summary=document.getElementById('block-summary');
  if(!surface||!summary)return;
  const {preview,blocks,manual}=selectedBlockData();
  if(!preview){surface.replaceChildren();summary.textContent=selectedGridSize===4?'正在计算 JEPA 模型块':'正在计算标注细网格';renderBlock3D();return;}
  const gridSize=preview.grid_size||4;
  document.getElementById('block-layer').max=String(gridSize-1);
  document.getElementById('block-layer').value=String(selectedBlockLayer);
  document.getElementById('block-layer-value').textContent=(selectedBlockLayer+1)+'/'+gridSize;
  const keys=new Set(blocks.map(blockKey));
  surface.style.setProperty('--grid-n',String(gridSize));surface.classList.toggle('fine-grid',gridSize>4);
  surface.innerHTML=Array.from({length:gridSize*gridSize},(_,i)=>{const x=i%gridSize,y=Math.floor(i/gridSize),key=blockKey([x,y,selectedBlockLayer]);return `<button type="button" class="${keys.has(key)?'active':''}" data-block="${key}" ${readOnly||gridSize>4?'disabled':''} aria-label="块 ${key}">${gridSize===4?`${x+1},${y+1}`:''}</button>`;}).join('');
  if(gridSize===4)surface.querySelectorAll('[data-block]').forEach(el=>el.onclick=()=>{const data=selectedBlockData(),key=el.dataset.block,currentKeys=new Set(data.blocks.map(blockKey));if(currentKeys.has(key))currentKeys.delete(key);else currentKeys.add(key);setSelectedBlocks([...currentKeys].map(fromKey));});
  for(const id of ['block-expand','block-shrink','block-auto'])document.getElementById(id).disabled=readOnly||gridSize>4;
  summary.textContent=gridSize===4?`${selectedBlockView} · ${blocks.length} 个真实 JEPA 模型块 · ${manual?'人工调整':'自动相交'}。`:`${selectedBlockView} · ${gridSize}³ 标注细网格 · ${blocks.length} 格；仅供精细空间核对，模型仍使用 4³ token。`;
  renderBlock3D();
}
function renderBlock3D(){
  if(!nv)return;
  document.getElementById('block-grid').dataset.meshTriangles='0';
  if(blockMesh){nv.removeMesh(blockMesh);blockMesh=null;}
  if(!isRender3D)return;
  const {preview,blocks}=selectedBlockData();
  if(selectedGridSize>4&&blocks.length>128){document.getElementById('block-grid').dataset.meshTriangles='0';return;}
  const view=preview?.views.find(x=>x.view===selectedBlockView);
  const geometry=buildBlockMesh(view,blocks);
  if(!geometry.points.length)return;
  try{
    blockMesh=new NVMesh(new Float32Array(geometry.points),new Uint32Array(geometry.triangles),
      'JEPA block preview',new Uint8Array([0,229,255,255]),.42,true,nv.gl);
    nv.addMesh(blockMesh);nv.drawScene();
    document.getElementById('block-grid').dataset.meshTriangles=String(geometry.triangles.length/3);
  }catch(_){blockMesh=null;status('3D 块高亮不可用；二维块选择仍可使用',true);}
}
function setSelectedBlocks(blocks){
  const {lesion,preview}=selectedBlockData();if(!lesion||!preview||readOnly||selectedGridSize!==4)return;
  if(!lesion.jepa_blocks||lesion.jepa_blocks.model_version!==preview.model_version)lesion.jepa_blocks={model_version:preview.model_version,views:{}};
  lesion.jepa_blocks.views[selectedBlockView]=blocks.sort(compareBlocks);
  change(()=>{});renderBlockGrid();
}
function editBlocks(action){
  const {lesion,blocks}=selectedBlockData();if(!lesion||readOnly||selectedGridSize!==4)return;
  if(action==='auto'){
    if(lesion.jepa_blocks?.views){delete lesion.jepa_blocks.views[selectedBlockView];if(!Object.keys(lesion.jepa_blocks.views).length)delete lesion.jepa_blocks;change(()=>{});renderBlockGrid();}
  }else setSelectedBlocks(action==='expand'?expandBlocks(blocks):shrinkBlocks(blocks));
}
function startRevision(){
  if(current?.status!=='submitted')return;
  const reason=prompt('修订原因（保留原标注版本）','修正疾病状态或标注');if(!reason||reason.trim().length<2)return;
  revisionMode=true;revisionReason=reason.trim();readOnly=false;
  if(payload.report_agreement==='uncertain'){payload.report_agreement=null;payload.report_agreement_source=null;payload.report_agreement_reason='legacy_uncertain';}
  for(const id of ['submit','add-point','box-start','box-end'])document.getElementById(id).disabled=false;
  document.querySelectorAll('input[name=state],input[name=report-agreement],#read-complete,#region').forEach(el=>el.disabled=false);
  document.getElementById('submit').textContent='提交修订';document.getElementById('save').disabled=true;
  document.getElementById('revise-case').hidden=true;discardOutsideLobeDraftMarks();renderPayload();syncSubmitState();status('修订中 · 原版本保留');
}
async function previewJepa(){
  if(!current)return status('先加载CT',true);
  const latest=payload.lesions.at(-1),lo=latest?.ras_mm||point;
  if(!lo)return status('先在CT中选点或画框',true);
  try{const r=await api('/api/tasks/'+current.task_id+'/jepa-preview','POST',{ras_mm:lo,ras_hi_mm:latest?.ras_hi_mm||null});
    document.getElementById('grid-info').textContent=`${r.model_version} · ${r.views.map(v=>`${v.view}: ${Array.isArray(v.mapping)?v.mapping.length+'个相交块':(v.mapping.inside?'块 '+v.mapping.block_ijk.join(','):'输入范围外')}`).join('；')}。目标块仅预览空间对应，不表示病灶分割。`;
    status('JEPA空间预览已生成');
  }catch(err){status('JEPA预览失败：'+err.message,true);}
}
async function saveDraft(){
  if(revisionMode)return status('请使用“提交修订”保存此例',true);
  if(!current||!dirty||submitting)return;
  if(draftSavePromise){await draftSavePromise;if(current&&dirty&&!submitting)return saveDraft();return;}
  clearTimeout(autosaveTimer);
  const taskId=current.task_id,version=current.version,serial=editSerial,snapshot=structuredClone(payload);
  status('正在保存');
  const request=api('/api/tasks/'+taskId+'/draft','PUT',{version,payload:snapshot}).then(r=>{
    if(current?.task_id!==taskId)return;
    current.version=r.version;
    if(editSerial===serial){dirty=false;status('已保存');}
    else {dirty=true;status('有新修改待保存');}
  }).catch(err=>{if(current?.task_id===taskId){dirty=true;status('保存失败：'+err.message,true);}throw err;});
  draftSavePromise=request;
  try{await request;}finally{if(draftSavePromise===request)draftSavePromise=null;}
}
async function submit(){
  if(submitting)return;
  if(readOnly)return status('已提交病例为只读',true);
  if(!current||!caseReady)return status('影像尚未加载完成，不能提交',true);
  clearTimeout(autosaveTimer);submitting=true;readOnly=true;syncSubmitState();status('正在提交，请稍候');
  try{
    if(draftSavePromise)await draftSavePromise;
    const taskId=current.task_id,snapshot=structuredClone(payload);
    const endpoint=revisionMode?'revise':'submit';
    const request={version:current.version,payload:snapshot};
    if(revisionMode)request.reason=revisionReason;
    const result=await api('/api/tasks/'+taskId+'/'+endpoint,'POST',request);
    if(current?.task_id!==taskId)return;
    current.version=result.version;dirty=false;
    if(revisionMode){revisionMode=false;readOnly=true;current.status='submitted';document.getElementById('revise-case').hidden=false;for(const id of ['save','submit','add-point','box-start','box-end'])document.getElementById(id).disabled=true;document.getElementById('submit').textContent='提交并下一例';document.querySelectorAll('input[name=state],input[name=report-agreement],#read-complete,#region').forEach(el=>el.disabled=true);renderPayload();status('修订已保存，原版本保留');return;}
    status('已提交，正在进入下一例');submitting=false;await refreshTaskList();await nextCase();if(caseReady)status('上一例已提交 · 下一例已加载');
  }catch(err){readOnly=false;renderPayload();status(err.message==='VERSION_CONFLICT'?'提交未完成：草稿版本已变化，请先刷新本例并核对标注。':'提交失败：'+err.message,true);}
  finally{submitting=false;syncSubmitState();}
}

api('/api/me').then(workspace).catch(loginPage);
