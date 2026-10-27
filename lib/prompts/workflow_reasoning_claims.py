"""Claim-by-claim evidence and typed arithmetic; legacy self-check remains frozen."""
from .workflow_reasoning_checklist import CHECKLIST_CONTRACT

CLAIM_CONTRACT = '''
本版本以“每个具体主张”而非找到一处正确内容来核验。先完整读原问题、所有正文和可见来源，检查每条规则；再逐条检查 claim_anchors。anchors 是程序定位的数字/强结论语句，不是正确答案，也不能替代完整阅读。
每个 anchor_id 必须出现一次。一个语句含多个主张时，reason 必须分别说明，每个可算的差值、倍率或增幅都列入 calculations。不能只检查其中最容易的一项。正文其它地方发现错误也必须反映在对应 checks 中。
status：supported 有足够依据；unsupported 有具体错误或不被前提支持；uncertain 当前材料无法判定；not_claim 仅用于序号、版本号、纯引用/被明确否定的命题等非作者断言，须具体说明。不能把难以判断的真实主张当 not_claim。uncertain 不自动放行。
evidence 引用 source_passages 的 id，或 reasoning/answer，quote 必须逐字存在。候选自身引文只能定位主张，不能充当外部事实证明；来源未覆盖的内容不得自行补全。历史助手说法也不是独立事实依据。禁止用检查表或自评分作证。只报来源范围内缺少证据，不把材料缺失推成世界中不存在。
单位核验先确认指标的语义、量表、分子分母、基线和比较行。0–100 展示的分数并不自动是百分比；ROUGE 等得分的点差应标 score_point，不因数值在 0–100 就标 percentage_point。比例从 6.8% 到 39.9% 才是 33.1 个百分点；得分 6.8 到 39.9 是 33.1 分数点。总倍率 value/baseline 与增量倍率 (value-baseline)/baseline 不同；相对增幅还要乘 100。不要让这些教学数字代替实际任务操作数。
对单位/比例/倍率的可复算主张必须给 calculations，不准以“已人工核对”跳过。每项字段为 operation(difference|ratio|relative_change), baseline, value, reported（十进制字符串）, reported_quote（当前 anchor 内包含 reported 数字及原单位的逐字短引文，不能改写成正确单位）, metric_kind(score|proportion|quantity), operand_scale(native|percent|fraction), reported_unit(score_point|percentage_point|percent|ratio|native)。全部值必须来自所引正文或来源。difference 的 value-baseline；ratio 的 value/baseline；relative_change 的 (value-baseline)/baseline*100。比例 fraction 的 difference 输出百分点需乘100。不得倒置基线来挽救原文。定性、单独计数/日期、无差值断言可 calculations=[]，但 no_calculation_reason 必须解释。
过强结论核验：写出原文确实陈述的前提，检查否定、量词、因果、适用人群/指标/实验范围。counterexample 必须讨论能否构造“前提成立而结论不成立”的具体情况，不能只写无反例。构造反例不能改掉原前提；找不到反例也不等于证明成立。条件不够就判 unsupported 或 uncertain，不准把评审新添的条件算成作者已说过。区分“移出报告”和“未做实验”，“单次训练不能估计训练方差”和“固定两模型不能做配对显著性检验”。只报某条基线可能失去对比信息，但不必然高估，其他对比差值可能更大。
全局 checks 与具体 claims 不得矛盾；任一未支持主张必须在对应规则标 unmet。不要用另一个已经写对的句子掩盖当前错误，不要求没有发生的工具执行或为了证明自查而虚构错误。独立检查员看不到作者结论，重新核查相同输入。
仅返回 {"checks":[原 checks 合同的项目],"claims":[{"anchor_id":"原 id","status":"supported|unsupported|uncertain|not_claim","evidence":[{"source":"source_passages id 或 reasoning 或 answer","quote":"逐字引文"}],"reason":"逐个主张的支持/缺口与单位语义","counterexample":"范围检验及具体反例或充分条件；纯数值非范围项可空","calculations":[],"no_calculation_reason":"无可复算断言时说明"}]}。不改正文，不把审计作为训练正文。
'''

CLAIM_SELF_CHECK = "你是 CoT 作者的逐项自检员，诚实寻找当前稿件的错误。\n" + CHECKLIST_CONTRACT.split("仅返回")[0] + CLAIM_CONTRACT
CLAIM_REVIEW = "你是独立的主张核查员，没有作者的通过意见；不得替原稿补前提。\n" + CHECKLIST_CONTRACT.split("仅返回")[0] + CLAIM_CONTRACT

CLAIM_SCHEMA = '''
输出合同补全（字段必须齐全，不能用 id 替代 rule_id）：
checks 每项必须且仅有 rule_id、status、evidence、reason。rule_id 为 rules 中原 id。
status 为 met/unmet/not_applicable；evidence 为 [{"field":"reasoning 或 answer","quote":"该字段中逐字原句"}]。
met 的 evidence 不能为空；其它状态也必须有 evidence 字段，没有可引用的原句时用 []。reason 是非空的具体说明。
顶层必须且仅有 checks 和 claims。claims 与 calculations 按前述完整字段返回，不得省掉值为空的 counterexample、no_calculation_reason 或 calculations。
示例结构（只是字段，不能照抄内容）：
{"checks":[{"rule_id":"原规则id","status":"met","evidence":[{"field":"reasoning","quote":"原句"}],"reason":"具体依据"}],"claims":[{"anchor_id":"原定位id","status":"supported","evidence":[{"source":"source.text","quote":"来源原句"}],"reason":"当前主张的依据","counterexample":"合法条件或具体反例检验","calculations":[],"no_calculation_reason":"当前句不是可复算的比较"}]}。
'''

CLAIM_PROTOCOL_V12 = '''
分批与格式修复约定：audit_partition 存在时，每批仍须阅读完整正文与来源，但只返回本批 claim_anchors 的逐项结果；checks 只对应本批 rules，rules=[] 时必须返回 checks=[]。程序在所有批次结束后核对完整覆盖。
新增计算类型 percent_of：value / baseline * 100，reported_unit 必须是 percent；它是“部分占总量的百分比”，不等于 relative_change 的增幅。baseline、value、reported 必须是 JSON 字符串且仅含十进制数，不带千分位逗号、百分号、分数、乘除式。源句和 reported_quote 保留原字面表示，数值字段去掉分隔符。比如计算对象为人数占比，metric_kind=quantity，operand_scale=native；数值结果的 reported_unit=percent。差值、倍率、占比和增幅不能混淆。
assessment_repair 若存在，上一份评审是你自己的格式/逐字证据缺陷记录，不是正确结论；重新核对原稿，只修正评审合同，禁止改写 candidate、补造来源或默认支持原判断。引文必须从当前提供的正文逐字复制，禁止凭记忆改写；找不到支持就明确 unsupported/uncertain，并同步对应规则 unmet。返回完整本批 checks/claims，程序仍执行原有严格验证。不要无意义复述整份来源；每个主张使用足够短、能实际支持它的逐字证据。
'''

def claim_protocol_v12(body):
    # Do not leak the validation paper's numerical answer into the judge.
    start = body.index('单位核验先确认')
    end = body.index('对单位/比例/倍率', start)
    unit_text = ('单位核验先确认指标语义、量表、分子分母、基线和比较行。分数的点差并不自动是百分点；'
                 '按指标报告约定区分 score_point 与比例百分点 percentage_point，不凭数值范围猜测。'
                 '总倍率 value/baseline 与增量倍率 (value-baseline)/baseline 不同；相对增幅还要乘100。\n')
    return body[:start] + unit_text + body[end:] + CLAIM_SCHEMA + CLAIM_PROTOCOL_V12
