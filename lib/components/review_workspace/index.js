export default function(component) {
  const host = component.parentElement;
  const root = host.querySelector('#review-workspace');
  if (!root) return;
  let state = host.__reviewState;
  if (!state) {
    state = {data:null, draft:null, ai:null, reason:'', pending:null, afterSave:null,
      guardAction:null, lastAck:null, scroll:0, mobileQueue:false, signature:null};
    host.__reviewState = state;
  }
  state.sendTrigger = component.setTriggerValue;
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const record = () => state.data?.record;
  const changed = () => !!state.draft && state.draft.text !== state.draft.base;
  const dirty = () => changed() || !!state.reason.trim() || !!state.ai?.instruction?.trim();
  const fieldData = (index, field) => record()?.messages?.find(m => m.index === index)?.[field];
  function notice(text, kind='info') {
    const el = root.querySelector('.statusbar');
    if (el) { el.textContent = text; el.className = 'statusbar ' + kind; }
  }
  function emit(action, payload={}) {
    if (state.pending) return;
    const id = crypto.randomUUID();
    state.pending = {id, action};
    root.classList.add('busy');
    root.querySelectorAll('button,input,textarea,select').forEach(b => b.disabled = true);
    notice(action === 'ai' ? '正在生成当前字段的建议；原文和草稿均未修改。' : '正在处理，请稍候…');
    state.sendTrigger('event', {id, action, record_id:record()?.record_id,
      sample_hash:record()?.sample_hash, ...payload});
  }
  function resetLocal() { state.draft=null; state.ai=null; state.reason=''; }
  function guarded(fn) {
    if (state.pending) { notice('请等待当前请求完成。'); return; }
    if (!dirty()) { state.draft=null; state.ai=null; fn(); return; }
    state.guardAction=fn;
    const guard=root.querySelector('.guard');
    guard.hidden=false;
    guard.querySelector('[data-action="guard-save"]').hidden=!changed();
    guard.querySelector('[data-action="guard-stay"]').focus();
  }
  function save() {
    if (!changed()) { notice('当前字段没有改动。'); return; }
    emit('save', {index:state.draft.index, field:state.draft.field, text:state.draft.text,
      record_id:state.draft.record_id, sample_hash:state.draft.sample_hash});
  }
  function openEdit(index, field) {
    const f=fieldData(index,field);
    if (!f?.editable) return;
    const run=()=>{state.ai=null;state.draft={index,field,text:f.text ?? '',base:f.text ?? '',record_id:record().record_id,sample_hash:record().sample_hash};drawReader();
      const input=root.querySelector('textarea.source');if(input){input.focus({preventScroll:true});input.setSelectionRange(input.value.length,input.value.length);input.scrollIntoView({block:'nearest'});}};
    if(changed())guarded(run);else run();
  }
  function openAI(index,field) {
    if(!fieldData(index,field)?.editable)return;
    const run=()=>{state.draft=null;state.ai={index,field,instruction:'',record_id:record().record_id,sample_hash:record().sample_hash};drawReader();root.querySelector('[data-input="ai-instruction"]')?.focus({preventScroll:true});};
    if(changed())guarded(run);else run();
  }
  function drawField(message, field, label) {
    const f=message[field];if(!f)return '';
    const i=message.index;
    const edit=state.draft?.index===i&&state.draft.field===field;
    const ai=state.ai?.index===i&&state.ai.field===field;
    const buttons=f.editable?`<div class="actions"><button data-action="edit" data-index="${i}" data-field="${field}" aria-label="编辑第 ${i+1} 条${label}">编辑</button><button data-action="ai-open" data-index="${i}" data-field="${field}" aria-label="AI 修改第 ${i+1} 条${label}">AI 修改</button></div>`:'<span class="small">结构化内容 · 只读</span>';
    let body=`<div class="field-read">${f.html || '<span class="muted">（空内容）</span>'}</div>`;
    if(edit)body=`<div class="editor"><div class="small">仅编辑第 ${i+1} 条${label} · Markdown 源码；其他消息保持渲染</div><textarea class="source" aria-label="第 ${i+1} 条${label}源码" data-input="source" spellcheck="false">${esc(state.draft.text)}</textarea><div class="actions"><button class="primary" data-server data-action="save">保存为新版本</button><button data-action="cancel-edit">取消编辑</button><span class="small" data-draft-status>${changed()?'有未保存的改动':'未改动'} · Ctrl+S 保存</span></div></div>`;
    if(ai){
      const suggestion=state.data.suggestion;
      const show=suggestion&&suggestion.index===i&&suggestion.field===field;
      body+=`<div class="ai-panel"><label>只修改这条${label}；先看建议，再决定是否采用</label><textarea aria-label="第 ${i+1} 条${label}修改要求" data-input="ai-instruction" placeholder="说明需要修正的内容，不会自动改动其他消息">${esc(state.ai.instruction)}</textarea><div class="actions"><button data-server data-action="ai-run" class="primary">生成建议</button><button data-action="ai-close">关闭建议</button></div>${show?`<div class="comparison"><strong>修改建议 · 尚未保存</strong><div class="field-read">${suggestion.html}</div><div class="actions"><button class="primary" data-server data-action="ai-adopt">采用并保存新版本</button><button data-action="ai-adjust">先手动调整</button><button data-action="ai-drop">丢弃建议</button></div></div>`:''}<p class="small">受预算与数据授权闸门约束，可能产生 API 费用。</p></div>`;
    }
    return `<section class="field" data-field-block="${field}"><div class="field-head"><span class="small ${field==='reasoning_content'?'think-label':''}">${label}</span>${buttons}</div>${body}</section>`;
  }
  function drawReader() {
    const pane=root.querySelector('.conversation');if(!pane)return;
    const top=pane.scrollTop;
    const r=record();
    pane.innerHTML=r?(r.messages||[]).map(m=>`<article class="message ${esc(m.role)}" id="message-${m.index}"><div class="message-head"><span class="role">${esc(m.role.toUpperCase())}</span><span>第 ${m.index+1} 条</span></div>${drawField(m,'reasoning_content','思考')}${drawField(m,'content','正文')}${m.extra_html?`<div class="extra">${m.extra_html}</div>`:''}</article>`).join(''):'<div class="empty"><h3>当前没有待审样本</h3><p>可以从已打开文件夹导入样本，或调整左侧筛选条件。</p></div>';
    pane.scrollTop=top;
    if(state.pending)root.querySelectorAll('button,input,textarea,select').forEach(b=>b.disabled=true);
  }
  function draw() {
    const oldPane=root.querySelector('.conversation');if(oldPane)state.scroll=oldPane.scrollTop;
    const d=state.data||{},q=d.queue||{},r=d.record,items=q.items||[];
    root.innerHTML=`<header class="topbar"><div class="top-meta"><strong>审核工作台</strong><div class="small">${esc(d.workspace)} · ${esc(d.identity)}${d.identity==='admin'?'（本机管理员）':''}</div></div><div class="actions"><button class="mobile-queue" data-action="queue-toggle">队列</button><button data-action="navigate-import">导入样本</button><button data-action="navigate-settings">身份与接入</button></div></header>
      <div class="statusbar" role="status" aria-live="polite">Markdown 阅读 · 修改只作用于选中字段 · 原始数据保持不变</div>
      <div class="layout"><aside class="queue ${state.mobileQueue?'open':''}" aria-label="审核队列"><div class="queue-controls"><strong>审核队列</strong><div class="small">待审 ${q.pending??0} · 我已审 ${q.reviewed??0}</div><input aria-label="搜索样本" placeholder="搜索 ID 或问题，回车确认" value="${esc(d.query)}" data-input="search"><select aria-label="队列状态" data-input="status"><option value="pending" ${d.status==='pending'?'selected':''}>待我审核</option><option value="reviewed" ${d.status==='reviewed'?'selected':''}>我已审核</option><option value="all" ${d.status==='all'?'selected':''}>全部记录</option></select></div><div class="queue-list">${items.map(item=>`<button class="queue-item ${r?.record_id===item.record_id?'active':''}" data-action="select" data-sample="${esc(item.sample_id)}" aria-label="打开样本 ${esc(item.sample_id)}"><span class="queue-id">${esc(item.sample_id)}</span><span class="queue-preview">${esc(item.instruction||'（无问题摘要）')}</span>${item.decision?`<span class="small">已${item.decision==='keep'?'保留':'驳回'}</span>`:''}</button>`).join('')||'<div class="empty">没有匹配记录</div>'}</div><div class="queue-footer"><div class="small">${q.total?`${(q.offset||0)+1}–${Math.min((q.offset||0)+(q.limit||30),q.total)}`:'0'} / ${q.total||0}</div><div class="actions"><button data-action="page-prev" ${(q.offset||0)<=0?'disabled':''}>上一页</button><button data-action="page-next" ${(q.offset||0)+(q.limit||30)>=(q.total||0)?'disabled':''}>下一页</button></div></div></aside>
      <section class="reader" aria-label="会话阅读区"><div class="reader-head"><div class="reader-title"><strong>${esc(r?.sample_id||'未选中样本')}</strong><span class="small">${r?`${r.messages?.length||0} 条消息 · ${r.decision?'已审核，内容只读判定':'待审核新内容'}`:'选择左侧记录开始'}</span></div>${r?`<select aria-label="跳转到消息" data-input="jump"><option value="">定位消息…</option>${(r.messages||[]).map(m=>`<option value="${m.index}">第 ${m.index+1} 条 · ${esc(m.role)}</option>`).join('')}</select>`:''}</div><div class="conversation" tabindex="0" aria-label="对话内容"></div></section>
      <aside class="decision" aria-label="审核判定"><h3>当前版本判定</h3><div class="summary">${r?.decision?`已${r.decision==='keep'?'保留':'驳回'}：${esc(r.reason)}`:'先阅读，再明确选择保留或驳回。编辑保存不会自动批准新版。'}</div><label for="review-reason-preset">常用理由（主动选择）</label><select id="review-reason-preset" data-input="preset" ${!r||r.decision?'disabled':''}><option value="">不使用模板</option><option>内容准确，任务完成，表达清晰</option><option>事实错误或缺少依据</option><option>失败操作未纠正</option><option>重复内容或低信息量</option><option>隐私或安全问题需处理</option><option>图像与文本不一致</option></select><label for="review-reason">判定理由</label><textarea id="review-reason" data-input="reason" aria-label="判定理由" placeholder="填写判断依据，或选择上方常用理由" ${!r||r.decision?'disabled':''}>${esc(state.reason)}</textarea><div class="decision-buttons"><button class="primary" data-server data-action="keep" ${!r||r.decision?'disabled':''}>保留并下一条</button><button class="reject" data-server data-action="reject" ${!r||r.decision?'disabled':''}>驳回并下一条</button><button data-server data-action="skip" ${!r?'disabled':''}>跳过 / 下一条</button></div><p class="small help">跳过不提交审核票。已提交判定保留审计，不直接覆盖。</p><p class="small help">Alt+Enter 保留<br>Alt+Backspace 驳回<br>Alt+→ 下一条<br>输入文字时快捷判定停用</p></aside></div>
      <div class="guard" role="dialog" aria-modal="true" aria-label="未保存内容" hidden><div class="guard-card"><h3>先处理当前未保存内容</h3><p>切换会放弃当前草稿、修改要求或判定理由。可以继续编辑，也可以保存当前文本修订后切换。</p><div class="actions"><button class="primary" data-action="guard-stay">继续编辑</button><button data-action="guard-save">保存修订后继续</button><button data-action="guard-discard">放弃未保存内容</button></div></div></div>`;
    drawReader();root.querySelector('.conversation').scrollTop=state.scroll;
    if(d.notice?.text)notice(d.notice.text,d.notice.kind);
    root.classList.toggle('busy',!!state.pending);
    if(state.pending)root.querySelectorAll('button,input,textarea,select').forEach(b=>b.disabled=true);
  }
  const incoming=component.data||{};
  const previous=state.data;
  const ack=incoming.ack;
  let after=null;
  if(ack?.id&&ack.id!==state.lastAck){
    state.lastAck=ack.id;
    if(state.pending?.id===ack.id){
      const action=state.pending.action;state.pending=null;
      if(ack.ok){
        if(action==='save'){state.draft=null;state.ai=null;after=state.afterSave;state.afterSave=null;}
        if(['select','skip','submit','query','navigate'].includes(action)){resetLocal();state.scroll=0;root.querySelector('.conversation')?.scrollTo(0,0);}
      }else state.afterSave=null;
    }
  }
  if(previous?.scope!==incoming.scope){resetLocal();state.pending=null;state.scroll=0;state.signature=null;root.querySelector('.conversation')?.scrollTo(0,0);}
  state.data=incoming;
  const signature=JSON.stringify(incoming);
  if(signature!==state.signature||!root.querySelector('.layout')){state.signature=signature;draw();}
  if(after)queueMicrotask(after);
  function handleClick(e) {
    const button=e.target.closest('button[data-action]');if(!button||button.disabled)return;
    const a=button.dataset.action;
    const idx=Number(button.dataset.index),field=button.dataset.field;
    if(a==='edit')return openEdit(idx,field);
    if(a==='save')return save();
    if(a==='cancel-edit')return guarded(()=>{state.draft=null;drawReader();});
    if(a==='ai-open')return openAI(idx,field);
    if(a==='ai-close'||a==='ai-drop'){state.ai=null;state.data.suggestion=null;drawReader();return;}
    if(a==='ai-run'){
      if(!state.ai?.instruction.trim()){notice('请填写本字段的修改要求。','error');return;}
      emit('ai',{index:state.ai.index,field:state.ai.field,instruction:state.ai.instruction,record_id:state.ai.record_id,sample_hash:state.ai.sample_hash});return;
    }
    if(a==='ai-adopt'||a==='ai-adjust'){
      const s=state.data.suggestion;if(!s)return;
      state.draft={index:s.index,field:s.field,text:s.text,base:fieldData(s.index,s.field)?.text||'',record_id:state.ai?.record_id,sample_hash:state.ai?.sample_hash};state.ai=null;
      if(a==='ai-adopt')save();else drawReader();return;
    }
    if(a==='select')return guarded(()=>{state.mobileQueue=false;emit('select',{sample_id:button.dataset.sample});});
    if(a==='skip')return guarded(()=>emit('skip'));
    if(a==='page-prev'||a==='page-next')return guarded(()=>emit('query',{query:state.data.query||'',status:state.data.status||'pending',offset:Math.max(0,(state.data.queue.offset||0)+(a==='page-next'?1:-1)*(state.data.queue.limit||30))}));
    if(a==='navigate-import'||a==='navigate-settings')return guarded(()=>emit('navigate',{target:a==='navigate-import'?'import':'settings'}));
    if(a==='keep'||a==='reject'){
      if(state.pending)return;
      if(changed()||state.ai){notice('请先保存或取消当前修改，再审核明确的内容版本。','error');return;}
      if(!state.reason.trim()){notice('请填写判定理由，或主动选择一项常用理由。','error');root.querySelector('[data-input="reason"]').focus();return;}
      emit('submit',{decision:a==='keep'?'keep':'reject',reason:state.reason});return;
    }
    if(a==='queue-toggle'){state.mobileQueue=!state.mobileQueue;root.querySelector('.queue').classList.toggle('open',state.mobileQueue);return;}
    if(a==='guard-stay'){state.guardAction=null;root.querySelector('.guard').hidden=true;(root.querySelector('textarea.source')||root.querySelector('[data-input="reason"]'))?.focus({preventScroll:true});return;}
    if(a==='guard-discard'){const fn=state.guardAction;state.guardAction=null;resetLocal();root.querySelector('.guard').hidden=true;drawReader();fn?.();return;}
    if(a==='guard-save'){state.afterSave=state.guardAction;state.guardAction=null;root.querySelector('.guard').hidden=true;save();}
  }
  function handleInput(e) {
    const type=e.target.dataset.input;
    if(type==='source'&&state.draft){state.draft.text=e.target.value;const label=root.querySelector('[data-draft-status]');if(label)label.textContent=(changed()?'有未保存的改动':'未改动')+' · Ctrl+S 保存';}
    if(type==='reason')state.reason=e.target.value;
    if(type==='ai-instruction'&&state.ai)state.ai.instruction=e.target.value;
  }
  function handleChange(e) {
    const type=e.target.dataset.input;
    if(type==='preset'&&e.target.value){state.reason=e.target.value;root.querySelector('[data-input="reason"]').value=state.reason;}
    if(type==='status'){const status=e.target.value;e.target.value=state.data.status;guarded(()=>emit('query',{status,query:state.data.query||'',offset:0}));}
    if(type==='jump'&&e.target.value!=='')root.querySelector('#message-'+e.target.value)?.scrollIntoView({block:'start'});
  }
  function handleKey(e) {
    if(e.target.dataset.input==='search'&&e.key==='Enter'){e.preventDefault();const query=e.target.value;guarded(()=>emit('query',{query,status:state.data.status||'pending',offset:0}));return;}
    if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='s'&&state.draft){e.preventDefault();save();return;}
    if(e.key==='Escape'&&!root.querySelector('.guard').hidden){e.preventDefault();state.guardAction=null;root.querySelector('.guard').hidden=true;return;}
    if(e.target.matches('input,textarea,select,[contenteditable="true"]'))return;
    if(e.altKey){const action=e.key==='Enter'?'keep':e.key==='Backspace'?'reject':e.key==='ArrowRight'?'skip':null;if(action){e.preventDefault();root.querySelector(`[data-action="${action}"]`)?.click();}}
  }
  // The outer app owns workspace/page navigation; intercept only user navigation while a draft exists.
  function outsideClick(e) {
    if(!e.isTrusted||!dirty()||e.composedPath().includes(root))return;
    const target=e.target.closest('button,a,label,[role="combobox"],[role="radio"]');
    if(!target)return;
    e.preventDefault();e.stopImmediatePropagation();
    guarded(()=>{resetLocal();target.click();});
  }
  function outsideKey(e) {
    if(!e.isTrusted||!dirty()||e.composedPath().includes(root))return;
    if(['Enter',' ','ArrowUp','ArrowDown','ArrowLeft','ArrowRight'].includes(e.key)){
      e.preventDefault();e.stopImmediatePropagation();
      guarded(()=>notice('未保存内容已处理，请重新选择目标页面。'));
    }
  }
  function unload(e) {if(dirty()){e.preventDefault();e.returnValue='';}}
  root.onclick=handleClick;root.oninput=handleInput;root.onchange=handleChange;root.onkeydown=handleKey;
  window.addEventListener('click',outsideClick,true);
  window.addEventListener('keydown',outsideKey,true);
  window.addEventListener('beforeunload',unload);
  return ()=>{window.removeEventListener('click',outsideClick,true);window.removeEventListener('keydown',outsideKey,true);window.removeEventListener('beforeunload',unload);};
}
