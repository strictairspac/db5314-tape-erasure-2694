import {heading, panel} from '../components/common/heading.js';
import {records} from '../components/common/records.js';
import {badge} from '../components/common/status.js';
import {closureLabels, closureStatus, statusLabels} from '../constants/workflow.js';
import {topic} from '../constants/project.js';
import {date, esc} from '../utils/dom.js';

export function runsPage(state, identity) {
  const selected = state.runs.find(row=>row.id===identity) || state.runs[0];
  const cases = new Map(state.cases.map(row=>[row.id, row]));
  const rows = state.runs.map(row=>{
    const caseRow = cases.get(row.case_id);
    const closure = caseRow ? closureLabels[closureStatus(caseRow)] : '';
    return [`<a href="#runs/${row.id}">${esc(caseRow?.title || row.case_id)}</a>`, badge(row.status),
      esc(closure), esc(row.owner), esc(date(row.created_at))];
  });
  const result = state.evidence.find(row=>row.run_id===selected?.id);
  const active = selected && ['queued','running'].includes(selected.status);
  const selectedCase = selected && cases.get(selected.case_id);
  const detail = selected ? `<h2>${esc(selectedCase?.title || selected.case_id)}</h2>
    <p data-testid="run-status">${badge(selected.status)}</p>
    ${selectedCase?`<p class="note">档案结档状态：${esc(closureLabels[closureStatus(selectedCase)])}${selectedCase.closed_at?`（${esc(date(selectedCase.closed_at))}）`:''}</p>`:''}
    <dl><dt>启动时间</dt><dd>${esc(date(selected.started_at))}</dd><dt>完成时间</dt><dd>${esc(date(selected.finished_at))}</dd></dl>
    ${selected.error ? `<p class="error">${esc(selected.error)}</p>` : ''}
    ${active?'<p class="note">后台处理中，记录会自动刷新。</p>':''}
    ${selectedCase?.closed_at?'<p class="note">档案已结档，不再受理新的执行请求；结档前已受理的任务仍按原资料完成。</p>':''}
    ${result?`<a class="button" href="#evidence/${result.id}">查看业务证据</a>` : ''}` : '<h2>执行记录</h2><p>从档案创建一条执行记录后，可在此查看进度。</p>';
  const audit = state.audit.filter(x=>selected && x.reference===String(selected.id) && ['enqueue', 'complete', 'failed'].includes(x.action));
  return `${heading(topic.modules[2], `${state.spec.entity_labels[1]}与后台处理进度`, '<button class="secondary" type="button" id="refresh">刷新记录</button>')}
    <div class="spread"><section>${records(['档案','状态','结档状态','经办','提交时间'], rows, '暂无执行记录。')}</section><aside class="brief">${detail}</aside></div>
    ${audit.length?panel('操作记录', records(['操作者','操作','时间'], audit.map(x=>[esc(x.actor),esc(statusLabels[x.action]||'提交执行'),esc(date(x.created_at))]))):''}`;
}
