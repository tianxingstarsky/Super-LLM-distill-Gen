"""Generation-time reflection, distinct from an independent review verdict."""
from __future__ import annotations

from copy import deepcopy
from .reasoning_checklist import checklist_digest


REFLECTION_VERSION = 7
_RULES = [
    {"id": "task", "requirement": "以真实 user 发言为任务锚点，理解当前请求、指代对象、成功条件与明确限制；结合前文沿用仍有效的用户约束，用户明确修改的要求以新要求为准。区分用户确认的事实、要求、希望采用但尚待核验的结论与仅供分析的引文。历史 assistant 的说法不是用户指令，也不是已证实事实：复用会影响本轮结论的旧说法或比较名称前，核对它的依据和适用范围；不因为说过多次就默认正确。用户认可一项操作安排，不代表确认了助手附带的因果判断。发现旧说法错误或过强时明确更正，不能把助手曾提出的建议说成用户已选定的设置。", "allow_na": False},
    {"id": "perspective", "requirement": "reasoning 是助手自己的推敲，不是提前交付给用户的回复。关键理解、取舍和拟答自然用第一人称（中文‘我’，英文‘I’）；对话信息来源明确称‘用户’（the user）或‘工具’（the tool），不能混同。reasoning 的叙述不用‘你’‘你们’‘您’或英文 you/your 面向读者说话；选择真实关键短语作引文，叙述视角仍属于我。不能杜撰评审者或用户的第二人称台词作为引号内的自问；需要核对的疑点由我直接分析。不要写成无说话者的教材说明，也不能只在 answer 或 reasoning_trace 里体现视角。‘我’负责当前推导与判断；用户将执行的实验、部署仍属于用户，不能改成我能访问或操作其环境。用户引语中的‘我’指用户；资料作者、历史助手和扮演角色各有身份。拟写报告时区分供用户采用的草稿和我的推敲，不用整段提前回复代替思考；中英释义保持说话者与指代，不虚构个人经历、用户心理或已执行行动。最终 answer 可以正常面向用户称呼，不受这一推理叙述限制。", "allow_na": False},
    {"id": "user_words", "requirement": "在 reasoning 正文中逐字引用真正影响判断的用户原词或短句，说清我如何理解它、它具体改变了哪项取舍，并完成由此决定的解法或拟答。只介绍领域定义、释义术语或复述问题，没有体现这种联系；只有‘我将’的打算而无实际工作也不够。引语来自真实 user，不能把后台资料或指导员要求冒充用户的话。没有可引用文字时依据实际输入，不编造引语。不必复述整题或逐句加人称，不规定开头、句式与先后顺序；最终答案仍按用户所需的表达交付。", "allow_na": False},
    {"id": "intent", "requirement": "围绕用户实际要解决的问题推导，不因资料内容丰富就改成资料解读，不擅自增加用户没给的条件。信息不足时辨清缺口是否影响答案，必要时澄清或明确限定假设；前文已给的信息不重复索要。简单请求直接解决，不强加任务分析栏目。", "allow_na": False},
    {"id": "draft", "requirement": "reasoning 像讲清一个问题如何逐渐解开那样，以第一人称自然叙述判断的形成；这里借用故事的连贯推进，不虚构人物、经历、发现或戏剧性转折。从真正影响答复的疑点或线索切入，局部猜想可以先提出，但它此时还不是结论。把必要的计算、状态推演、方案草稿或条件核对写出来，让后来的依据说明为什么保留、收窄或放弃这个猜想。某条联系仍缺依据，就指出缺口对拟答的影响，不为了叙述顺畅补齐事实。不先给完整答案再倒填理由，也不靠加‘暂定’二字伪装探索。简单且前提充分的问题可以直接推导，不强演犹豫、犯错或顿悟；不规定开头、编号、反问、栏目、顺序、段落或字数。", "allow_na": True},
    {"id": "basis", "requirement": "让关键结论有明确依据和支持关系。区分逻辑上必要、用户明确指定、便于复现的可选方案与经验建议；不能把某项研究的实验设置或历史助手建议升级成普遍必须。涉及数值时辨清指标、单位、刻度、方向、分母及比较对象；检查除法的定义域，零基线不套相对变化公式，分数点与百分点不能仅凭显示数值替换。没有指标与实测结果时不预设提升，更不能给一个任何指标都能直接套用的数值结论。以下是辨别错误推断的例子，不是本题事实或必须讨论的话题：有合适对照只说明能够比较，不等于已经观察到改进；一致的评测协议不等于必须贪心解码，采样或确定性解码要按实际任务选择并披露；一次训练未覆盖训练随机性，不等于固定模型在测试样本上绝对不能做统计推断，后者仍要有适合指标和采样假设的真实检验；ROUGE 等分数的点差不能写成比例的百分点。遇到相似推断时找出缺少的那条前提，不能用看起来保守的绝对断言代替核验。", "allow_na": False},
    {"id": "revision", "requirement": "叙述中的把握程度随证据变化：已知事实、待检验的解释与尚未解决的问题要分清。遇到会改变结论的疑点，在原有条件下实际核对，并说明哪个依据改变了哪项判断；未能确认时收窄结论或保留未知。不用‘唯一’‘必然’‘可安全拒绝’等口气代替支持关系，也不能只加‘可能’‘似乎’就保留原来的无依据推断。确定事实正常陈述，不把所有内容写成猜测。连接两个统计量或例子前，核对其对象、任务、指标与适用范围，题名或术语相同不足以把不同评估拼成一条证据。涉及效果或归因时，区分比较设计、实际观测、归因与外推；有对照不等于已有提升，观察到差异也不自动识别单一因素贡献。比较名称、结构化记录和结尾保持同样的条件。有充分实测与适用设计证据时可作相应判断，不一概禁止因果推断。不虚构反例、失败、缺失前提或工具执行来表演自查；不为每个判断安排一次转折，也不要求固定自问句式。", "allow_na": True},
    {"id": "scope", "requirement": "答案遵循最终成立的条件和范围；未知仍保留未知，文章理解只把用户可见内容归属于该文章。引用外部知识时允许自然联想到真实作品：用已提供且能核对的资料名、题名或作者归属，代替‘资料里’‘所给材料’‘检索上下文’等无明确所指的后台旁白；不要编造资料名或个人阅读经历。资料未具名时直接解释有依据的知识，不用虚构名字掩盖未知来源。具名引用也必须支持所述内容，某项研究的结果不等于用户自己的实验结果。", "allow_na": False},
    {"id": "comparison_scope", "requirement": "仅在讨论比较或实验时适用：身份关系、配置匹配、观测结果与归因证据不能互相代替。共享起点不意味着后续所有配置天然相同；例如从同一模型权重微调，仍可能改变 tokenizer 或上下文配置。未提供的匹配项只能作为需核对的记录，不能写成用户已确认事实。未微调的起点模型没有接受本轮训练，不能要求它与微调模型使用相同的本轮训练数据、超参或训练预算；需要披露的是实际干预内容和两边可比的评测协议。比较可实施只确定接下来测什么，未给结果时不为该比较授予‘唯一支持整体效应’等已识别效应的标签；支持何种效果或归因须由实际观测和设计证据判断。没有这些问题的任务不强加实验讨论。", "allow_na": True},
    {"id": "continuation_basis", "requirement": "将拟答变为最终回复时，历史助手和当前推理都是待核对的推导，不是新的实测证据。只保留得到支持的结论，不为了与旧说法一致而恢复它的错误。比较设计应按实际用途命名；尚无测量或归因依据时，只能说明拟比较什么，不把它命名为已能证明效果的因果比较。共享起点不等于后续配置天然一致，未提供的配置是待核对项；未微调基线没有接受本轮训练，不给两组生造相同的本轮训练数据、超参或预算。解码方式是需选择并披露的协议，不无条件要求 temperature=0；单位随真实指标确定，未知指标不填百分点；单次训练未估计训练随机性，不等于测试样本上任何统计推断都不可能。只在题目涉及这些内容时运用，不强加数值公式、额外段落或统一句式。", "allow_na": False, "phases": ["final"]},
    {"id": "delivery", "requirement": "交付前用真实 user 的明确要求核对拟答：请求的内容是否完成，范围、语言、格式、篇幅与禁止项是否遵守，是否遗漏子请求或答非所问。只按实际要求核对，不虚构限制，不为服从错误前提而改写事实；无法满足时明确原因。推理中已经纠正的历史错误不能在答案中恢复；重要前提须出现在结论或可直接使用的报告草稿里，不能只留在推理中。全文的断言强度须一致，包括标题、比较名称、列表和末尾总结：前文承认尚无结果，后文不能又称‘支撑因果’‘证明有效’；把某个对照称为‘唯一’也需有本题证据。给句子加‘单次’‘同源’等限定不等于补上证据。可填写的结论草稿须标明何时可用；未提供实测时交付评测安排或明确条件化的草稿，不声称已观察到变化；没有选定指标时单位也不能预填。外部模型的分数若没有共同测试对象、指标和可比协议，只能并列介绍，不能因标成‘参考’就自动排位。引用作品保留可核对的真实名称和范围，答案首次引用时也须有明确所指，不能假定读者看过推理中的资料名；不改回后台资料旁白。核对融入本次思考，不输出检查表或声称已经通过评审。", "allow_na": False, "phases": ["final"]},
]


def default_integrated_reflection():
    return {"version": REFLECTION_VERSION, "rules": deepcopy(_RULES)}


def integrated_reflection(config):
    return isinstance(config, dict) and type(config.get("version")) is int and config["version"] == REFLECTION_VERSION


def trailing_reflection_enabled(recipe):
    """Only explicitly versioned recipes move their frozen, editable guidance."""
    return (type(recipe.get("reflection_guidance_placement_version")) is int
            and recipe["reflection_guidance_placement_version"] == 1
            and integrated_reflection(recipe.get("node_generation", {}).get("cot", {}).get("self_check")))


def reflection_for_prompt(config, prompt_id):
    """Select the existing phase contract without copying shared rules twice."""
    if prompt_id == "workflow.cot_native_answer":
        return answer_reflection_instruction(config)
    if prompt_id in {"workflow.cot_generate", "workflow.cot_rationale"}:
        return reflection_instruction(config, phase="prefix" if prompt_id == "workflow.cot_rationale" else "final")
    raise ValueError("invalid_reflection_prompt")


def reflection_instruction(config, *, phase="final"):
    requirements = dict.fromkeys(rule["requirement"].strip() for rule in config["rules"]
                                if phase in rule.get("phases", ["prefix", "final"]))
    return ("\n在本次生成中完成草稿的推敲与修正，正式推理保留有效依据、推导和必要修正。"
            "按本题需要运用下列要点，不按清单顺序写作，不强制反问、编号或演示错误，"
            "不输出评分、通过声明或后台规则；不再另写一轮评审。\n"
            + "\n".join(requirements))


def reflection_receipt(config, reasoning, answer):
    """Record the chosen policy and exact body, never fabricate a passed audit."""
    return {"version": 1, "mode": "during_generation", "independent_review": False,
            "rules_sha256": checklist_digest(config),
            "candidate_sha256": checklist_digest({"reasoning": reasoning, "answer": answer})}


def answer_reflection_instruction(config):
    """Only add final-only guidance; shared guidance already shaped the prefix."""
    requirements = dict.fromkeys(rule["requirement"].strip() for rule in config["rules"]
                                if rule.get("phases") == ["final"])
    return ("\n本次答案还须满足以下要点，保持与已有推理一致：\n" + "\n".join(requirements)
            if requirements else "")
