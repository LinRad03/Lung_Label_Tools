import assert from 'node:assert/strict';
import {inferLobes,maskLabel,rasToMaskVoxel,classifyLobeMark,partitionLobeMarks} from './lobe_mask.js';

const shape=[10,10,10],voxels=new Uint8Array(1000);
const at=(x,y,z)=>x+10*(y+10*z);
for(let x=1;x<=4;x++)voxels[at(x,1,2)]=1;
for(let x=5;x<=8;x++)voxels[at(x,1,2)]=3;
voxels[at(3,3,3)]=6; // Historical anatomy class, not a lung lobe.
const mask={shape,affine:[[2,0,0,10],[0,3,0,20],[0,0,4,30],[0,0,0,1]],
  header:{datatypeCode:2,littleEndian:true},data:new DataView(voxels.buffer)};
assert.deepEqual(rasToMaskVoxel(mask,[16,23,38]),[3,1,2]);
assert.equal(maskLabel(mask,[3,1,2]),1);
assert.equal(inferLobes(mask,{kind:'point',ras_mm:[16,23,38]}).lobes[0].name,'左上叶');
assert.equal(inferLobes(mask,{kind:'point',ras_mm:[16,29,42]}).status,'outside_lobes');
const inside={kind:'point',ras_mm:[16,23,38]},outside={kind:'point',ras_mm:[16,29,42]};
assert.equal(classifyLobeMark(mask,'pulmonary_nodule',outside),'reject');
assert.equal(classifyLobeMark(mask,'pleural_effusion',outside),'not_applicable');
assert.equal(classifyLobeMark(null,'pulmonary_nodule',outside),'mask_unavailable');
assert.deepEqual(partitionLobeMarks(mask,'pulmonary_nodule',[inside,outside]),{kept:[inside],removed:[outside]});
assert.deepEqual(partitionLobeMarks(mask,'lymphadenopathy',[outside]),{kept:[outside],removed:[]});
const result=inferLobes(mask,{kind:'box',ras_mm:[12,23,38],ras_hi_mm:[26,23,38]});
assert.deepEqual(new Set(result.lobes.map(x=>x.name)),new Set(['左上叶','右上叶']));
assert.equal(result.sampled,25);
console.log('Lobe point, planar box and historical-class exclusion: PASS');
