// Transparent annotation surface over NiiVue. The CT remains rendered by WebGL.
export function createGpuOverlay(canvas, nv, onPoint, onBox, onHover, onZoom=()=>{}) {
  const view={canvas,nv,onPoint,onBox,onHover,onZoom,tool:'box',annotations:[],drag:null};
  const context=canvas.getContext('2d');
  if(!context)throw Error('GPU_MARKUP_CANVAS_UNAVAILABLE');
  view.context=context;
  const coordinates=e=>{
    const rect=canvas.getBoundingClientRect();
    const x=(e.clientX-rect.left)*nv.canvas.width/rect.width;
    const y=(e.clientY-rect.top)*nv.canvas.height/rect.height;
    const tile=nv.screenSlices?.find(s=>{const [left,top,width,height]=s.leftTopWidthHeight;return s.axCorSag<=2&&x>=Math.min(left,left+width)&&x<=Math.max(left,left+width)&&y>=Math.min(top,top+height)&&y<=Math.max(top,top+height);});
    if(!tile)return null;
    const frac=nv.canvasPos2frac([x,y]);
    if(!frac||frac.some(v=>!Number.isFinite(v)||v<0||v>1))return null;
    const ras=nv.frac2mm(frac);
    return ras?.every(Number.isFinite)?{ras,frac,xy:[x,y]}:null;
  };
  const pointerDown=e=>{if(e.button!==0)return;view.drag=coordinates(e);if(view.drag)canvas.setPointerCapture(e.pointerId);};
  const pointerMove=e=>{
    const c=coordinates(e);if(c)onHover(c.ras);
    if(view.drag){view.drag.end=c;renderGpuOverlay(view);}
  };
  const pointerUp=e=>{
    const start=view.drag,end=coordinates(e);view.drag=null;
    if(!end){renderGpuOverlay(view);return;}
    nv.scene.crosshairPos=end.frac;nv.drawScene();onPoint(end.ras);
    const moved=start&&Math.hypot(end.xy[0]-start.xy[0],end.xy[1]-start.xy[1])>6;
    if(moved&&view.tool==='box')onBox(start.ras,end.ras);
    if(!moved&&view.tool==='point')view.onAddPoint?.(end.ras);
    renderGpuOverlay(view);
  };
  const wheel=e=>{
    e.preventDefault();
    if(e.ctrlKey||e.metaKey){
      setGpuZoom(view,(nv.scene.pan2Dxyzmm?.[3]||1)*Math.exp(-e.deltaY*.008));return;
    }
    const c=coordinates(e);if(!c)return;
    const tile=nv.screenSlices?.find(s=>{
      const [x,y,w,h]=s.leftTopWidthHeight;
      return c.xy[0]>=Math.min(x,x+w)&&c.xy[0]<=Math.max(x,x+w)&&c.xy[1]>=Math.min(y,y+h)&&c.xy[1]<=Math.max(y,y+h);
    });
    const axis=tile?.axCorSag;
    if(axis===0||axis===1||axis===2){const delta=Math.sign(e.deltaY);const steps=[0,0,0];steps[2-axis]=delta;nv.moveCrosshairInVox(...steps);renderGpuOverlay(view);}
  };
  const resize=()=>renderGpuOverlay(view);
  canvas.addEventListener('pointerdown',pointerDown);canvas.addEventListener('pointermove',pointerMove);
  canvas.addEventListener('pointerup',pointerUp);canvas.addEventListener('wheel',wheel,{passive:false});
  window.addEventListener('resize',resize);
  view.dispose=()=>{canvas.removeEventListener('pointerdown',pointerDown);canvas.removeEventListener('pointermove',pointerMove);canvas.removeEventListener('pointerup',pointerUp);canvas.removeEventListener('wheel',wheel);window.removeEventListener('resize',resize);};
  renderGpuOverlay(view);
  return view;
}

function projected(view,ras,type){
  const frac=view.nv.mm2frac(ras);
  return view.nv.frac2canvasPosWithTile(frac,type);
}

export function renderGpuOverlay(view){
  const {canvas,context,nv}=view;
  if(!nv?.canvas)return;
  if(canvas.width!==nv.canvas.width||canvas.height!==nv.canvas.height){canvas.width=nv.canvas.width;canvas.height=nv.canvas.height;}
  context.clearRect(0,0,canvas.width,canvas.height);
  canvas.dataset.sliceCount=String(nv.screenSlices?.length||0);
  canvas.dataset.annotationCount=String(view.annotations.length);
  canvas.dataset.zoom=String(nv.scene.pan2Dxyzmm?.[3]||1);
  const first=nv.screenSlices?.find(s=>s.axCorSag<=2);
  if(first){const [x,y,w,h]=first.leftTopWidthHeight;canvas.dataset.tileCenter=JSON.stringify([x+w/2,y+h/2]);}
  if(view.annotations.length){const frac=nv.mm2frac(view.annotations[0].ras_mm);canvas.dataset.fracFinite=String(!!frac?.every(Number.isFinite));canvas.dataset.fracInside=String(!!frac?.every(v=>v>=0&&v<=1));canvas.dataset.directProjected=String(!!nv.frac2canvasPos(frac));}
  let markerCount=0;
  view.annotations.forEach((lesion,i)=>{
    for(const type of [0,1,2]){
      const a=projected(view,lesion.ras_mm,type);
      if(!a)continue;
      const b=lesion.kind==='box'&&lesion.ras_hi_mm?projected(view,lesion.ras_hi_mm,type):null;
      if(lesion.kind==='box'&&(!b||a.tileIndex!==b.tileIndex))continue;
      context.save();context.lineWidth=3;context.strokeStyle=b?'#00e5ff':'#42ff76';
      if(b){const x=Math.min(a.pos[0],b.pos[0]),y=Math.min(a.pos[1],b.pos[1]);const w=Math.max(4,Math.abs(a.pos[0]-b.pos[0])),h=Math.max(4,Math.abs(a.pos[1]-b.pos[1]));context.fillStyle='rgba(0,229,255,.18)';context.fillRect(x,y,w,h);context.strokeRect(x,y,w,h);}
      else{context.strokeRect(a.pos[0]-7,a.pos[1]-7,14,14);context.beginPath();context.moveTo(a.pos[0]-12,a.pos[1]);context.lineTo(a.pos[0]+12,a.pos[1]);context.moveTo(a.pos[0],a.pos[1]-12);context.lineTo(a.pos[0],a.pos[1]+12);context.stroke();}
      context.fillStyle=context.strokeStyle;context.font='bold 14px sans-serif';context.fillText(String(i+1),a.pos[0]+10,a.pos[1]-10);context.restore();
      markerCount++;
    }
  });
  if(view.drag?.end&&view.tool==='box'){
    const a=view.drag.xy,b=view.drag.end.xy;
    context.save();context.strokeStyle='#00d8ff';context.lineWidth=2;context.setLineDash([5,4]);context.strokeRect(Math.min(a[0],b[0]),Math.min(a[1],b[1]),Math.abs(a[0]-b[0]),Math.abs(a[1]-b[1]));context.restore();
  }
  canvas.dataset.markerCount=String(markerCount);
}

export function setGpuAnnotations(view,annotations){view.annotations=Array.isArray(annotations)?annotations:[];renderGpuOverlay(view);}
export function cancelGpuDrawing(view){view.drag=null;renderGpuOverlay(view);}
export function setGpuZoom(view,zoom,render3D=false){const value=Math.max(1,Math.min(8,Number(zoom)||1));view.nv.scene.pan2Dxyzmm[3]=value;if(render3D)view.nv.scene.volScaleMultiplier=value;view.nv.drawScene();renderGpuOverlay(view);view.onZoom(value);}
export function setGpuTool(view,tool,onAddPoint){if(!['point','box'].includes(tool))throw Error('ANNOTATION_TOOL_INVALID');view.tool=tool;view.onAddPoint=onAddPoint;renderGpuOverlay(view);}
