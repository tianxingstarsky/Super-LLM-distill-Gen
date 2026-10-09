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
console.log('Canvas navigation, compact fit, manual viewport stability, and edge boundaries passed');
dom.window.close();
