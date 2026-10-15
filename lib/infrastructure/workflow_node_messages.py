"""Keep configured node instructions separate from source and task data."""
from __future__ import annotations


def _learning_rules(data, config):
    """Only finite, configured controls become rules; source prose never does."""
    if not {"learning_goal", "user_languages"}.intersection(config):
        return []
    context = data.get("context") if isinstance(data.get("context"), dict) else data
    fields = {"qa_contract", "question_brief", "assigned_types", "learning_goal", "user_language"}
    if not fields.intersection(context) and not fields.intersection(data):
        # CPT cleanup, parsing and unrelated node calls must not receive QA
        # instructions merely because their workflow also has a director.
        return []
    contract = context.get("qa_contract") or data.get("qa_contract") or {}
    contract = contract if isinstance(contract, dict) else {}
    candidates = context.get("candidates", [])
    candidates = candidates if isinstance(candidates, list) else []
    manual = [item for item in candidates if isinstance(item, dict) and item.get("preserve_human_input")]
    manual_rule = "已有人工问题、资料、角色与语言保持原样；自动用途和语言设置只指导新设计，不翻译或改写固定人工输入。"
    if contract.get("human_design") or candidates and len(manual) == len(candidates):
        return [manual_rule]
    goal = contract.get("learning_goal", config.get("learning_goal"))
    rules = []
    if manual:
        rules.append(manual_rule + "以下自动用途与语言要求仅适用于automatic_controls_applied=true的候选，不覆盖preserve_human_input=true的候选。")
    if goal == "knowledge_application":
        rules.append("本次训练用途是运用已学知识：生成可独立理解的直接请求或实际问题，不在训练用户输入中附加教师原文。"
            "教师私有资料用于核验必要事实，允许助手直接运用这些知识；不要求用户先提供答案中的知识事实。"
            "真实用户或工具上下文没有公开文章时，最终答案与显式推理都直接运用相关知识，说明完成当前任务所需的事实、关系与条件。"
            "不要写成本次‘根据资料’‘材料指出’‘原文说’或‘the source states’的读后转述；"
            "不得把自己查看后台私有资料、接受知识注入或遵循来源包装作为回答的依据或推导步骤。"
            "私有资料的读取、选区、核验和派发过程不是训练对话内容；生成和评审都要检查实际答案及全部显式推理中的这类自述。"
            "这不禁止必要的公开事实归属：可以准确说明命名论文的作者提出了什么方法、在哪组实际实验中得到什么结果，"
            "但不能把作者的发现、操作和经历说成用户或助手亲自完成。"
            "若真实用户后来提供文章、询问某篇公开论文或要求出处，可以按实际任务引用和解释所需内容；"
            "只有确实出现在真实用户或工具上下文中的文章，才能称为用户提供的文章。"
            "不得假称用户给过隐藏文章或经历。保留事实归属、条件与不确定性；真实缺少用户条件时可以澄清或条件作答。"
            "不为了方便核验而退回‘阅读下面资料’任务，不声称学生已完成训练或已掌握知识。")
    elif goal == "document_reading":
        rules.append("本次训练用途是阅读用户提供的资料：实际训练输入必须包含所需原文，回答遵循用户真实任务与证据范围；"
            "教师私有资料不能冒充用户已看过的上下文。")
    elif goal == "mixed":
        rules.append("本次兼顾知识运用与资料阅读：按每项任务的实际qa_type和可见上下文设计，"
            "知识运用不附教师原文，资料阅读给出必要原文；两种用途都允许有价值的连续协作，不机械凑题。")
    languages = config.get("user_languages", [])
    brief = context.get("question_brief") or data.get("question_brief") or {}
    brief = brief if isinstance(brief, dict) else {}
    selected = contract.get("user_language", context.get("user_language", brief.get("user_language")))
    if isinstance(selected, str) and selected in {"zh", "en"} and selected in languages:
        name = "中文" if selected == "zh" else "英文"
        rules.append(f"本段对话用户主要使用{name}，后续用户发言保持同一种主要语言；助手最终回答跟随用户语言。"
            "保留真实引文、代码、专名与必要术语，不逐句双语翻译或机械插入另一语言。显式推理语言单独遵循generation_style，"
            "可按配置中英混合；不能因推理与答案语言不同而直接判错。")
    elif languages:
        rules.append("按每项候选的user_language安排整段用户主要语言（zh中文、en英文），不是把每句话写成双语。"
            "助手答案跟随实际用户语言，显式推理可采用节点独立指定的语言。已有人工原文优先。")
    return rules


def split_node_rules(data, *, instruction=None, qa_director=None):
    """Return a stable system suffix and an unmodified-content task projection.

    Only the explicit workflow instruction argument and rules matched against
    the immutable recipe are trusted. Never infer instructions from document
    text or arbitrary payload fields with names such as ``instruction``.
    """
    sections = []
    if instruction is not None:
        sections.append("本节点固定处理规则（node_task_instructions）：\n" + instruction)
    if not isinstance(data, dict):
        return ("\n" + "\n".join(sections) if sections else ""), data
    task = dict(data)
    containers = [task]
    # judge_answer wraps the workflow's task under context. Other nested
    # mappings, including uploaded source records, remain ordinary task data.
    if isinstance(data.get("context"), dict):
        task["context"] = dict(data["context"])
        containers.append(task["context"])
    config = qa_director or {}
    controls = _learning_rules(task, config)
    if controls:
        sections.append("本次样本用途与语言要求（仅约束候选对话，不约束评审回执语言）：\n" + "\n".join(controls))
    for field, label in (("question_rules", "提问规则"), ("answer_rules", "回答规则")):
        rule = config.get(field)
        if not isinstance(rule, str) or not rule:
            continue
        matched = [item for item in containers if item.get(field) == rule]
        if matched:
            sections.append(f"本节点用户配置的{label}（{field}）：\n" + rule)
            for item in matched:
                del item[field]
    return ("\n" + "\n".join(sections) if sections else ""), task
