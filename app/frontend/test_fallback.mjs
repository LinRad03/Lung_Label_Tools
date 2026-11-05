import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import * as nifti from 'nifti-reader-js';
import {createFallback, setFallbackMode, setFallbackWindow, setFallbackAnnotations, setFallbackZoom, focusFallback} from './fallback.js';

const bytes = readFileSync(process.argv[2]);
globalThis.fetch = async () => ({ok: true, arrayBuffer: async () => bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength)});
let outlines=0;
const mockContext = {
  setTransform(){}, fillRect(){}, drawImage(){}, fillText(){}, beginPath(){}, moveTo(){}, lineTo(){}, stroke(){},rect(){},clip(){},
  save(){}, restore(){}, setLineDash(){}, strokeRect(){outlines++;},
  createImageData(w, h){return {data: new Uint8ClampedArray(w * h * 4)};},
  putImageData(){},
};
globalThis.document = {createElement: () => ({width: 0, height: 0, getContext: () => mockContext})};
globalThis.window = {devicePixelRatio: 1, addEventListener(){}, removeEventListener(){}};
const handlers = {};
const canvas = {clientWidth: 900, clientHeight: 450, width: 0, height: 0,
  getContext: () => mockContext, setPointerCapture(){}, addEventListener: (name, fn) => {handlers[name] = fn;}, removeEventListener: name => {delete handlers[name];}};
let point, hover, box;
let niftiBytes=bytes.buffer.slice(bytes.byteOffset,bytes.byteOffset+bytes.byteLength);
if(nifti.isCompressed(niftiBytes))niftiBytes=await nifti.decompressAsync(niftiBytes);
const shape=nifti.readHeader(niftiBytes).dims.slice(1,4).map(Number);
const meta = {shape_ijk: shape, affine_ras: [[1, 0, 0, 10], [0, 2, 0, 20], [0, 0, 3, 30], [0, 0, 0, 1]]};
const view = await createFallback(canvas, '/synthetic.nii.gz', meta, value => {point = value;}, value => {hover = value;}, (lo,hi)=>{box=[lo,hi];});
assert.deepEqual(view.shape, shape);
assert.deepEqual(view.axes, [0, 1, 2]);
assert.equal(view.mode, 'axial');
setFallbackMode(view, 'axial');
setFallbackWindow(view, [-1000, 1000]);
assert.deepEqual(view.window, [-1000, 1000]);
handlers.pointerup({offsetX: 450, offsetY: 225});
assert.equal(point.length, 3);
assert(point.every(Number.isFinite));
handlers.pointermove({offsetX: 400, offsetY: 220});
assert(hover.every(Number.isFinite));
handlers.pointerdown({button:0,pointerId:1,offsetX:420,offsetY:220});
handlers.pointermove({offsetX:460,offsetY:260});
handlers.pointerup({offsetX:460,offsetY:260});
assert.equal(box.length,2);
assert(box.flat().every(Number.isFinite));
setFallbackAnnotations(view,[{kind:'point',ras_mm:point,id:'test-point'},
  {kind:'box',ras_mm:box[0],ras_hi_mm:box[1],id:'test-box'}]);
assert(outlines>=2);
const before=outlines;
setFallbackMode(view,'multi');
assert(outlines>before);
assert(focusFallback(view,point));
setFallbackMode(view,'axial');
const oldZ = view.index[2];
handlers.wheel({offsetX:450,offsetY:225,deltaY:-80,ctrlKey:true,preventDefault(){}});
assert(view.zoom>1 && view.zoom<=8);
setFallbackZoom(view,2);
assert.equal(view.zoom,2);
setFallbackZoom(view,20);
assert.equal(view.zoom,8);
handlers.wheel({offsetX: 450, offsetY: 225, deltaY: 1, preventDefault(){}});
assert.equal(view.index[2], oldZ + 1);
view.dispose();
assert.equal(Object.keys(handlers).length, 0);
console.log('CPU viewer synthetic load, three planes, windowing, hover, point, drag box and wheel: PASS');
