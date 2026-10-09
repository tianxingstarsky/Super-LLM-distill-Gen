export const DURATION = 60;
export const SCENES = [
  {start:0,end:7,kicker:'KNOWLEDGE → TRAINING DATA',title:['让好资料，','变成好数据。'],detail:'数简立方 · 全流程训练数据工作台',name:'知识成为数据'},
  {start:7,end:15,kicker:'BRING YOUR KNOWLEDGE',title:['资料进来。','目标，由你决定。'],detail:'文档 · 智能体上下文 · 开放需求',name:'资料与目标'},
  {start:15,end:25,kicker:'VISIBLE. FLEXIBLE. YOURS.',title:['看得见过程，','随时调整每一步。'],detail:'在节点上配置模型、提示词与生成策略',name:'可见的工作流'},
  {start:25,end:35,kicker:'BEYOND QUESTION & ANSWER',title:['更自然的对话，','更贴近你的需求。'],detail:'风格化生成 · 对话指导员 · 连续多轮',name:'风格与多轮'},
  {start:35,end:45,kicker:'QUALITY THROUGH THE PROCESS',title:['生成之后，','还有质量检查。'],detail:'生成 · 评审 · 去重 · 打包 · 从检查点继续',name:'质量与续跑',dark:true},
  {start:45,end:54,kicker:'BUILT FOR EVERY CREATOR',title:['不同的创造者，','同样需要好数据。'],detail:'从小批验证，到可追溯的数据生产',name:'每一种创造者'},
  {start:54,end:60,kicker:'SHUJIAN CUBE',title:['数简立方','让数据，简单生成。'],detail:'你的资料。你的流程。你的下一次训练。',name:'数简立方'},
];
export function clamp(value,min=0,max=1){return Math.min(max,Math.max(min,value));}
export function sceneAt(seconds){const t=clamp(seconds,0,DURATION);return SCENES.findIndex(s=>t<s.end) < 0 ? SCENES.length-1 : SCENES.findIndex(s=>t<s.end);}
export function cueAt(cues,t){return cues.find(c=>t>=c.start && t<c.end)?.text || '';}
export function ease(value){const x=clamp(value);return 1-Math.pow(1-x,3);}
