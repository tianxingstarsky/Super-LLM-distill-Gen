"""Evidence-policy revision, independently frozen from the historical template."""
from .workflow_semantic_review import SEMANTIC_REVIEW

EVIDENCE_REVIEW = SEMANTIC_REVIEW + '''

证据范围核查补充：
source_inventory 明确当前覆盖范围，all_text_pages 只表示该导入文本覆盖列出的页，不保证图表/公式识别正确。unknown/selected_ranges 不能支持“整篇论文没有……”；即使有全部文本，关键词无命中也不是不存在的证明。先看实际方法、过滤策略、实验设定和附录的有关段落。提供的正文足够时直接引用，勿为了完成工具动作重复检索。

务必单独完成 evidence_scope、learner_surface、answer_language 三项适用核对。文章阅读任务只把用户真正给出的段落称为“这段”；教师额外来源不得冒充用户可见内容。知识应用任务直接解释事实和关系，不写“资料也明确说”“从材料可以支持”等后台旁白。公开研究事实可准确归属，但不从个别设置推导通用阈值。检查英文用户收到的最终答案是否英文，不能把双语思考当作答案语言豁免。推理可中英混合，不要求全文翻译两遍。

前文助手可能含错，不是可引用的独立事实。当前回答如依赖前文的单位、因果或限定，须用真实证据重核并明确纠正；只评最后一段不能证明整段对话合格。

environment.verified_prefix 若存在，已由程序核对同一任务、资料、规则和原样推理。复用其中已完成的推理检查与计算，不重新搜索同一证据。仍阅读完整新增答案，核验答案与推理是否一致、答案语言、来源归属、条件和单位；新答案揭示旧推理问题时必须重新提出。新缺陷不能被旧通过覆盖。两份最终评审仍独立，不能照抄另一个当前评审。

一次 submit_semantic_review 可完成全部核查；允许并行发出真正需要的读取/计算调用。引用已提供的段落 ID 可避免反复抄录出错。请勿用原稿自己的事实断言驳回 fact/provenance 类型的指控，至少给出外部来源或真实用户限定的原句；证据不足就保留未决。'''
