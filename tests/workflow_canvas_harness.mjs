import fs from 'node:fs';
import assert from 'node:assert/strict';
import {JSDOM} from 'jsdom';
const html=fs.readFileSync('lib/presentation/streamlit/workflow_canvas_frontend/index.html','utf8');
const dom=new JSDOM(html,{runScripts:'dangerously',beforeParse(w){w.ResizeObserver=class{constructor(callback){w.resizeCanvas=callback}observe(){}};}});
const w=dom.window,d=w.document,events=[];
w.postMessage=(value)=>events.push(value);
const view=d.getElementById('view'),canvas=d.getElementById('canvas'),space=d.getElementById('space');
Object.defineProperty(view,'clientWidth',{value:680,configurable:true});
Object.defineProperty(view,'clientHeight',{get(){return parseFloat(view.style.height)||0},configurable:true});
Object.defineProperty(d.querySelector('.shell'),'offsetHeight',{get(){return view.clientHeight+80}});
Object.defineProperty(canvas,'offsetLeft',{get(){return parseFloat(canvas.style.left)||0}});
Object.defineProperty(canvas,'offsetTop',{get(){return parseFloat(canvas.style.top)||0}});
let scrollLeft=0,scrollTop=0;
Object.defineProperty(view,'scrollLeft',{get(){return scrollLeft},set(value){scrollLeft=Math.max(0,Math.min(value,(parseFloat(space.style.width)||0)-view.clientWidth))}});
Object.defineProperty(view,'scrollTop',{get(){return scrollTop},set(value){scrollTop=Math.max(0,Math.min(value,(parseFloat(space.style.height)||0)-view.clientHeight))}});
view.setPointerCapture=()=>{};
const labels={node_picker:'Go to node',fit:'Fit',focus:'Locate',hint:'Select',lineage:'Dependencies',overview:'3 nodes',reset:'Actual size',zoom_in:'Zoom in',zoom_out:'Zoom out'};
const nodes=[{id:'ingest',label:'Input',x:24,y:24},{id:'sft',label:'SFT <source>',x:294,y:24},{id:'package',label:'Package',x:564,y:24}].map(n=>({...n,status:'pending',subtitle:'Choose model',glyph:'◇'}));
function renderSpec(spec){w.dispatchEvent(new w.MessageEvent('message',{source:w,data:{type:'streamlit:render',args:{spec}}}));}
function render(selected,language='en'){renderSpec({nodes,edges:[['ingest','sft'],['sft','package']],selected,width:810,height:126,labels,language});}
render('ingest');
assert.equal(d.documentElement.lang,'en');
assert.equal(d.getElementById('scale').textContent,'82%');
assert.ok(810*Number(d.getElementById('canvas').style.transform.match(/[\d.]+/)[0])<=680);
assert.ok(view.clientHeight<200,'a single-row graph must not leave a 320px viewport');
const wideHeight=view.clientHeight;
const frameEvents=()=>events.filter(event=>event.type==='streamlit:setFrameHeight').length;
const initialFrames=frameEvents();
for(let index=0;index<20;index++)w.resizeCanvas();
assert.equal(view.clientHeight,wideHeight,'observer notifications must converge to a stable height');
assert.equal(frameEvents(),initialFrames,'stable observer callbacks must not keep posting iframe heights');
Object.defineProperty(d.getElementById('view'),'clientWidth',{value:540,configurable:true});
w.resizeCanvas();
assert.equal(d.getElementById('scale').textContent,'68%');
assert.ok(810*.68>540,'initial fit keeps labels readable and allows horizontal navigation');
assert.ok(view.clientHeight<wideHeight,'fit height should follow the actual available width');
d.getElementById('fit').click();
assert.equal(d.getElementById('scale').textContent,'65%','explicit fit can show the complete graph at a smaller scale');
assert.notEqual(d.getElementById('canvas').style.transform,'scale(1)');
const manualHeight=view.clientHeight;
d.getElementById('reset').click();
assert.equal(d.getElementById('canvas').style.transform,'scale(1)');
assert.equal(view.clientHeight,manualHeight,'manual zoom must keep the page height stable');
Object.defineProperty(d.getElementById('view'),'clientWidth',{value:680,configurable:true});
w.resizeCanvas();
assert.equal(d.getElementById('scale').textContent,'100%');
assert.equal(view.clientHeight,manualHeight,'manual mode must keep the viewport height on resize');
nodes[1].models=['Generate: writer <source>', 'Review: critic & judge'];
render('sft');
assert.equal(d.getElementById('scale').textContent,'100%');
assert.equal(view.clientHeight,manualHeight,'selection refresh must not reset manual viewport height');
assert.deepEqual([...d.querySelector('[data-node="sft"] .copy').querySelectorAll('small')].map(x=>x.textContent),nodes[1].models);
assert.equal(d.querySelector('[data-node="sft"] source'),null);
assert.ok(d.querySelector('[data-node="sft"]').title.includes('Review: critic & judge'));
assert.ok(d.querySelector('[data-node="sft"]').getAttribute('aria-label').includes('Generate: writer <source>'));
assert.ok(d.querySelector('[data-node="sft"]').getAttribute('aria-label').includes('Review: critic & judge'));
render('ingest');
const picker=d.getElementById('node-picker');
assert.equal(picker.getAttribute('aria-label'),'Go to node');
assert.equal(picker.options.length,3);
assert.equal(picker.options[1].textContent,'SFT <source>');
assert.equal(picker.querySelector('source'),null);
picker.value='package';picker.dispatchEvent(new w.Event('change'));
assert.equal(events.at(-1).value.node,'package');
render('package','zh');
assert.equal(d.documentElement.lang,'zh-CN');
assert.equal(picker.value,'package');
assert.equal(d.querySelector('.node.selected').dataset.node,'package');
assert.equal(d.querySelector('path[data-from="sft"]').getAttribute('stroke-width'),'3');
assert.equal(d.querySelector('path[data-from="ingest"]').getAttribute('stroke-width'),'2');
d.querySelector('[data-node="sft"]').click();
assert.equal(events.at(-1).value.node,'sft');
// Panning leaves auto-fit, so a progress refresh cannot recenter the user's view.
d.getElementById('fit').click();
const pannedHeight=view.clientHeight;
view.onpointerdown({target:view,button:0,clientX:100,clientY:50,pointerId:1});
Object.defineProperty(view,'clientWidth',{value:540,configurable:true});
w.resizeCanvas();
assert.equal(d.getElementById('scale').textContent,'82%');
assert.equal(view.clientHeight,pannedHeight);
view.onpointermove({clientX:50,clientY:50});
view.onpointerup();
const pannedLeft=view.scrollLeft;
assert.ok(pannedLeft>0);
render('package');
assert.equal(view.scrollLeft,pannedLeft,'progress updates must preserve deliberate panning');
assert.equal(view.clientHeight,pannedHeight);
d.getElementById('fit').click();
assert.equal(d.getElementById('scale').textContent,'65%');
assert.ok(view.clientHeight<pannedHeight);
assert.ok(events.some(event=>event.type==='streamlit:setFrameHeight'&&event.height===view.clientHeight+80));

if(process.argv.includes('--check-specs')){
 const specs=JSON.parse(fs.readFileSync(0,'utf8'));
 for(const spec of specs){
  renderSpec(spec);d.getElementById('fit').click();
  const zoom=Number(canvas.style.transform.match(/[\d.]+/)[0]);
  assert.ok(view.clientHeight>=spec.height*zoom,'fit must contain the complete graph vertically');
  for(const node of spec.nodes){
   assert.ok(node.y>=0&&node.y+78<=spec.height,`${node.id} outside world`);
  }
  // Every actual SVG control point must stay inside the world, including bypass rails.
  for(const path of d.querySelectorAll('svg path')){
   const coords=path.getAttribute('d').match(/-?\d+(?:\.\d+)?/g).map(Number);
   for(let index=0;index<coords.length;index+=2){
    assert.ok(coords[index]>=0&&coords[index]<=spec.width,'edge exceeds world width');
    assert.ok(coords[index+1]>=0&&coords[index+1]<=spec.height,'edge exceeds world height');
   }
  }
  // Both the compact and multi-row graphs remain navigable after zooming.
  const fitHeight=view.clientHeight;
  d.getElementById('reset').click();
  renderSpec({...spec,selected:spec.nodes[0].id});
  assert.equal(view.clientHeight,fitHeight);
  const selected=spec.nodes[0],left=selected.x+canvas.offsetLeft-view.scrollLeft,top=selected.y+canvas.offsetTop-view.scrollTop;
  assert.ok(left>=0&&left+218<=view.clientWidth,'located node is horizontally clipped');
  assert.ok(top<view.clientHeight&&top+78>0,'located node is vertically unreachable');
 }
}
// The inspector remains a native Streamlit subtree in the parent document.
// Exercise the actual bridge inside an iframe rather than mocking its API.
const bridgeDom=new JSDOM('<!doctype html><iframe id="workflow-frame"></iframe><div id="inspector-host" data-testid="stLayoutWrapper"></div>',{runScripts:'dangerously',pretendToBeVisual:true});
const outer=bridgeDom.window,outerDocument=outer.document,iframe=outerDocument.getElementById('workflow-frame'),inner=iframe.contentWindow;
Object.defineProperty(outer,'innerWidth',{value:1280,configurable:true});
Object.defineProperty(outer,'innerHeight',{value:900,configurable:true});
let frameTop=160;
iframe.getBoundingClientRect=()=>({left:100,top:frameTop,right:1120,bottom:frameTop+500,width:1020,height:500});
const bridgeEvents=[],animationFrames=new Map();let animationSerial=0;
outer.postMessage=value=>bridgeEvents.push(value);
inner.ResizeObserver=class{constructor(callback){inner.resizeCanvas=callback}observe(){}};
inner.requestAnimationFrame=callback=>{const id=++animationSerial;animationFrames.set(id,callback);return id};
inner.cancelAnimationFrame=id=>animationFrames.delete(id);
const script=html.match(/<script>([\s\S]*)<\/script>/)[1];
inner.document.open();inner.document.write(html.replace(/<script>[\s\S]*<\/script>/,''));inner.document.close();inner.eval(script);
const innerDocument=inner.document,innerView=innerDocument.getElementById('view'),innerCanvas=innerDocument.getElementById('canvas'),innerSpace=innerDocument.getElementById('space');
let innerWidth=1000,innerLeft=0,innerTop=0;
Object.defineProperty(innerView,'clientWidth',{get(){return innerWidth}});
Object.defineProperty(innerView,'clientHeight',{get(){return parseFloat(innerView.style.height)||0}});
Object.defineProperty(innerCanvas,'offsetLeft',{get(){return parseFloat(innerCanvas.style.left)||0}});
Object.defineProperty(innerCanvas,'offsetTop',{get(){return parseFloat(innerCanvas.style.top)||0}});
Object.defineProperty(innerView,'scrollLeft',{get(){return innerLeft},set(value){innerLeft=Math.max(0,Math.min(value,Math.max(0,(parseFloat(innerSpace.style.width)||0)-innerWidth)))}});
Object.defineProperty(innerView,'scrollTop',{get(){return innerTop},set(value){innerTop=Math.max(0,Math.min(value,Math.max(0,(parseFloat(innerSpace.style.height)||0)-innerView.clientHeight)))}});
innerView.getBoundingClientRect=()=>({left:10,top:42,right:10+innerWidth,bottom:42+innerView.clientHeight,width:innerWidth,height:innerView.clientHeight});
Object.defineProperty(innerDocument.querySelector('.shell'),'offsetHeight',{get(){return innerView.clientHeight+80}});
innerView.setPointerCapture=()=>{};
const originalRect=inner.HTMLElement.prototype.getBoundingClientRect;
inner.HTMLElement.prototype.getBoundingClientRect=function(){
 if(!this.classList.contains('node'))return originalRect.call(this);
 const zoom=Number(innerCanvas.style.transform.match(/[\d.]+/)[0]),left=10+innerCanvas.offsetLeft+parseFloat(this.style.left)*zoom-innerLeft,top=42+innerCanvas.offsetTop+parseFloat(this.style.top)*zoom-innerTop;
 return {left,top,right:left+218*zoom,bottom:top+78*zoom,width:218*zoom,height:78*zoom};
};
function bridgeRender(selected,extra={}){inner.dispatchEvent(new inner.MessageEvent('message',{source:outer,data:{type:'streamlit:render',args:{spec:{nodes,edges:[['ingest','sft'],['sft','package']],selected,width:810,height:126,labels,language:'en',expanded:true,inspector:{key:'workbench-node-panel',open:true},...extra}}}}))}
async function flushBridge(){await Promise.resolve();const callbacks=[...animationFrames.values()];animationFrames.clear();for(const callback of callbacks)callback();await Promise.resolve()}
function addPanel(){const panel=outerDocument.createElement('div');panel.className='st-key-workbench-node-panel';panel.innerHTML='<label>Model <input id="native-model" value="writer"></label>';Object.defineProperty(panel,'scrollHeight',{value:480});outerDocument.getElementById('inspector-host').append(panel);return panel}
const initialFocus=outerDocument.createElement('input');outerDocument.body.append(initialFocus);initialFocus.focus();
bridgeRender('ingest',{inspector:{key:'workbench-node-panel',open:false}});await flushBridge();
assert.equal(outerDocument.activeElement,initialFocus,'the initial closed inspector never steals page focus');
bridgeRender('ingest');
assert.equal(innerView.clientHeight,420,'expanded setup reserves a useful canvas area even for a single-row graph');
let nativePanel=addPanel();await flushBridge();
assert.equal(nativePanel.dataset.workflowInspector,'floating','late native panels are attached by the observer');
assert.equal(nativePanel.parentElement.id,'inspector-host','the bridge never reparents native Streamlit controls');
assert.equal(nativePanel.style.width,'430px');
assert.equal(nativePanel.dataset.workflowPlacement,'right');
assert.equal(nativePanel.parentElement.dataset.workflowInspectorHost,'floating');
assert.ok(parseFloat(nativePanel.style.left)>=110&&parseFloat(nativePanel.style.left)+430<=1110);
assert.ok(parseFloat(nativePanel.style.top)>=76&&parseFloat(nativePanel.style.top)+parseFloat(nativePanel.style.maxHeight)<=884);
const nativeInput=outerDocument.getElementById('native-model');nativeInput.value='edited-writer';nativeInput.focus();
nativePanel.scrollTop=200;bridgeRender('ingest');await flushBridge();
assert.equal(nativePanel.scrollTop,200,'same-node widget reruns retain the form scroll position');
bridgeRender('package');await flushBridge();
assert.equal(nativePanel.scrollTop,0,'switching nodes starts the new form at its heading');
assert.equal(nativePanel.dataset.workflowPlacement,'left','right-edge nodes place the inspector to their left');
assert.equal(nativeInput.value,'edited-writer','a canvas render preserves native input edits');
assert.equal(outerDocument.activeElement,nativeInput,'progress renders do not steal native form focus');
const beforeScroll=parseFloat(nativePanel.style.top);frameTop-=60;outer.dispatchEvent(new outer.Event('scroll'));await flushBridge();
assert.equal(parseFloat(nativePanel.style.top),beforeScroll-60,'the inspector follows parent page scrolling');
for(let index=0;index<5;index++)innerDocument.getElementById('plus').click();await flushBridge();
const zoomPosition=parseFloat(nativePanel.style.left);
innerView.onpointerdown({target:innerView,button:0,clientX:300,clientY:100,pointerId:1});
innerView.onpointermove({clientX:160,clientY:100});innerView.onpointerup({type:'pointerup'});await flushBridge();
assert.notEqual(parseFloat(nativePanel.style.left),zoomPosition,'panning follows the selected node without a Python rerun');
assert.ok(!bridgeEvents.some(event=>event.value?.action==='close'),'dragging never dismisses the inspector');
const beforeSelectionScroll=innerView.scrollLeft;bridgeRender('sft');await flushBridge();
assert.equal(innerView.scrollLeft,beforeSelectionScroll,'selecting a visible setup node preserves its canvas position');
const closeCount=()=>bridgeEvents.filter(event=>event.value?.action==='close').length;
innerView.onpointerdown({target:innerView,button:0,clientX:200,clientY:150,pointerId:2});innerView.onpointerup({type:'pointerup'});
assert.equal(closeCount(),1,'a plain canvas background click closes the inspector');
assert.equal(bridgeEvents.at(-1).value.node,'sft');
bridgeRender('sft',{inspector:{key:'workbench-node-panel',open:false}});await flushBridge();
assert.equal(nativePanel.dataset.workflowInspector,'floating');
assert.equal(nativePanel.dataset.workflowInspectorOpen,'false');
assert.equal(nativePanel.style.display,'none','closed native controls stay mounted and hidden');
assert.equal(nativePanel.parentElement.dataset.workflowInspectorHost,'floating','closing does not reserve an empty inspector row');
assert.equal(innerDocument.activeElement.dataset.node,'sft','closing returns keyboard focus to the selected node');
bridgeRender('sft');await flushBridge();
assert.equal(nativePanel.dataset.workflowInspectorOpen,'true');
assert.equal(nativePanel.style.display,'','reopening makes the same native form visible');
nativePanel.remove();nativePanel=addPanel();await flushBridge();
assert.equal(nativePanel.dataset.workflowInspector,'floating','React replacement panels attach without reparenting or stale styles');
const menu=outerDocument.createElement('div');menu.setAttribute('role','listbox');menu.getClientRects=()=>[{width:200,height:80}];outerDocument.body.append(menu);
outerDocument.dispatchEvent(new outer.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));
assert.equal(closeCount(),1,'Escape belongs to an open model dropdown before the inspector');menu.remove();
outerDocument.dispatchEvent(new outer.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));
assert.equal(closeCount(),2,'Escape from a native form closes its inspector');
bridgeRender('sft',{inspector:{key:'workbench-node-panel',open:false}});await flushBridge();
bridgeRender('sft');await flushBridge();nativePanel.querySelector('input').focus();
bridgeRender('sft',{inspector:{key:'workbench-node-panel',open:false}});await flushBridge();
assert.equal(innerDocument.activeElement.dataset.node,'sft','the native close button transition also returns keyboard focus to its node');
bridgeRender('sft');await flushBridge();
// Panning a selected node completely offscreen still leaves the form reachable.
innerWidth=330;inner.resizeCanvas();innerView.scrollLeft=10000;innerView.dispatchEvent(new inner.Event('scroll'));await flushBridge();
assert.ok(parseFloat(nativePanel.style.left)>=12&&parseFloat(nativePanel.style.left)+parseFloat(nativePanel.style.width)<=1268);
assert.ok(parseFloat(nativePanel.style.width)<=innerWidth);
bridgeRender('sft',{inspector:undefined});await flushBridge();
assert.equal(nativePanel.dataset.workflowInspector,undefined,'removing inspector support restores inline fallback');
assert.equal(animationFrames.size,0,'closed inspectors leave no pending animation loop');
bridgeDom.window.close();
console.log('Canvas navigation, compact fit, expanded setup, native inspector anchoring, dismiss/reopen, manual viewport stability, and edge boundaries passed');
dom.window.close();
