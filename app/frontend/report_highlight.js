// Presentation only: this never changes the source report or annotation state.
export const REPORT_TERMS = Object.freeze({
  pulmonary_nodule: ['磨玻璃结节','实性结节','小结节','结节','nodules','nodule'],
  atelectasis: ['肺不张','atelectasis'],
  consolidation: ['实变','consolidation'],
  bronchiectasis: ['支气管扩张','bronchiectasis'],
  pleural_effusion: ['胸腔积液','胸水','pleural effusion'],
  lymphadenopathy: ['淋巴结肿大','肿大淋巴结','lymphadenopathy'],
  pericardial_effusion: ['心包积液','pericardial effusion'],
  pulmonary_bulla: ['肺大泡','肺大疱','bullae','bulla'],
  copd_review: ['慢性阻塞性肺疾病','慢性阻塞性肺病','慢阻肺','COPD'],
});

export function reportSegments(text,disease){
  const source=String(text ?? '');
  const terms=REPORT_TERMS[disease] || [];
  if(!terms.length)return [{text:source,match:false}];
  const escaped=terms.map(x=>x.replace(/[.*+?^${}()|[\]\\]/g,'\\$&'));
  const re=new RegExp(escaped.join('|'),'gi');
  const parts=[];
  let start=0,hit;
  while((hit=re.exec(source))!==null){
    if(hit.index>start)parts.push({text:source.slice(start,hit.index),match:false});
    parts.push({text:hit[0],match:true});
    start=re.lastIndex;
  }
  if(start<source.length||!parts.length)parts.push({text:source.slice(start),match:false});
  return parts;
}

export function highlightReport(element,text,disease){
  const source=String(text ?? '');
  element.replaceChildren();
  for(const part of reportSegments(source,disease)){
    if(part.match){const mark=document.createElement('mark');mark.className='report-keyword';mark.textContent=part.text;element.append(mark);}
    else element.append(document.createTextNode(part.text));
  }
  return source;
}
