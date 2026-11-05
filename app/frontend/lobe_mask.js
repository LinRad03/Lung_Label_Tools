import * as nifti from 'nifti-reader-js';

export const LOBES = {
  1:{name:'左上叶',color:[255,107,107]},
  2:{name:'左下叶',color:[255,188,84]},
  3:{name:'右上叶',color:[95,207,255]},
  4:{name:'右中叶',color:[177,133,255]},
  5:{name:'右下叶',color:[96,224,155]},
};
export const LOBE_BOUND_DISEASES = new Set(['pulmonary_nodule','atelectasis','consolidation','bronchiectasis','pulmonary_bulla']);

export async function loadLobeMask(url, imageMeta) {
  const response=await fetch(url,{credentials:'same-origin'});
  if(!response.ok)throw Error('MASK_LOAD_FAILED');
  let buffer=await response.arrayBuffer();
  if(nifti.isCompressed(buffer))buffer=await nifti.decompressAsync(buffer);
  if(!nifti.isNIFTI(buffer))throw Error('MASK_FORMAT_INVALID');
  const header=nifti.readHeader(buffer);
  const shape=header.dims.slice(1,4).map(Number);
  if(shape.some((n,i)=>n!==imageMeta.shape_ijk[i]))throw Error('MASK_SHAPE_MISMATCH');
  if(header.affine?.length===4 && header.affine.some((row,i)=>row.some((v,j)=>Math.abs(v-imageMeta.affine_ras[i][j])>1e-3)))
    throw Error('MASK_AFFINE_MISMATCH');
  if(![2,256,4,512].includes(header.datatypeCode))throw Error('MASK_LABEL_DATATYPE_UNSUPPORTED');
  return {shape,affine:imageMeta.affine_ras,header,data:new DataView(nifti.readImage(header,buffer))};
}

export function maskLabel(mask, ijk) {
  if(!mask||ijk.some((v,i)=>v<0||v>=mask.shape[i]))return 0;
  const v=ijk.map(Math.round),offset=v[0]+mask.shape[0]*(v[1]+mask.shape[1]*v[2]);
  const code=mask.header.datatypeCode, d=mask.data, le=mask.header.littleEndian;
  return code===2?d.getUint8(offset):code===256?d.getInt8(offset):code===4?d.getInt16(offset*2,le):d.getUint16(offset*2,le);
}

export function rasToMaskVoxel(mask, ras) {
  const a=mask.affine.map(row=>row.slice(0,3)),b=ras.map((v,i)=>v-mask.affine[i][3]);
  const determinant=a[0][0]*(a[1][1]*a[2][2]-a[1][2]*a[2][1])-a[0][1]*(a[1][0]*a[2][2]-a[1][2]*a[2][0])+a[0][2]*(a[1][0]*a[2][1]-a[1][1]*a[2][0]);
  if(Math.abs(determinant)<1e-10)throw Error('MASK_AFFINE_SINGULAR');
  const cross=(u,v)=>[u[1]*v[2]-u[2]*v[1],u[2]*v[0]-u[0]*v[2],u[0]*v[1]-u[1]*v[0]];
  const cols=[0,1,2].map(i=>a.map(row=>row[i]));
  return [cross(cols[1],cols[2]),cross(cols[2],cols[0]),cross(cols[0],cols[1])]
    .map(row=>row.reduce((sum,v,i)=>sum+v*b[i],0)/determinant);
}

export function inferLobes(mask, lesion) {
  if(!mask)return {status:'mask_missing',lobes:[],sampled:0};
  const a=lesion.ras_mm,b=lesion.kind==='box'?lesion.ras_hi_mm:null;
  if(!Array.isArray(a)||a.length!==3)return {status:'invalid_geometry',lobes:[],sampled:0};
  const counts=new Map(),steps=b?25:1;
  const differences=b?[0,1,2].map(i=>Math.abs(a[i]-b[i])):[];
  const variable=b?differences.map((d,i)=>d>0.5?i:-1).filter(i=>i>=0):[];
  let sampled=0;
  for(let i=0;i<steps;i++)for(let j=0;j<(variable.length>1?steps:1);j++){
    const ras=[...a];
    if(variable.length)ras[variable[0]]=a[variable[0]]+(b[variable[0]]-a[variable[0]])*(i+.5)/steps;
    if(variable.length>1)ras[variable[1]]=a[variable[1]]+(b[variable[1]]-a[variable[1]])*(j+.5)/steps;
    const label=maskLabel(mask,rasToMaskVoxel(mask,ras));
    counts.set(label,(counts.get(label)||0)+1);sampled++;
  }
  const lobes=[...counts].filter(([id])=>LOBES[id]).sort((x,y)=>y[1]-x[1]).map(([id,n])=>({id,name:LOBES[id].name,fraction:n/sampled}));
  return {status:lobes.length?'matched':'outside_lobes',lobes,sampled};
}

export function classifyLobeMark(mask,disease,lesion){
  if(!mask)return 'mask_unavailable';
  if(!LOBE_BOUND_DISEASES.has(disease))return 'not_applicable';
  return inferLobes(mask,lesion).status==='outside_lobes'?'reject':'accept';
}

export function partitionLobeMarks(mask,disease,lesions){
  const kept=[],removed=[];
  for(const lesion of lesions)(classifyLobeMark(mask,disease,lesion)==='reject'?removed:kept).push(lesion);
  return {kept,removed};
}
