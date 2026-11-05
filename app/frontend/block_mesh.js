// Build a translucent 3D preview from versioned JEPA block geometry.
// Coordinates are RAS millimetres. The mesh is a viewing aid, not a lesion mask.
export function buildBlockMesh(view, blocks){
  if(!view?.input_affine_ras||!view?.block_shape_ijk)return {points:[],triangles:[]};
  const affine=view.input_affine_ras,shape=view.block_shape_ijk;
  const points=[],triangles=[];
  const faces=[[0,2,3,1],[4,5,7,6],[0,1,5,4],[2,6,7,3],[0,4,6,2],[1,3,7,5]];
  for(const block of blocks){
    const base=points.length/3;
    for(let corner=0;corner<8;corner++){
      const ijk=block.map((v,axis)=>(v+((corner>>axis)&1))*shape[axis]-.5);
      for(let axis=0;axis<3;axis++)points.push(affine[axis][3]+ijk.reduce((sum,n,j)=>sum+affine[axis][j]*n,0));
    }
    for(const face of faces){triangles.push(base+face[0],base+face[1],base+face[2],base+face[0],base+face[2],base+face[3]);}
  }
  return {points,triangles};
}
