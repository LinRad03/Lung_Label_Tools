import assert from 'node:assert/strict';
import {reportSegments} from './report_highlight.js';

const source='未见肺大疱。另见右肺结节；<script>alert(1)</script>。';
const parts=reportSegments(source,'pulmonary_bulla');
assert.equal(parts.map(x=>x.text).join(''),source);
assert.deepEqual(parts.filter(x=>x.match).map(x=>x.text),['肺大疱']);
assert.deepEqual(reportSegments(source,'pulmonary_nodule').filter(x=>x.match).map(x=>x.text),['结节']);
assert.equal(reportSegments('双肺散在结节，未见胸腔积液','pleural_effusion').filter(x=>x.match)[0].text,'胸腔积液');
assert.equal(reportSegments('COPD，肺气肿','copd_review').filter(x=>x.match)[0].text,'COPD');
assert.equal(reportSegments(source,'unknown').map(x=>x.text).join(''),source);
console.log('Disease-specific, verbatim-preserving report highlighting: PASS');
