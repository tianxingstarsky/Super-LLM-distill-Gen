import * as THREE from './vendor/three.module.min.js';
import {DURATION,SCENES,clamp,sceneAt,cueAt,ease} from './timeline.mjs';

// The same 1920 × 1080 canvas is used by the player and the video recorder.
// Captions and motion graphics are composited into it, rather than DOM overlays.
const W=1920,H=1080, FONT='Promo, "Microsoft YaHei", "Noto Sans SC", sans-serif';
const $=id=>document.getElementById(id);
const canvas=$('film'),ctx=canvas.getContext('2d',{alpha:false});
const images=new Map();
const SHOTS=[
  {start:10,end:14.8,key:'home',title:'从资料出发，选择训练目标',label:'真实界面 · 首页',crop:[250,330,815,537]},
  {start:18,end:24.8,key:'model',title:'模型与提示词，就在节点上配置',label:'真实界面 · 节点配置',crop:[254,357,1480,854],focus:[880,605,640,605],features:['上下文与单次输出上限','生成与评审可复用模型','提示词可编辑、保存与还原']},
  {start:28,end:34.8,key:'director',title:'让指导员理解目的，设计自然的互动',label:'真实界面 · 对话指导员',crop:[254,357,1480,810],focus:[600,595,700,575],features:['理解目的与上下文','设计澄清、修改与协作','按真实回应继续多轮']},
  {start:39,end:44.8,key:'library',title:'来源、样本与产物，随时回看',label:'真实界面 · 数据管理',crop:[254,495,1489,650]},
  {start:50,end:53.9,key:'readme',title:'从项目主页，认识你的下一张工作台',label:'项目 README · 本地文档预览',crop:[0,66,1264,848]},
];
async function loadImage(key,url){const image=new Image();image.src=url;await image.decode();images.set(key,image);return image;}
const renderer=new THREE.WebGLRenderer({antialias:true,alpha:true,preserveDrawingBuffer:true,powerPreference:'high-performance'});
renderer.setSize(W,H);renderer.setPixelRatio(1);renderer.setClearColor(0,0);
renderer.outputColorSpace=THREE.SRGBColorSpace;renderer.toneMapping=THREE.ACESFilmicToneMapping;renderer.toneMappingExposure=1.25;
const world=new THREE.Scene(),camera=new THREE.PerspectiveCamera(38,W/H,.1,100);
camera.position.set(0,2.8,19);camera.lookAt(0,.4,0);
world.add(new THREE.HemisphereLight(0xeef7ff,0x648baf,3.5));
function light(color,intensity,x,y,z){const a=new THREE.DirectionalLight(color,intensity);a.position.set(x,y,z);world.add(a);}
light(0xffffff,5,-7,10,8);light(0x438bff,3,8,2,-3);light(0xd7ccff,2,-4,-2,4);
const palette=[0x5d98ff,0x8dcaff,0x47c7d4,0xa08cff,0x66cbb4,0xffffff];
const material=(color,opacity=1)=>new THREE.MeshPhysicalMaterial({color,metalness:.17,roughness:.22,clearcoat:1,clearcoatRoughness:.16,transparent:opacity<1,opacity});
const mats=palette.map(c=>material(c));
const white=material(0xf7fbff),ink=material(0x3077ed),violet=material(0x9a7ce5);
function roundGeometry(w,h,d,r=.14){
  r=Math.min(r,w/2,h/2);
  const s=new THREE.Shape();const x=-w/2,y=-h/2;
  s.moveTo(x+r,y);s.lineTo(x+w-r,y);s.quadraticCurveTo(x+w,y,x+w,y+r);s.lineTo(x+w,y+h-r);s.quadraticCurveTo(x+w,y+h,x+w-r,y+h);s.lineTo(x+r,y+h);s.quadraticCurveTo(x,y+h,x,y+h-r);s.lineTo(x,y+r);s.quadraticCurveTo(x,y,x+r,y);
  const g=new THREE.ExtrudeGeometry(s,{depth:d,bevelEnabled:true,bevelSegments:3,steps:1,bevelSize:Math.min(r/2,.08),bevelThickness:Math.min(r/2,.08),curveSegments:6});g.translate(0,0,-d/2);return g;
}
const cubeGeom=roundGeometry(.73,.73,.73,.16),smallGeom=roundGeometry(.18,.18,.18,.045);
function mesh(parent,g,m,x=0,y=0,z=0){const a=new THREE.Mesh(g,m);a.position.set(x,y,z);parent.add(a);return a;}
function box(parent,w,h,d,m,x=0,y=0,z=0,r=.12){return mesh(parent,roundGeometry(w,h,d,r),m,x,y,z);}
function rod(parent,a,b,color=0x75b3ff,r=.012){const delta=new THREE.Vector3().subVectors(b,a);const m=mesh(parent,new THREE.CylinderGeometry(r,r,delta.length(),8),material(color));m.position.copy(a).add(b).multiplyScalar(.5);m.quaternion.setFromUnitVectors(new THREE.Vector3(0,1,0),delta.normalize());return m;}
function ring(parent,r,color,rotation={x:0,y:0,z:0}){const m=mesh(parent,new THREE.TorusGeometry(r,.012,8,128),material(color));m.rotation.set(rotation.x||0,rotation.y||0,rotation.z||0);return m;}
function documentMesh(parent,x,y,z,color=0x347af0){const g=new THREE.Group();g.position.set(x,y,z);parent.add(g);box(g,1.15,1.5,.08,white);box(g,.29,.3,.028,material(color),-.26,.37,.075,.04);for(let i=0;i<4;i++)box(g,.72-i*.065,.035,.024,material(i?0xc5d5e9:color),0,.02-i*.17,.075,.01);return g;}
function cube(parent,x,y,z,scale=1){const g=new THREE.Group();g.position.set(x,y,z);g.scale.setScalar(scale);parent.add(g);const blocks=[];for(let a=-1;a<=1;a++)for(let b=-1;b<=1;b++)for(let c=-1;c<=1;c++){const m=mesh(g,cubeGeom,mats[(a+b+c+9)%5],a*.82,b*.82,c*.82);m.userData.home=m.position.clone();blocks.push(m);}g.userData.blocks=blocks;return g;}
function tick(parent,x,y,z,scale=1){const g=new THREE.Group();g.position.set(x,y,z);g.scale.setScalar(scale);parent.add(g);rod(g,new THREE.Vector3(-.22,0,0),new THREE.Vector3(-.04,-.17,0),0x72e4cd,.047);rod(g,new THREE.Vector3(-.04,-.17,0),new THREE.Vector3(.34,.25,0),0x72e4cd,.047);return g;}
function makeParticleField(parent,count=100){const inst=new THREE.InstancedMesh(smallGeom,material(0x86b6ff,.5),count),dummy=new THREE.Object3D();parent.add(inst);return t=>{for(let i=0;i<count;i++){const seed=i*137.508;dummy.position.set(Math.sin(seed)*10+Math.sin(t*.25+i)*.1,Math.cos(seed*2.1)*5,Math.sin(seed*.7)*4-7);dummy.rotation.set(t*.3+i,t*.2+i,0);dummy.scale.setScalar(.15+(i%7)*.07);dummy.updateMatrix();inst.setMatrixAt(i,dummy.matrix);}inst.instanceMatrix.needsUpdate=true;};}
const particles=new THREE.Group();world.add(particles);const updateParticles=makeParticleField(particles);
const stages=[];
function stage(){const g=new THREE.Group();world.add(g);stages.push(g);return g;}

// 01 / Brand: separate source fragments assemble into a dimensional data cube.
const s0=stage(),hero=cube(s0,4.55,.1,0,1.25),halo=ring(s0,2.7,0x8dbaff,{x:.65,y:.28,z:.2});halo.position.set(4.55,.1,0);
const sourceDocs=[documentMesh(s0,2.1,1.7,-1.2),documentMesh(s0,7.1,.3,-1.4,0x58bbbd),documentMesh(s0,4.3,-2,-.5,0x9d7be3)];
const heroDots=[];for(let i=0;i<16;i++)heroDots.push(mesh(s0,smallGeom,mats[i%5]));

// 02 / Source cards and three major training goals.
const s1=stage(),inputStack=new THREE.Group();s1.add(inputStack);inputStack.position.set(4.6,.2,0);
const stackDocs=[];for(let i=0;i<5;i++){const d=documentMesh(inputStack,(i-2)*.44,(i-2)*.21,-i*.36,palette[i%5]);d.scale.setScalar(1.65);d.rotation.set(-.12,.2,(i-2)*.1);stackDocs.push(d);}
const goalCubes=[cube(s1,2.5,-1.95,1,.25),cube(s1,4.55,-1.95,1,.25),cube(s1,6.6,-1.95,1,.25)];
for(let i=0;i<3;i++)rod(s1,new THREE.Vector3(4.5,-.55,-.5),new THREE.Vector3(2.5+i*2.05,-1.6,1),0xa9caff,.012);

// 03 / A real workflow metaphor, with model, prompt and stream panels.
const s2=stage();const nodePositions=[[2.1,1.4,0],[4.25,1.4,0],[6.4,1.4,0],[6.4,-.35,0],[4.25,-.35,0],[2.1,-.35,0]];
const nodes=nodePositions.map((p,i)=>{const g=new THREE.Group();g.position.set(...p);s2.add(g);box(g,1.22,.87,.25,mats[i%5]);box(g,.43,.43,.14,white,0,0,.23,.08);return g;});
const streamDots=[];for(let i=0;i<nodePositions.length-1;i++){rod(s2,new THREE.Vector3(...nodePositions[i]),new THREE.Vector3(...nodePositions[i+1]),0x9fc4f1,.018);for(let j=0;j<4;j++)streamDots.push({mesh:mesh(s2,smallGeom,mats[2]),from:new THREE.Vector3(...nodePositions[i]),to:new THREE.Vector3(...nodePositions[i+1]),index:j});}

// 04 / Open dialogue: each response informs the next turn.
const s3=stage(),conversation=new THREE.Group();s3.add(conversation);conversation.position.set(4.7,.1,0);
const talkCards=[];for(let i=0;i<4;i++){const g=new THREE.Group();conversation.add(g);g.position.set(i%2?.32:-.32,1.8-i*1.1,-i*.24);box(g,3.3,.8,.12,i%2?mats[1]:white,0,0,0,.16);for(let j=0;j<2;j++)box(g,2.4-j*.55,.042,.02,i%2?material(0x3879b9):material(0xaac1dc),-.18,.12-j*.23,.12,.01);talkCards.push(g);}
const dialogueOrbit=ring(s3,2.95,0x9db9ed,{x:.3,y:.7});dialogueOrbit.position.set(4.7,.1,-1.2);

// 05 / Batch production: source-preserving checks, review and saved progress.
const s4=stage(),batchGroup=new THREE.Group();s4.add(batchGroup);batchGroup.position.set(4.7,.1,0);
const batches=[];for(let row=0;row<4;row++)for(let col=0;col<5;col++){const m=box(batchGroup,.62,.62,.5,mats[(row+col)%5],(col-2)*.8,(row-1.5)*.8,0,.12);batches.push(m);}
const qcRing=ring(s4,2.3,0x75e6d3,{y:.55});qcRing.position.set(4.7,.1,.4);
const savedTicks=[];for(let i=0;i<3;i++)savedTicks.push(tick(s4,2.2+i*2.4,-2.1,.8,.9));

// 06 / Three practical audiences, expressed through dimensional symbols.
const s5=stage();const audienceGroups=[];
for(let i=0;i<3;i++){const g=new THREE.Group();g.position.set(-5.4+i*5.4,-.15,0);s5.add(g);audienceGroups.push(g);box(g,3.55,.22,2.5,white,0,-1.45,0,.2);ring(g,1.6,palette[i],{x:Math.PI/2}).position.set(0,-1.29,0);}
const book=audienceGroups[0];box(book,1.22,1.45,.22,mats[1],-.55,.05,0,.1).rotation.z=.08;box(book,1.22,1.45,.22,mats[0],.55,.05,0,.1).rotation.z=-.08;box(book,.08,1.5,.28,white,0,.04,.08,.02);for(let j=0;j<3;j++){box(book,.67,.045,.03,white,-.55,.38-j*.25,.2,.01);box(book,.67,.045,.03,white,.55,.38-j*.25,.2,.01);}
const building=audienceGroups[1];box(building,1.7,2.15,1.1,mats[0],0,-.04,0,.18);for(let x=0;x<3;x++)for(let y=0;y<3;y++)box(building,.25,.27,.03,white,-.5+x*.5,.6-y*.5,.65,.035);
const atom=audienceGroups[2];mesh(atom,new THREE.IcosahedronGeometry(.63,1),mats[3],0,.05,0);for(let i=0;i<3;i++)ring(atom,1.28,palette[i],{x:i*1.0,y:.6,z:i*1.1});

// 07 / Resolution uses the project's real, transparent brand asset.
const s6=stage(),finalBrand=new THREE.Group();s6.add(finalBrand);finalBrand.position.set(5,0,0);
const finalRing=ring(s6,2.55,0xadc9f2,{x:.6,y:.5});finalRing.position.set(5,0,-.5);

const projected=new THREE.Vector3();
function project(x,y,z){projected.set(x,y,z).project(camera);return[(projected.x*.5+.5)*W,(-projected.y*.5+.5)*H];}
function rr(x,y,w,h,r=16,fill='#fff',stroke=null){ctx.beginPath();ctx.roundRect(x,y,w,h,r);if(fill){ctx.fillStyle=fill;ctx.fill();}if(stroke){ctx.strokeStyle=stroke;ctx.lineWidth=1.4;ctx.stroke();}}
function text(value,x,y,size=30,color='#1a3459',weight=500,align='left'){ctx.font=`${weight} ${size}px ${FONT}`;ctx.textAlign=align;ctx.textBaseline='alphabetic';ctx.fillStyle=color;ctx.fillText(value,x,y);}
function tracked(value,x,y,size=16,color='#6788af',spacing=4){ctx.font=`500 ${size}px ${FONT}`;ctx.fillStyle=color;ctx.textAlign='left';for(const c of value){ctx.fillText(c,x,y);x+=ctx.measureText(c).width+spacing;}}
function panel(x,y,w,h,draw,dark=false){ctx.save();ctx.shadowColor=dark?'#00000030':'#638eca20';ctx.shadowBlur=40;ctx.shadowOffsetY=18;rr(x,y,w,h,25,dark?'#173855ee':'#ffffffed',dark?'#72a8d52a':'#ffffff');ctx.restore();draw();}
function chip(label,x,y,accent='#3679dc',dark=false){ctx.font=`550 21px ${FONT}`;const w=ctx.measureText(label).width+40;rr(x,y,w,44,22,dark?'#3974a526':'#e8f1ff',dark?'#8dbbed33':'#c3d8f650');text(label,x+20,y+29,21,accent,550);return w;}
function line(x1,y1,x2,y2,color='#94b6e4'){ctx.beginPath();ctx.moveTo(x1,y1);ctx.lineTo(x2,y2);ctx.strokeStyle=color;ctx.lineWidth=2;ctx.stroke();}
function iconCheck(x,y,color='#2ab89b'){ctx.lineWidth=4;ctx.strokeStyle=color;ctx.beginPath();ctx.moveTo(x-8,y);ctx.lineTo(x-2,y+6);ctx.lineTo(x+10,y-9);ctx.stroke();}
function brandMark(x,y,size=22){const image=images.get('brand');if(image)ctx.drawImage(image,x-size*1.8,y-size*1.8,size*3.6,size*3.6);}
function proofShot(t,dark){
  const shot=SHOTS.find(s=>t>=s.start&&t<s.end);if(!shot)return;
  const image=images.get(shot.key);if(!image)return;
  const local=t-shot.start,duration=shot.end-shot.start;
  const alpha=ease(Math.min(local/.55,(duration-local)/.4));
  const bg=dark?'#102c49':'#eaf3ff';
  ctx.save();ctx.globalAlpha=alpha;ctx.fillStyle=bg;ctx.fillRect(0,148,W,814);
  tracked(shot.label.toUpperCase(),120,191,16,dark?'#93bddb':'#6f8db3',2);
  text(shot.title,120,249,43,dark?'#edf6ff':'#1c406c',650);
  let crop=shot.crop||[0,0,image.naturalWidth,image.naturalHeight],focusProgress=0;
  if(shot.focus){focusProgress=ease((local-1.5)/3);crop=crop.map((v,i)=>v+(shot.focus[i]-v)*focusProgress);}
  const [sx,sy,sw,sh]=crop,scale=Math.min(1656/sw,646/sh),dw=sw*scale,dh=sh*scale;
  const x=(W-dw)/2+((W-dw)/2-135)*focusProgress,y=290+(646-dh)/2,zoom=1+clamp(local/duration)*.027;
  if(shot.features){ctx.save();ctx.globalAlpha*=ease((focusProgress-.55)/.45);tracked('YOUR NODE. YOUR CHOICE.',150,440,17,'#7796ba',2.4);shot.features.forEach((label,i)=>{text(String(i+1).padStart(2,'0'),150,525+i*87,18,'#7fa6d5',500);text(label,206,525+i*87,34,'#3c6390',550);});ctx.restore();}
  ctx.save();ctx.shadowColor=dark?'#00000044':'#4077b83b';ctx.shadowBlur=38;ctx.shadowOffsetY=17;
  rr(x-8,y-8,dw+16,dh+16,19,'#ffffff',dark?'#5d81a333':'#ffffff');ctx.restore();
  ctx.save();ctx.beginPath();ctx.roundRect(x,y,dw,dh,13);ctx.clip();
  ctx.drawImage(image,sx,sy,sw,sh,x-(dw*(zoom-1)/2),y-(dh*(zoom-1)/2),dw*zoom,dh*zoom);ctx.restore();
  ctx.restore();
}
function background(t,dark){const grad=ctx.createLinearGradient(0,0,W,H);if(dark){grad.addColorStop(0,'#0c2039');grad.addColorStop(.55,'#123356');grad.addColorStop(1,'#16344d');}else{grad.addColorStop(0,'#f9fcff');grad.addColorStop(.5,'#edf5ff');grad.addColorStop(1,'#dfedff');}ctx.fillStyle=grad;ctx.fillRect(0,0,W,H);const g=ctx.createRadialGradient(1390+Math.sin(t*.17)*80,340,0,1380,380,820);g.addColorStop(0,dark?'#337ad132':'#ffffff');g.addColorStop(1,dark?'#337ad100':'#ffffff00');ctx.fillStyle=g;ctx.fillRect(0,0,W,H);ctx.save();ctx.strokeStyle=dark?'#b5d8ff08':'#5484ba09';ctx.lineWidth=1;for(let x=0;x<W;x+=100){line(x,0,x,H,ctx.strokeStyle);}for(let y=0;y<H;y+=100){line(0,y,W,y,ctx.strokeStyle);}ctx.restore();}
let cues=[],audioBuffer,audioCtx,audioNode,gainNode,audioDestination,audioPrepare,playRequest=0,playPending=false;
let ready=false,playing=false,offset=0,clockStart=0,captionsEnabled=true,muted=false,recording=false,recorder=null;
let activeScene=-1,lastUI=-1,lastFrame=-1,recordStartWall=0,recordPauseStarted=0,recordPauseTotal=0;
function timeNow(){return playing?clamp(audioCtx.currentTime-clockStart,0,DURATION):offset;}
function setStatus(message){$('status').textContent=message;}

function animateStage(index,t,p){
  stages.forEach((g,i)=>g.visible=i===index);const local=t-SCENES[index].start;const entry=ease(local/1.3);updateParticles(t);
  camera.position.set(Math.sin(t*.09)*.16,2.8+Math.cos(t*.12)*.12,19);camera.lookAt(0,.4,0);
  if(index===0){hero.rotation.set(.18+Math.sin(t*.4)*.04,t*.17-.3,-.08);hero.scale.setScalar(1.05+entry*.2);hero.userData.blocks.forEach((m,i)=>{m.position.copy(m.userData.home).multiplyScalar(1+Math.pow(1-entry,2)*1.8);});sourceDocs.forEach((d,i)=>{d.rotation.set(Math.sin(t*.6+i)*.08,.2+i*.15,Math.sin(t*.5+i)*.12);d.position.y=[1.7,.3,-2][i]+Math.sin(t*.8+i)*.15;});halo.rotation.z=t*.08;heroDots.forEach((d,i)=>{const a=t*.55+i/16*Math.PI*2;d.position.set(4.55+Math.cos(a)*2.85,Math.sin(a)*1.85,Math.sin(a)*.8);d.rotation.set(t+i,t*.5,0);});}
  if(index===1){inputStack.rotation.y=Math.sin(local*.4)*.12;inputStack.position.y=.2+Math.sin(local*.6)*.1;goalCubes.forEach((g,i)=>{g.rotation.set(.2,local*.6+i,.1);g.scale.setScalar(.2+ease((local-i*.3)/1.4)*.1);});}
  if(index===2){nodes.forEach((g,i)=>{const hot=Math.floor(local*1.1)%6===i;g.scale.setScalar(hot?1.1:1);g.rotation.y=Math.sin(local*.5+i)*.045;});streamDots.forEach(({mesh:m,from,to,index:i})=>{m.position.lerpVectors(from,to,(local*.45+i*.25)%1);m.position.z=.35;});}
  if(index===3){conversation.rotation.y=Math.sin(local*.35)*.1;talkCards.forEach((g,i)=>{g.position.x=(i%2?.32:-.32)+(1-ease((local-i*.55)/1.4))*(i%2?2:-2);g.position.y=1.8-i*1.1+Math.sin(local*.7+i)*.05;});dialogueOrbit.rotation.z=local*.08;}
  if(index===4){batchGroup.rotation.set(.15,Math.sin(local*.35)*.22-.18,.08);batches.forEach((m,i)=>{m.position.z=Math.sin(local*1.6+i*.4)*.16;m.scale.setScalar(.85+ease((local-i*.08)/2)*.15);});qcRing.rotation.y=.5+Math.sin(local*.3)*.3;savedTicks.forEach((m,i)=>m.scale.setScalar(ease((local-2-i*.5)/1.3)*.9));}
  if(index===5){audienceGroups.forEach((g,i)=>{g.position.y=-.15+Math.sin(local*.8+i*1.5)*.12;g.rotation.y=Math.sin(local*.23+i)*.1;g.scale.setScalar(.85+ease((local-i*.25)/1.3)*.15);});atom.children.filter(a=>a.geometry?.type==='TorusGeometry').forEach((r,i)=>r.rotation.z=local*.25+i*1.1);}
  if(index===6){finalBrand.rotation.set(-.06,Math.sin(local*.45)*.13,.015);finalBrand.position.y=Math.sin(local*.65)*.12;finalBrand.scale.setScalar(.96+entry*.04);finalRing.rotation.z=local*.1;}
}
function overlay(index,t){
  const s=SCENES[index],local=t-s.start,dark=!!s.dark,inkColor=dark?'#edf6ff':'#163154',mutedColor=dark?'#96badf':'#6986aa';
  ctx.save();ctx.globalAlpha=ease(local/.65);brandMark(123,96,22);text('数简立方',164,105,26,inkColor,650);tracked('SHUJIAN CUBE',W-405,101,14,mutedColor,3.5);line(118,141,W-118,141,dark?'#b3d0f21c':'#517faf1a');
  const up=(1-ease(local/.8))*25;
  if(index===5){tracked(s.kicker,120,228+up,18,mutedColor,3.4);text('为每一种创造，准备更好的数据。',120,319+up,66,inkColor,700);text(s.detail,123,380+up,27,mutedColor,400);}
  else{tracked(s.kicker,120,260+up,18,mutedColor,3.3);text(s.title[0],115,392+up,index===6?100:86,inkColor,700);text(s.title[1],115,508+up,index===6?66:86,inkColor,700);text(s.detail,122,580+up,27,mutedColor,400);}
  if(index===0){chip('全流程自动化',122,640);chip('灵活工作流',327,640);text('从你的知识，开始构建。',122,790,25,'#7893b6',400);}
  if(index===1){['CPT','SFT','DPO'].forEach((label,i)=>{const [x,y]=project(2.5+i*2.05,-2.45,1);text(label,x,y,28,'#3977cb',700,'center');});panel(120,658,620,146,()=>{text('一套资料，多种训练目标',146,704,25,'#315a8b',600);text('CPT 预训练语料    SFT 指令数据    DPO 偏好对',146,754,23,'#6684a8',400);});text('也支持 Agent、多轮、CoT 等目标',122,851,23,mutedColor,400);}
  if(index===2){['输入','解析','生成','评审','去重','输出'].forEach((label,i)=>{const [x,y]=project(nodePositions[i][0],nodePositions[i][1]-.7,0);text(label,x,y,20,'#4774aa',500,'center');});let x=120;for(const label of ['CPT','SFT','DPO','Agent','CoT']){x+=chip(label,x,640)+14;}
    const px=1010,py=655;panel(px,py,760,210,()=>{text('生成节点',px+28,py+43,23,'#254c7f',650);chip('模型 · 提示词',px+481,py+18);line(px+28,py+66,px+730,py+66,'#dde9f7');const content='正在依据来源组织样本。\n保留上下文，生成有依据的回答。';const stream=content.slice(0,Math.floor((local%6)*11));const lines=stream.split('\n');lines.forEach((v,i)=>text(v,px+28,py+112+i*37,25,'#51739d',400));if(Math.floor(t*2)%2)text('▎',px+28+ctx.measureText(lines.at(-1)).width,py+112+(lines.length-1)*37,24,'#3d8bef');text('节点运行示意',px+625,py+187,13,'#92a8c1',400);});
    text('模型、上下文与输出上限，都在节点内设置。',122,760,25,mutedColor,400);text('打开节点，观看模型逐步输出。',122,810,25,mutedColor,400);}
  if(index===3){panel(122,647,672,183,()=>{text('风格化生成',151,696,26,'#365987',600);text('可选',626,694,20,'#849cb8',400);rr(693,669,67,35,18,'#568ff1');rr(729,673,27,27,14,'#fff');text('可编辑提示词 · 默认模板一键还原',151,754,24,'#6b87aa',400);text('按上下文组织互动，让对话自然延续。',151,800,23,'#6b87aa',400);});
    const labels=['需求','联想','回应','继续'];labels.forEach((v,i)=>{const [x,y]=project(4.7+(i%2?.32:-.32),1.8-i*1.1,0);chip(v,x+200,y-20,i%2?'#427e9f':'#6886af');});text('知识库检索与来源记录，为样本提供依据。',980,842,23,'#6582a7',400);}
  if(index===4){panel(122,647,640,170,()=>{text('让质量成为流程的一部分',151,694,27,'#e1f1ff',600);['AI 评审 · 可选','去重与结构检查','保存检查点'].forEach((v,i)=>{iconCheck(164,740+i*28,'#68d8c1');text(v,190,747+i*28,22,'#a6c9e5',400);});},true);['检查','保存','交付'].forEach((label,i)=>{const [x,y]=project(2.2+i*2.4,-2.68,.8);text(label,x,y,21,'#9ec5e9',550,'center');});text('不为凑数量而牺牲有效性。',122,867,24,'#88accf',400);}
  if(index===5){const labels=[['高校与研究','领域语料 · 实验数据 · 多轮研究'],['企业与团队','知识沉淀 · 业务问答 · 模型微调'],['爱好者与开发者','个人资料 · 定制风格 · 快速验证']];labels.forEach(([a,b],i)=>{const x=450+i*510;const point=project(-5.4+i*5.4,-1.4,0);text(a,point[0],768,34,'#234977',650,'center');text(b,point[0],818,21,'#6986aa',400,'center');});}
  if(index===6){chip('可视化',122,645);chip('可配置',267,645);chip('可追溯',412,645);text('开源项目 · 开始你的下一次训练',122,777,25,'#5a7da9',550);text('github.com/tianxingstarsky/Super-LLM-distill-Gen',122,827,20,'#7893b6',400);}
  ctx.restore();
  proofShot(t,dark);
  // One concise caption at a time, comfortably inside the video safe area.
  const caption=captionsEnabled?cueAt(cues,t):'';
  if(caption){ctx.font=`500 30px ${FONT}`;const measured=ctx.measureText(caption).width;const maxWidth=1630;let size=30;if(measured>maxWidth)size*=maxWidth/measured;ctx.save();ctx.shadowColor=dark?'#04132499':'#ffffffd9';ctx.shadowBlur=8;ctx.shadowOffsetY=1;text(caption,W/2,990,size,dark?'#f1f7ff':'#23456d',500,'center');ctx.restore();}
  tracked(String(index+1).padStart(2,'0')+' / 07',122,1042,12,dark?'#6c97bd':'#8fa8c5',2.2);text('SHUJIAN CUBE · PRODUCT FILM',W-120,1042,12,dark?'#6c97bd':'#8fa8c5',500,'right');
  // A brief light wipe bridges scenes without covering text for long.
  if(index>0 && local<.38){ctx.fillStyle=`rgba(242,248,255,${(1-clamp(local/.38))*.85})`;ctx.fillRect(0,0,W,H);}
}
function draw(t){if(!playing && !recording && t===0)t=1.6;const index=sceneAt(t);background(t,SCENES[index].dark);animateStage(index,t,(t-SCENES[index].start)/(SCENES[index].end-SCENES[index].start));renderer.render(world,camera);ctx.drawImage(renderer.domElement,0,0);overlay(index,t);canvas.dataset.scene=SCENES[index].name;canvas.dataset.time=t.toFixed(3);if(index!==activeScene){activeScene=index;$('description').textContent=SCENES[index].title.join(' ');document.querySelectorAll('.chapters button').forEach((b,i)=>b.setAttribute('aria-current',String(i===index)));}}
function stopSource(){if(audioNode){try{audioNode.stop();}catch{}audioNode.disconnect();audioNode=null;}}
async function prepareAudio(){
  audioCtx??=new AudioContext();
  if(!audioPrepare)audioPrepare=(async()=>{
    if(!audioBuffer){const result=await fetch('audio/master.mp3');if(!result.ok)throw new Error('旁白文件未载入');
      const buffer=await audioCtx.decodeAudioData(await result.arrayBuffer());
      gainNode=audioCtx.createGain();gainNode.gain.value=muted?0:1;gainNode.connect(audioCtx.destination);
      audioDestination=audioCtx.createMediaStreamDestination();gainNode.connect(audioDestination);audioBuffer=buffer;}
    await audioCtx.resume();
  })();
  try{await audioPrepare;}finally{audioPrepare=null;}
}
async function play(from=offset){if(!ready)return false;const request=++playRequest;playPending=true;try{await prepareAudio();if(request!==playRequest)return false;playPending=false;stopSource();offset=clamp(from,0,DURATION);if(offset>=DURATION)offset=0;audioNode=audioCtx.createBufferSource();audioNode.buffer=audioBuffer;audioNode.connect(gainNode);clockStart=audioCtx.currentTime-offset;audioNode.start(0,offset);playing=true;$('start').hidden=true;$('play').textContent='Ⅱ';$('play').setAttribute('aria-label','暂停');setStatus(recording?'正在录制 1080p 影片，请保持此页在前台。':'中文旁白 · 原创配乐 · 中文字幕');return true;}catch(e){if(request===playRequest){playPending=false;setStatus('声音播放失败：'+e.message);}return false;}}
function pause(){++playRequest;playPending=false;if(playing)offset=timeNow();playing=false;stopSource();$('play').textContent='▶';$('play').setAttribute('aria-label','播放');}
function seek(t){if(recording)return;const resume=playing||playPending;pause();offset=clamp(t,0,DURATION);$('start').hidden=offset>0;draw(offset);updateUI(offset);if(resume && offset<DURATION)void play(offset);}
function stamp(t){return `${String(Math.floor(t/60)).padStart(2,'0')}:${String(Math.floor(t%60)).padStart(2,'0')}`;}
function updateUI(t){$('seek').value=t;$('time').value=stamp(t)+' / 01:00';}
function tickFrame(){const t=timeNow();if(playing || lastFrame!==t){draw(t);lastFrame=t;}if(Math.floor(t*5)!==lastUI){lastUI=Math.floor(t*5);updateUI(t);}if(playing&&t>=DURATION){pause();offset=DURATION;draw(DURATION);updateUI(DURATION);if(recording)recorder.stop();else setStatus('影片播放完成。可以选择章节或从头播放。');}requestAnimationFrame(tickFrame);}
function setLocked(locked){['start','play','replay','seek','mute','captions','record'].forEach(id=>$(id).disabled=locked);document.querySelectorAll('.chapters button').forEach(b=>b.disabled=locked);}

async function recordFilm(){
  if(recording)return;
  if(!window.MediaRecorder || !canvas.captureStream){setStatus('此浏览器不支持录制。请使用已导出的 MP4。');return;}
  recording=true;setLocked(true);let recordStream;
  try{await prepareAudio();pause();offset=0;captionsEnabled=true;muted=false;gainNode.gain.value=1;$('captions').textContent='字幕 开';$('mute').textContent='声音 开';draw(0);
    const mime=['video/webm;codecs=vp9,opus','video/webm;codecs=vp8,opus','video/webm'].find(v=>MediaRecorder.isTypeSupported(v));if(!mime)throw new Error('没有可用的 WebM 编码器');
    const stream=recordStream=canvas.captureStream(30);audioDestination.stream.getAudioTracks().forEach(track=>stream.addTrack(track));
    recorder=new MediaRecorder(stream,{mimeType:mime,videoBitsPerSecond:10000000,audioBitsPerSecond:192000});const parts=[];
    recorder.ondataavailable=e=>{if(e.data.size)parts.push(e.data);};
    recorder.onstop=async()=>{stream.getVideoTracks().forEach(track=>track.stop());recording=false;setLocked(false);$('record').textContent='录制 WebM';setStatus('正在保存影片。');
      const blob=new Blob(parts,{type:'video/webm'});
      try{const res=await fetch('/__recording',{method:'POST',headers:{'Content-Type':'video/webm'},body:blob});if(!res.ok)throw new Error('local save unavailable');const info=await res.json();if(!info.saved)throw new Error('save failed');setStatus('已保存 1080p WebM。影片包含旁白、配乐和字幕。');canvas.dataset.recording='saved';}
      catch{const link=document.createElement('a');link.href=URL.createObjectURL(blob);link.download='shujian-cube-promo.webm';link.click();setTimeout(()=>URL.revokeObjectURL(link.href),30000);setStatus('WebM 已导出到浏览器下载目录。');canvas.dataset.recording='downloaded';}
    };
    recording=true;recordStartWall=performance.now();recordPauseTotal=0;canvas.dataset.recording='recording';setLocked(true);$('record').textContent='录制中';recorder.start(1000);if(!await play(0))throw new Error('声音未能开始播放');
  }catch(error){recordStream?.getVideoTracks().forEach(track=>track.stop());if(recorder){recorder.onstop=null;if(recorder.state!=='inactive')recorder.stop();}pause();recording=false;canvas.dataset.recording='failed';setLocked(false);$('record').textContent='录制 WebM';setStatus('录制失败：'+error.message);}
}
$('start').addEventListener('click',()=>void play());$('play').addEventListener('click',()=>playing?pause():void play());$('replay').addEventListener('click',()=>void play(0));$('seek').addEventListener('input',e=>seek(Number(e.target.value)));
document.querySelectorAll('.chapters button').forEach(b=>b.addEventListener('click',()=>{void play(Number(b.dataset.time));}));
$('mute').addEventListener('click',()=>{muted=!muted;if(gainNode)gainNode.gain.value=muted?0:1;$('mute').textContent=muted?'声音 关':'声音 开';$('mute').setAttribute('aria-label',muted?'开启声音':'静音');$('mute').setAttribute('aria-pressed',String(muted));});
$('captions').addEventListener('click',()=>{captionsEnabled=!captionsEnabled;$('captions').textContent=captionsEnabled?'字幕 开':'字幕 关';$('captions').setAttribute('aria-label',captionsEnabled?'关闭字幕':'开启字幕');lastFrame=-1;});
$('fullscreen').addEventListener('click',()=>document.fullscreenElement?document.exitFullscreen():document.querySelector('.screen').requestFullscreen());$('record').addEventListener('click',()=>void recordFilm());
document.addEventListener('keydown',e=>{if(e.code==='Space' && !['INPUT','BUTTON','A'].includes(document.activeElement.tagName)){e.preventDefault();if(!recording)playing?pause():void play();}});
document.addEventListener('visibilitychange',()=>{if(document.hidden && playing){if(recording){recorder.pause();recordPauseStarted=performance.now();pause();setStatus('录制已暂停，回到此页后继续。');}else{pause();setStatus('影片已暂停。回到页面后可继续播放。');}}else if(!document.hidden && recording && recorder.state==='paused'){recordPauseTotal+=performance.now()-recordPauseStarted;recorder.resume();void play(offset);}});
window.addEventListener('pagehide',()=>{stopSource();renderer.dispose();});

async function initialize(){
  try{setLocked(true);const res=await fetch('audio/cues.json');if(!res.ok)throw new Error('字幕文件未载入');cues=await res.json();await document.fonts.ready;await document.fonts.load('700 86px Promo');
    // Decode only after the user's gesture. Preflight media separately to show
    // missing files rather than silently delivering a film without its sound.
    const media=await fetch('audio/master.mp3',{method:'HEAD'});if(!media.ok)throw new Error('声音文件未载入');
    await Promise.all([loadImage('brand','brand-mark.png'),loadImage('home','shots/home.png'),loadImage('model','shots/node-model.png'),loadImage('director','shots/director.png'),loadImage('library','shots/data-library.png'),loadImage('readme','shots/readme.png')]);
    const brandTexture=new THREE.Texture(images.get('brand'));brandTexture.needsUpdate=true;brandTexture.colorSpace=THREE.SRGBColorSpace;
    mesh(finalBrand,new THREE.PlaneGeometry(8.7,8.7),new THREE.MeshBasicMaterial({map:brandTexture,transparent:true,depthWrite:false,side:THREE.DoubleSide,toneMapped:false}));
    ready=true;draw(0);setLocked(false);$('loading').hidden=true;$('start').disabled=false;setStatus('60 秒 · 1080p · 中文旁白与原创配乐。含真实界面与 README 文档预览。');
    const video=await fetch('shujian-cube-promo.mp4',{method:'HEAD'});if(video.ok)$('mp4').hidden=false;
    requestAnimationFrame(tickFrame);
  }catch(e){$('loading').querySelector('strong').textContent='影片暂未准备好';$('loading').querySelector('span:last-child').textContent=e.message;setStatus(e.message);console.error(e);}
}
void initialize();
