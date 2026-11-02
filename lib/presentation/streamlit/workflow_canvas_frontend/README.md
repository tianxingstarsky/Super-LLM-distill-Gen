# 工作流画布组件

`index.html` 是由 `workflow_canvas.py` 装载的桌面 Streamlit 组件。Python 提供实际执行阶段、数据依赖、布局与只读状态；前端负责绘制、键盘导航、缩放、平移，以及将节点选择送回原有 Streamlit 配置窗口。组件不发起模型请求，不改变执行顺序或冻结任务。

`qa_routes` 展示指导员中已保存的知识问答／资料问答穿插策略。两条路线合流到原来的 SFT、多轮生成节点，不创建额外模型节点、完成计数或并行执行状态。路线按钮仍选择真实指导员节点；没有新版混合策略的历史配方保留原图。概率是大致触发机会，不代表必须凑出的数量。

运行态的评分修正分支使用 `feedback_branch`、`control_nodes` 与 `control_edges`，职责仍属于原审核节点，不向生成器回灌错误结果。`spec.nodes` 只包含真实阶段，虚拟路线不进入阶段选择列表或模型绑定。

生产组件只使用浏览器原生 DOM、SVG 与 Streamlit 消息桥，无额外前端运行时依赖。验证入口是 `tests/test_workflow_canvas.py` 与已有 `tests/workflow_canvas_harness.mjs`；后者在本地使用 Node/jsdom 核对真实脚本的交互和布局。实际页面还需桌面浏览器截图检查，不开展手机验收。
