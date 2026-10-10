import fs from 'node:fs';
import assert from 'node:assert/strict';
import {JSDOM} from 'jsdom';

const html=fs.readFileSync('lib/presentation/streamlit/workflow_canvas_frontend/index.html','utf8');
const specs=JSON.parse(fs.readFileSync(0,'utf8'));
const dom=new JSDOM('<!doctype html><div class="df-topbar"></div><main data-testid="stMain"><iframe></iframe><section class="st-key-human-results-editor"></section></main>',{runScripts:'dangerously',pretendToBeVisual:true});
const outer=dom.window,doc=outer.document,frame=doc.querySelector('iframe'),w=frame.contentWindow;
const sequence=[];let reduced=false;
outer.postMessage=event=>sequence.push({type:'event',event});
w.ResizeObserver=class{observe(){}};
w.matchMedia=()=>({get matches(){return reduced}});
w.document.open();w.document.write(html.replace(/<script>[\s\S]*<\/script>/,''));w.document.close();
w.eval(html.match(/<script>([\s\S]*)<\/script>/)[1]);
const d=w.document,view=d.getElementById('view'),canvas=d.getElementById('canvas');
Object.defineProperty(view,'clientWidth',{value:1200});
Object.defineProperty(view,'clientHeight',{get(){return parseFloat(view.style.height)||0}});
Object.defineProperty(canvas,'offsetLeft',{get(){return parseFloat(canvas.style.left)||0}});
Object.defineProperty(canvas,'offsetTop',{get(){return parseFloat(canvas.style.top)||0}});
Object.defineProperty(d.querySelector('.shell'),'offsetHeight',{get(){return view.clientHeight+80}});
const main=doc.querySelector('main'),target=doc.querySelector('.st-key-human-results-editor');
main.getBoundingClientRect=()=>({top:0});main.scrollTop=0;
main.scrollTo=options=>sequence.push({type:'scroll',options});
target.getClientRects=()=>[{width:1100,height:300}];
target.getBoundingClientRect=()=>({top:1100});
doc.querySelector('.df-topbar').getBoundingClientRect=()=>({bottom:56});
function render(spec){w.dispatchEvent(new w.MessageEvent('message',{source:outer,data:{type:'streamlit:render',args:{spec}}}));}
function selectionEvents(){return sequence.filter(item=>item.type==='event'&&item.event.type==='streamlit:setComponentValue');}

for(const spec of specs){
 render({...spec,feedback_target:'human-results-editor'});
 const correction=d.querySelector('.feedback-node');
 assert.ok(correction,'one visible correction control is available');
 assert.equal(d.querySelectorAll('.feedback-node').length,1);
 assert.equal(d.querySelectorAll('.node').length,spec.nodes.length,'controls never masquerade as execution stages');
 assert.equal(d.getElementById('node-picker').options.length,spec.nodes.length);
 assert.equal(correction.dataset.selectNode,'review');
 assert.equal(correction.dataset.status,spec.control_nodes[0].status);
 assert.equal(correction.disabled,!spec.control_nodes[0].actionable);
 assert.equal(d.querySelectorAll('.feedback-edge[data-kind="return"]').length,1,'the repaired result returns to scoring in the same stage');
 assert.equal(d.querySelector('.feedback-edge[data-kind="return"]').dataset.to,'review');
 assert.ok(d.querySelector('.review-stage-group'),'one enclosing group keeps scoring and repair together');
 assert.equal(d.querySelectorAll('.feedback-edge[data-to="sft"]').length,0,'rejected answers never return to SFT');
 assert.equal(d.querySelector('.feedback-edge[data-kind="correction"]').dataset.from,spec.feedback_branch.review_node);
 assert.equal(d.querySelector('.feedback-edge[data-kind="pass"]').dataset.to,'package');
 for(const edge of spec.control_edges){
  assert.equal(d.querySelector(`.feedback-edge[data-route="${edge.id}"]`).dataset.status,edge.status);
 }
 const returning=d.querySelector('.feedback-edge[data-kind="return"]');
 if(!spec.feedback_branch.revision)assert.equal(returning.dataset.status,'pending','first-version work never implies human revision');
 if(spec.feedback_branch.phase==='reviewing')assert.equal(d.querySelector('.feedback-edge[data-kind="pass"]').dataset.status,'pending','the pass route waits for a real check result');
 assert.ok(d.getElementById('lineage').textContent.includes(spec.language==='en'?'one stage':'同一节点'));
 assert.equal(d.getElementById('legend').hidden,false);
 assert.equal(d.getElementById('legend').children.length,4,'waiting and needs-correction states remain explicit');
 for(const path of d.querySelectorAll('svg.connections path')){
  const coords=path.getAttribute('d').match(/-?\d+(?:\.\d+)?/g).map(Number);
  for(let i=0;i<coords.length;i+=2){
   assert.ok(coords[i]>=0&&coords[i]<=spec.width,'edge exceeds world width');
   assert.ok(coords[i+1]>=0&&coords[i+1]<=spec.height,'edge exceeds world height');
   if(path.classList.contains('edge'))assert.ok(coords[i+1]<=spec.data_height,'data dependencies stay out of the revision lane');
  }
 }
 const before=selectionEvents().length,mark=sequence.length;
 correction.click();
 if(spec.control_nodes[0].actionable){
  assert.equal(selectionEvents().length,before+1);
  const action=selectionEvents().at(-1).event.value;
  assert.equal(action.action??null,spec.control_nodes[0].action);assert.equal(action.node,'review');
  assert.ok(action.serial);
  if(spec.feedback_branch.mode==='human'){
   assert.equal(sequence[mark].type,'scroll','the human scoring editor is reached before publishing its action');
   assert.equal(sequence[mark].options.top,1032);
   assert.equal(sequence[mark].options.behavior,'smooth');
  }else assert.equal(sequence[mark].type,'event','the automatic branch opens its real stage without navigating to human work');
 }else assert.equal(selectionEvents().length,before,'waiting/limited controls never submit work');
 const beforeRender=selectionEvents().length;
 render({...spec,feedback_target:'human-results-editor'});
 assert.equal(selectionEvents().length,beforeRender,'refreshes never replay the human action');
 if(spec.language==='en')assert.equal(/[\u4e00-\u9fff]/.test(d.querySelector('.feedback-node').textContent+d.querySelector('.feedback-heading').textContent),false);
}
const actionable=specs.find(spec=>spec.control_nodes[0].actionable&&spec.feedback_branch.mode==='human');
reduced=true;render({...actionable,feedback_target:'human-results-editor'});d.querySelector('.feedback-node').click();
assert.equal(sequence.at(-2).type,'scroll');assert.equal(sequence.at(-2).options.behavior,'instant');
const scrolls=()=>sequence.filter(item=>item.type==='scroll').length;
for(const extra of [{feedback_target:'unsafe/key'}, {feedback_target:undefined}]){
 const count=scrolls(),events=selectionEvents().length;
 render({...actionable,...extra});d.querySelector('.feedback-node').click();
 assert.equal(scrolls(),count,'a missing or invalid parent target cannot navigate another area');
 assert.equal(selectionEvents().length,events+1,'normal live canvases can still open the human workspace explicitly');
}
console.log('Human correction routes, real node mapping, status colors, action receipts, parent editor navigation, and stable refresh passed');
dom.window.close();
