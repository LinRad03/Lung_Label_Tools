import * as nifti from 'nifti-reader-js';
import {LOBES,maskLabel} from './lobe_mask.js';

// CPU canvas viewer. The affine check prevents silently displaying oblique data
// with incorrect direction labels or annotation coordinates.
export async function createFallback(canvas, imageUrl, meta, onPoint, onHover=()=>{}, onBox=()=>{}, onZoom=()=>{}) {
  const response = await fetch(imageUrl, {credentials: 'same-origin'});
  if (!response.ok) throw Error('CT_LOAD_FAILED');
  let buffer = await response.arrayBuffer();
  if (nifti.isCompressed(buffer)) buffer = await nifti.decompressAsync(buffer);
  if (!nifti.isNIFTI(buffer)) throw Error('UNSUPPORTED_CT_FORMAT');
  const header = nifti.readHeader(buffer);
  const voxels = nifti.readImage(header, buffer);
  const shape = header.dims.slice(1, 4).map(Number);
  if (shape.some((n, i) => n !== meta.shape_ijk[i])) throw Error('CT_SHAPE_MISMATCH');
  const affine = meta.affine_ras;
  const physicalAxes = [0, 1, 2].map(row => {
    const magnitudes = [0, 1, 2].map(col => Math.abs(affine[row][col]));
    const axis = magnitudes.indexOf(Math.max(...magnitudes));
    if (!magnitudes[axis] || magnitudes.some((v, i) => i !== axis && v / magnitudes[axis] > 0.01))
      throw Error('OBLIQUE_AFFINE_REQUIRES_WEBGL');
    return axis;
  });
  if (new Set(physicalAxes).size !== 3) throw Error('INVALID_CT_AFFINE');
  const view = {canvas, shape, affine, axes: physicalAxes, header, data: new DataView(voxels),
    index: shape.map(n => Math.floor(n / 2)), window: [-1350, 150], mode: 'axial', onPoint, onHover, onBox,
    dragStart:null, dragEnd:null, lastDragDraw:0, annotations:[], tool:'box', onAddPoint:()=>{},
    mask:null,showMask:false,zoom:1,onZoom,crosshairUntil:0,crosshairTimer:null,pan:[0,0]};
  const x = canvas.getContext('2d', {alpha: false});
  if (!x) throw Error('CANVAS_2D_UNAVAILABLE');
  view.context = x;
  const onWheel = event => {
    event.preventDefault();
    if(event.ctrlKey||event.metaKey){setFallbackZoom(view,view.zoom*Math.exp(-event.deltaY*.008));return;}
    const pane = paneAt(view, event.offsetX, event.offsetY);
    if (!pane) return;
    const axis = view.axes[pane.slicePhysicalAxis];
    view.index[axis] = Math.min(shape[axis] - 1, Math.max(0, view.index[axis] + Math.sign(event.deltaY)));
    render(view);
  };
  const physicalAt = event => {
    const pane = paneAt(view, event.offsetX, event.offsetY);
    if (!pane) return null;
    const i = Math.max(0, Math.min(pane.width - 1, Math.floor((event.offsetX - pane.left) / pane.drawWidth * pane.width)));
    const j = Math.max(0, Math.min(pane.height - 1, Math.floor((event.offsetY - pane.top) / pane.drawHeight * pane.height)));
    const p = [...view.index];
    p[view.axes[pane.xPhysicalAxis]] = pane.xSign > 0 ? i : pane.width - 1 - i;
    p[view.axes[pane.yPhysicalAxis]] = pane.ySign > 0 ? pane.height - 1 - j : j;
    return {pane, index:p, ras:[0, 1, 2].map(row => affine[row][3] + p.reduce((sum, v, col) => sum + affine[row][col] * v, 0)),
            screen:[event.offsetX,event.offsetY]};
  };
  const onPointerDown = event => {
    if (event.button !== 0) return;
    view.dragStart = physicalAt(event);
    view.dragEnd = view.dragStart;
    if (view.dragStart) canvas.setPointerCapture(event.pointerId);
  };
  const onPointerMove = event => {
    const here = physicalAt(event);
    if (here) view.onHover(here.ras);
    if (view.dragStart && here && here.pane.name === view.dragStart.pane.name) {
      view.dragEnd=here;
      if (performance.now()-view.lastDragDraw>50) {render(view);view.lastDragDraw=performance.now();}
    }
  };
  const onPointerUp = event => {
    const here = physicalAt(event);
    const start = view.dragStart;
    view.dragStart=null;view.dragEnd=null;
    if (!here) {render(view);return;}
    view.index=here.index;
    const moved = start && start.pane.name===here.pane.name && Math.hypot(here.screen[0]-start.screen[0],here.screen[1]-start.screen[1])>6;
    if (moved && view.tool==='box') view.onBox(start.ras,here.ras);
    if (!moved && view.tool==='point') view.onAddPoint(here.ras);
    view.onPoint(here.ras);
    view.crosshairUntil=performance.now()+1200;
    if(canvas.dataset)canvas.dataset.crosshairVisible='yes';
    clearTimeout(view.crosshairTimer);
    view.crosshairTimer=setTimeout(()=>{view.crosshairUntil=0;if(canvas.dataset)canvas.dataset.crosshairVisible='no';render(view);},1200);
    render(view);
  };
  const onResize = () => render(view);
  canvas.addEventListener('wheel', onWheel, {passive: false});
  canvas.addEventListener('pointerdown', onPointerDown);
  canvas.addEventListener('pointermove', onPointerMove);
  canvas.addEventListener('pointerup', onPointerUp);
  window.addEventListener('resize', onResize);
  view.dispose = () => {
    clearTimeout(view.crosshairTimer);
    canvas.removeEventListener('wheel', onWheel);
    canvas.removeEventListener('pointerdown', onPointerDown);
    canvas.removeEventListener('pointermove', onPointerMove);
    canvas.removeEventListener('pointerup', onPointerUp);
    window.removeEventListener('resize', onResize);
  };
  render(view);
  return view;
}

export function setFallbackSlice(view, physicalAxis, sliceIndex) {
  const axis=view.axes[physicalAxis];
  view.index[axis]=Math.max(0,Math.min(view.shape[axis]-1,Math.round(sliceIndex)));
  render(view);
}
export function panFallback(view, dx, dy) {view.pan[0]+=dx;view.pan[1]+=dy;view.canvas.dataset.panOffset=view.pan.join(',');render(view);}
export function cancelFallbackDrawing(view) {view.dragStart=null;view.dragEnd=null;render(view);}

function sample(view, index) {
  const n = index[0] + view.shape[0] * (index[1] + view.shape[1] * index[2]);
  const d = view.data, code = view.header.datatypeCode, le = view.header.littleEndian;
  let value;
  if (code === 2) value = d.getUint8(n);
  else if (code === 256) value = d.getInt8(n);
  else if (code === 4) value = d.getInt16(n * 2, le);
  else if (code === 512) value = d.getUint16(n * 2, le);
  else if (code === 8) value = d.getInt32(n * 4, le);
  else if (code === 16) value = d.getFloat32(n * 4, le);
  else if (code === 64) value = d.getFloat64(n * 8, le);
  else throw Error('UNSUPPORTED_CT_DATATYPE');
  const slope = view.header.scl_slope || 1;
  return value * slope + (view.header.scl_inter || 0);
}

function panes(view) {
  const specs = {
    axial: [0, 1, 2, '轴位'],
    coronal: [0, 2, 1, '冠状位'],
    sagittal: [1, 2, 0, '矢状位'],
  };
  const names = view.mode === 'multi' ? ['axial', 'coronal', 'sagittal'] : [view.mode];
  const paneWidth = view.canvas.clientWidth / names.length;
  const paneHeight = view.canvas.clientHeight;
  return names.map((name, k) => {
    const [xp, yp, sp, label] = specs[name];
    const width = view.shape[view.axes[xp]], height = view.shape[view.axes[yp]];
    const scale = Math.min((paneWidth - 12) / width, (paneHeight - 35) / height)*view.zoom;
    const drawWidth = width * scale, drawHeight = height * scale;
    return {name, label, xPhysicalAxis: xp, yPhysicalAxis: yp, slicePhysicalAxis: sp,
      xSign: Math.sign(view.affine[xp][view.axes[xp]]), ySign: Math.sign(view.affine[yp][view.axes[yp]]),
      width, height, drawWidth, drawHeight,
      viewportLeft:k*paneWidth,viewportWidth:paneWidth,
      left: k * paneWidth + (paneWidth - drawWidth) / 2 + view.pan[0],
      top: (paneHeight - drawHeight) / 2 + view.pan[1]};
  });
}

function paneAt(view, x, y) {
  return panes(view).find(p => x >= p.viewportLeft && x < p.viewportLeft+p.viewportWidth &&
    x >= p.left && x < p.left + p.drawWidth && y >= Math.max(0,p.top) && y < Math.min(view.canvas.clientHeight,p.top+p.drawHeight));
}

function rasToIndex(view, ras) {
  if (!Array.isArray(ras) || ras.length !== 3 || !ras.every(Number.isFinite)) return null;
  const ijk = [0, 0, 0];
  for (let physical=0; physical<3; physical++) {
    const axis=view.axes[physical];
    ijk[axis]=(ras[physical]-view.affine[physical][3])/view.affine[physical][axis];
  }
  return ijk;
}

function screenPosition(view, pane, index) {
  const ix=index[view.axes[pane.xPhysicalAxis]], iy=index[view.axes[pane.yPhysicalAxis]];
  return [pane.left+(pane.xSign>0?ix+.5:pane.width-ix-.5)/pane.width*pane.drawWidth,
          pane.top+(pane.ySign>0?pane.height-iy-.5:iy+.5)/pane.height*pane.drawHeight];
}

function drawAnnotations(view, pane, context) {
  const sliceAxis=view.axes[pane.slicePhysicalAxis], current=view.index[sliceAxis];
  view.annotations.forEach((lesion, n) => {
    const lo=rasToIndex(view, lesion.ras_mm);
    if (!lo) return;
    const hi=lesion.kind==='box' ? rasToIndex(view, lesion.ras_hi_mm) : null;
    if (lesion.kind==='box' && !hi) return;
    const extent=hi ? [Math.min(lo[sliceAxis],hi[sliceAxis]),Math.max(lo[sliceAxis],hi[sliceAxis])] : [lo[sliceAxis],lo[sliceAxis]];
    if (current < extent[0]-.5 || current > extent[1]+.5) return;
    const a=screenPosition(view,pane,lo);
    context.save();
    context.lineWidth=3;
    context.strokeStyle=lesion.kind==='box'?'#00e5ff':'#42ff76';
    if (hi) {
      const b=screenPosition(view,pane,hi);
      const x=Math.min(a[0],b[0]),y=Math.min(a[1],b[1]),w=Math.abs(a[0]-b[0]),h=Math.abs(a[1]-b[1]);
      context.fillStyle='rgba(0,229,255,.18)';
      context.fillRect(x,y,Math.max(w,4),Math.max(h,4));
      context.strokeRect(x,y,Math.max(w,4),Math.max(h,4));
    } else {
      context.strokeRect(a[0]-7,a[1]-7,14,14);
      context.beginPath();context.moveTo(a[0]-12,a[1]);context.lineTo(a[0]+12,a[1]);
      context.moveTo(a[0],a[1]-12);context.lineTo(a[0],a[1]+12);context.stroke();
    }
    context.fillStyle=context.strokeStyle;
    context.font='bold 14px sans-serif';
    context.fillText(String(n+1),a[0]+10,a[1]-10);
    context.restore();
  });
}

export function render(view) {
  const canvas = view.canvas, context = view.context, ratio = window.devicePixelRatio || 1;
  if(canvas.dataset)canvas.dataset.zoom=String(view.zoom);
  if(canvas.dataset)canvas.dataset.crosshairVisible=view.crosshairUntil>performance.now()?'yes':'no';
  const pixelWidth = Math.max(1, Math.round(canvas.clientWidth * ratio));
  const pixelHeight = Math.max(1, Math.round(canvas.clientHeight * ratio));
  if (canvas.width !== pixelWidth || canvas.height !== pixelHeight) {canvas.width = pixelWidth; canvas.height = pixelHeight;}
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  context.fillStyle = '#000'; context.fillRect(0, 0, canvas.clientWidth, canvas.clientHeight);
  for (const pane of panes(view)) {
    context.save();context.beginPath();context.rect(pane.viewportLeft,0,pane.viewportWidth,canvas.clientHeight);context.clip();
    const image = context.createImageData(pane.width, pane.height);
    const index = [...view.index];
    for (let y = 0; y < pane.height; y++) for (let x = 0; x < pane.width; x++) {
      index[view.axes[pane.xPhysicalAxis]] = pane.xSign > 0 ? x : pane.width - 1 - x;
      index[view.axes[pane.yPhysicalAxis]] = pane.ySign > 0 ? pane.height - 1 - y : y;
      const value = sample(view, index);
      const gray = Math.max(0, Math.min(255, Math.round(255 * (value - view.window[0]) / (view.window[1] - view.window[0]))));
      const offset = 4 * (x + pane.width * y);
      image.data[offset] = gray; image.data[offset + 1] = gray; image.data[offset + 2] = gray; image.data[offset + 3] = 255;
      if(view.showMask && view.mask){
        const label=maskLabel(view.mask,index),lobe=LOBES[label];
        if(lobe){for(let channel=0;channel<3;channel++)image.data[offset+channel]=Math.round(gray*.6+lobe.color[channel]*.4);}
      }
    }
    const tile = document.createElement('canvas'); tile.width = pane.width; tile.height = pane.height;
    tile.getContext('2d').putImageData(image, 0, 0);
    context.imageSmoothingEnabled = true;
    context.drawImage(tile, pane.left, pane.top, pane.drawWidth, pane.drawHeight);
    if(view.crosshairUntil>performance.now()){
      const cxIndex = view.index[view.axes[pane.xPhysicalAxis]];
      const cyIndex = view.index[view.axes[pane.yPhysicalAxis]];
      const cx = pane.left + (pane.xSign > 0 ? cxIndex + 0.5 : pane.width - cxIndex - 0.5) / pane.width * pane.drawWidth;
      const cy = pane.top + (pane.ySign > 0 ? pane.height - cyIndex - 0.5 : cyIndex + 0.5) / pane.height * pane.drawHeight;
      context.strokeStyle = '#ffb000'; context.lineWidth = 1; context.beginPath();
      context.moveTo(cx, pane.top); context.lineTo(cx, pane.top + pane.drawHeight);
      context.moveTo(pane.left, cy); context.lineTo(pane.left + pane.drawWidth, cy); context.stroke();
    }
    if(view.tool==='box' && view.dragStart && view.dragEnd && view.dragStart.pane.name===pane.name){
      const a=view.dragStart.screen,b=view.dragEnd.screen;
      context.save();context.strokeStyle='#00d8ff';context.lineWidth=2;context.setLineDash([5,4]);
      context.strokeRect(Math.min(a[0],b[0]),Math.min(a[1],b[1]),Math.abs(a[0]-b[0]),Math.abs(a[1]-b[1]));context.restore();
    }
    context.fillStyle = '#fff'; context.font = '14px sans-serif';
    context.fillText(`${pane.label} · ${view.index[view.axes[pane.slicePhysicalAxis]] + 1}/${view.shape[view.axes[pane.slicePhysicalAxis]]}`, pane.left + 4, pane.top + 18);
    const sign = pane.xSign;
    context.fillText(pane.xPhysicalAxis === 0 ? (sign > 0 ? 'L' : 'R') : (sign > 0 ? 'P' : 'A'), pane.left + 4, pane.top + pane.drawHeight / 2);
    context.fillText(pane.xPhysicalAxis === 0 ? (sign > 0 ? 'R' : 'L') : (sign > 0 ? 'A' : 'P'), pane.left + pane.drawWidth - 18, pane.top + pane.drawHeight / 2);
    drawAnnotations(view,pane,context);
    context.restore();
  }
}

export function setFallbackWindow(view, range) {view.window = range; render(view);}
export function setFallbackZoom(view, zoom) {view.zoom=Math.max(1,Math.min(8,Number(zoom)||1));render(view);view.onZoom(view.zoom);}
export function setFallbackTool(view, tool, onAddPoint) {
  if (!['point','box'].includes(tool)) throw Error('ANNOTATION_TOOL_INVALID');
  view.tool=tool;
  if (onAddPoint) view.onAddPoint=onAddPoint;
  render(view);
}
export function setFallbackMode(view, mode) {view.mode = mode; render(view);}
export function setFallbackAnnotations(view, lesions) {view.annotations=Array.isArray(lesions)?lesions:[];render(view);}
export function setFallbackMask(view, mask, visible=true){view.mask=mask;view.showMask=!!visible;render(view);}
export function focusFallback(view, ras) {
  const ijk=rasToIndex(view,ras);
  if (!ijk) return false;
  view.index=ijk.map((v,i)=>Math.min(view.shape[i]-1,Math.max(0,Math.round(v))));
  render(view);
  view.onPoint(ras);
  return true;
}
