import {heading, panel} from '../components/common/heading.js';
import {entries, records} from '../components/common/records.js';
import {closureLabels, closureStatus} from '../constants/workflow.js';
import {date, esc} from '../utils/dom.js';
import {topic} from '../constants/project.js';
import {submit} from '../hooks/submit.js';

export function casesPage(state, identity) {
  const {spec, session, cases, runs} = state;
  const selected = cases.find(row=>row.id===identity) || cases[0];
  const operator = session.role==='operator';
  const rows = cases.map(row=>[`<a href="#cases/${row.id}" ${row.id===selected?.id?'aria-current="true"':''}>${esc(row.title)}</a>`,
    esc(closureLabels[closureStatus(row)]), esc(row.author), esc(date(row.created_at))]);
  const instruction = selected ? `<h2>${esc(selected.title)}</h2><p>${esc(spec.guidance)}</p>` : '';
  const input = selected ? Object.entries(selected.payload).map(([key, value])=>{
    const label = spec.field_labels[key] || key;
    const rows = Array.isArray(value) && value.every(x=>x && typeof x === 'object' && !Array.isArray(x));
    return panel(label, rows ? entries(value.slice(0,10), spec.field_labels) + (value.length>10?`<p class="note">显示前 10 条，共 ${value.length} 条。</p>`:'') : `<pre>${esc(value)}</pre>`);
  }).join('') : '';
  const closurePanel = selected ? (selected.closed_at
    ? `<p class="note" data-testid="closed">结档状态：${esc(closureLabels.closed)}</p>
       <dl><dt>结档时间</dt><dd>${esc(date(selected.closed_at))}</dd><dt>经办</dt><dd>${esc(selected.closed_by || '—')}</dd></dl>
       <p class="note">源素材路径与演员编号已从库中移除，旧证据与签署仍可在匿名归档中查阅。</p>`
    : `<p class="note" data-testid="open">结档状态：${esc(closureLabels.open)}</p>
       ${operator ? '<form id="close-case-form"><button>结档</button><p class="note">需复核员已签署至少一份证据；结档后不再受理新的执行请求。</p></form>' : '<p class="note">复核员签署后，经办员可以结档。</p>'}`) : '';
  return `${heading(topic.modules[0], spec.summary, operator?'<a class="button" href="#editor">建立档案</a>':'')}
    <div class="spread"><section aria-label="档案目录">${records([spec.entity_labels[0], '结档状态', '经办', '建立时间'], rows, '尚未建立档案。经办员可以从演示资料建立第一份。')}</section>
    <aside class="brief">${instruction || `<h2>${esc(spec.customer)}</h2>`}${selected?`<p class="note">该档案记录 ${runs.filter(row=>row.case_id===selected.id).length} 次执行。</p>`:''}${selected&&operator&&!selected.closed_at?`<a href="#editor/${selected.id}">复制为新档案</a>`:''}${closurePanel}</aside></div>
    ${input ? `<div class="inspection">${input}</div>`:''}`;
}

export function bindCases(api, update, fail) {
  submit('close-case-form', async()=>{
    const {id} = selectedCase();
    await api(`/cases/${id}/close`, {method:'POST', data:{}});
    await update();
  }, fail);
}

function selectedCase() {
  const [, raw] = (location.hash.slice(1) || 'cases').split('/');
  return {id: Number(raw) || 0};
}
