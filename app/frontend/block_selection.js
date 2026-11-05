export const BLOCK_GRID_SIZE = 4;
export const blockKey = ijk => ijk.join(',');
export const fromKey = key => key.split(',').map(Number);
export function automaticBlocks(view){
  const m=view?.mapping;
  return Array.isArray(m)?m.map(x=>x.block_ijk):m?.inside?[m.block_ijk]:[];
}
export function neighbors(ijk){
  const out=[];
  for(let axis=0;axis<3;axis++)for(const delta of [-1,1]){
    const next=[...ijk];next[axis]+=delta;
    if(next[axis]>=0&&next[axis]<BLOCK_GRID_SIZE)out.push(next);
  }
  return out;
}
export function expandBlocks(blocks){
  const keys=new Set(blocks.map(blockKey));
  for(const block of blocks)for(const n of neighbors(block))keys.add(blockKey(n));
  return [...keys].map(fromKey).sort(compareBlocks);
}
export function shrinkBlocks(blocks){
  const keys=new Set(blocks.map(blockKey));
  return blocks.filter(block=>neighbors(block).every(n=>keys.has(blockKey(n)))).sort(compareBlocks);
}
export function compareBlocks(a,b){return a[2]-b[2]||a[1]-b[1]||a[0]-b[0];}
