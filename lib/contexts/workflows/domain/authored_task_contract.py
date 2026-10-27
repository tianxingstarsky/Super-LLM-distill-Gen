"""Derive call-local evidence and delivery boundaries from actual learner turns.

Stored sources/history stay unchanged. Unknown or open-ended tasks retain their
source context. This policy does not infer factual entailment from syntax.
"""
from copy import deepcopy
import hashlib
import json
import re

from lib.domain.training_surface import _VISIBLE_ARTICLE_BLOCK
from .review_surface_contract import observable_surface_defects

_SCOPED = re.compile(
    r"(?:only|solely|strictly|exclusively).{0,70}(?:passage|excerpt|provided text|paragraph)"
    r"|stick to what this (?:passage|excerpt|paragraph).{0,30}(?:settles|says|establishes)"
    r"|(?:仅|只)(?:依据|根据|按|用|讨论).{0,20}(?:选段|片段|这段|段落|给定|所给)"
    r"|不要.{0,12}(?:引入|使用).{0,12}(?:外部|其他资料)", re.I)
_EXPAND = re.compile(r"(?:beyond|outside).{0,35}(?:passage|excerpt)|use (?:the )?(?:whole|full) (?:paper|document)"
                     r"|结合全文|允许.{0,10}外部|不限于.{0,10}(?:片段|选段)", re.I)
_LANGUAGE = re.compile(r"(?:answer|reply|respond|write)(?:\s+\w+){0,3}\s+in\s+(English|Chinese|Mandarin)"
                       r"|用(中文|英文|英语|汉语)(?:回答|回复|写)|(?:请|请用)?(中文|英文)(?:回答|回复)", re.I)


def task_contract_enabled(recipe):
    return type(recipe.get('authored_task_contract_version')) is int and recipe['authored_task_contract_version'] in (1, 2, 3)


def answer_language_instruction(contract):
    language = {'en': 'English', 'zh': 'Chinese'}.get(contract['answer_language'])
    if not language:
        return ''
    return (f'Final answer delivery: write the answer in {language}. The bilingual interpretation in the '
            'reasoning prefix is not an instruction to translate or repeat the final answer in other languages. '
            'Do not carry its unsolicited bilingual glosses into the answer. Preserve genuine quotations, '
            'proper names, and other-language material that the actual user task requires. '
            'This constrains answer delivery, not the retained reasoning or the evidence. Do not announce this rule.')


def _request_text(text):
    return _VISIBLE_ARTICLE_BLOCK.sub('', text).strip()


def compile_task_contract(task):
    messages = task.get('learner_messages', task.get('prompt', []))
    users = [m['content'] for m in messages if isinstance(m, dict) and m.get('role') == 'user'
             and isinstance(m.get('content'), str)]
    current = _request_text(users[-1]) if users else ''
    # Only explicit, recognizable language directives override the current turn.
    # Quotes/code are not directives. Unclear language remains undetermined.
    directives = []
    for user in users:
        plain = re.sub(r'```[\s\S]*?```|"[^"\n]*"|“[^”\n]*”', '', _request_text(user))
        directives.extend(_LANGUAGE.finditer(plain))
    language = None
    if directives:
        label = next(g for g in directives[-1].groups() if g).lower()
        language = 'en' if label in {'english', '英文', '英语'} else 'zh'
    elif re.search(r'translat|翻译|bilingual|中英|(?:answer|reply|respond|write).{0,25}\bin\s+[A-Za-z]+', current, re.I):
        language = None
    else:
        zh = len(re.findall(r'[\u4e00-\u9fff]', current))
        en = len(re.findall(r'\b[A-Za-z]+\b', current))
        if en >= 8 and zh <= 4:
            language = 'en'
        elif zh >= 12 and en < zh / 3:
            language = 'zh'
    blocks = [_VISIBLE_ARTICLE_BLOCK.search(u) for u in users]
    # A latest explicit restriction may refer back to visible excerpts. A later
    # request to broaden scope revokes it. Never treat article contents as rules.
    scope = False
    for user in users:
        request = _request_text(user)
        if _EXPAND.search(request):
            scope = False
        elif _SCOPED.search(request):
            scope = True
    excerpts = [m['article'] for m in blocks if m]
    scope = scope and bool(excerpts)
    return {'version': 1, 'answer_language': language, 'language_basis': 'explicit_user' if directives else 'current_user',
            'source_scope': 'learner_excerpt_only' if scope else 'available_context',
            'visible_excerpts': excerpts if scope else [],
            'request_sha256': hashlib.sha256(current.encode()).hexdigest()}


def project_task_context(task, contract):
    """A disposable view for both author tools and model request projection."""
    projected = deepcopy(task)
    if contract['source_scope'] != 'learner_excerpt_only':
        return projected
    for key in ('source', 'source_context', 'source_context_pack', 'source_inventory',
                'source_evidence', 'source_line_scope', 'source_lines', 'source_line_ranges', 'source_line_focus', 'quotes',
                'reference_answer', 'teacher_context_pack'):
        projected.pop(key, None)
    projected['source'] = {'kind': 'learner_visible_excerpt',
                           'text': '\n\n'.join(contract['visible_excerpts'])}
    return projected


def task_contract_instruction(contract, *, version=1, phase=None):
    if version == 2:
        scope = ('Use only the learner-visible excerpt for article-specific facts. References to a table '
                 'do not supply its contents. Do not import examples, algorithms or filtering details from '
                 'the full paper or memory and attribute them to this passage. Missing here does not mean '
                 'absent from the full paper. Explain what follows from the supplied text, without adding premises.'
                 if contract['source_scope'] == 'learner_excerpt_only' else
                 'Use the available evidence within its actual scope. Distinguish background knowledge from learner-visible text.')
        language = {'en': 'The final answer must be in English.', 'zh': 'The final answer must be in Chinese.'}.get(
            contract['answer_language'], 'Follow the actual user\'s language request; no unique language was inferred.')
        return ('<authored_task_contract version="2" stage="' + str(phase) + '">\n'
                + language + ' This applies to answer delivery, independently of the configured bilingual reasoning. '
                'A Chinese reasoning prefix is not a request for a Chinese answer. '
                + scope + '\n'
                'Test the actual inference, not just whether its topic is relevant: frequency words do not establish '
                'absence of exceptions; a step used in a described procedure does not establish that every alternative '
                'procedure is impossible; a working definition does not establish a universal necessary condition. '
                'Conditioning generation on a label expresses a target, not a certificate that generated inputs '
                'satisfy it. A claimed guarantee needs evidence or a valid implication covering that guarantee. '
                'If the source is compatible with a counterexample, revise the claim itself. A caveat elsewhere '
                'cannot cancel an overstatement. Assess only relevant doubts, with no fixed order, headings, '
                'catchphrases or artificial failed attempts. Keep the full reasoning. The prefix and earlier '
                'assistant statements are not independent evidence. Do not quote these production instructions.\n'
                '</authored_task_contract>')
    scope = ('本任务明确只依据用户可见选段。资料库仍保存全文，但本轮只能用这些选段建立文章事实；'
             '选段提及表号不等于提供了表格内容。不能从记忆补表格示例、其他章节或过滤细节再称为选段结论。'
             '可以解释选段已经描述的逻辑；片段没给的实现参数应保持未定，不扩大为全文没有。'
             if contract['source_scope'] == 'learner_excerpt_only' else
             '可使用当前任务允许的资料，区分用户可见内容与背景知识，不扩大资料原有结论。')
    language = {'en': '最终 answer 使用英语，推理的中文或双语设置不改变答案语言。',
                'zh': '最终 answer 使用中文，保留必要术语与引文。'}.get(contract['answer_language'],
                '答案语言按真实用户的要求处理；程序未推定唯一语言。')
    return ('<authored_task_contract version="1">\n' + scope + language
            + '推理与答案中的结论必须保持同样的证据强度。区分方法采用的生成顺序、设计目的、实测效果和普遍保证：'
              '规定执行某个步骤，不证明该步骤一定成功或覆盖全部情况；作者采用一种方案，不证明其他时序或方案都无效。'
              '推导必要性或充分性时，用当前证据判断反例是否仍可能成立；条件不足就修改该断言本身，不能只在结尾加免责语。'
              '这是本题的输出边界，不是正文栏目或固定思考步骤；自检的主题由实际疑点决定，保留完整推敲和有效修正。'
              '推理前缀和历史助手说法均不是事实认证。交付正文前对照真实请求的范围和答案语言，不复述本段规则。'
              '\n</authored_task_contract>')


def task_surface_receipt(task, candidate, contract, *, phase='final'):
    """Check observable violations; do not claim that a self-check proves truth."""
    texts = {k: candidate.get(k, '') for k in ('reasoning', 'answer')}
    defects = [d for d in observable_surface_defects(task, texts, phase)
               if not d['issue'].startswith('answer_language_mismatch')]
    answer = re.sub(r'```[\s\S]*?```|“[^”]*”|"[^"]*"', '', texts['answer'])
    zh = len(re.findall(r'[\u4e00-\u9fff]', answer))
    en = len(re.findall(r'\b[A-Za-z]+\b', answer))
    expected = contract['answer_language']
    mismatch = (expected == 'en' and zh >= 40 and en < zh / 3
                or expected == 'zh' and en >= 30 and zh <= 4)
    if mismatch and phase == 'final':
        defects.append({'kind': 'task', 'field': 'answer', 'quote': texts['answer'],
                        'issue': 'answer_language_mismatch_' + expected, 'rule_id': 'answer_language'})
    # Reuse of a literal hidden-source quotation is demonstrably out of scope.
    # Terms, paraphrases, ellipses and semantic entailment remain unclassified.
    if contract['source_scope'] == 'learner_excerpt_only':
        visible = re.sub(r'\s+', ' ', '\n'.join(contract['visible_excerpts']))
        source = task.get('source', task.get('source_context', {}))
        source = source.get('text', '') if isinstance(source, dict) else ''
        hidden = re.sub(r'\s+', ' ', source)
        for field, text in texts.items():
            for match in re.finditer(r'"([^"\n]{30,})"|“([^”\n]{30,})”', text):
                quote = next(g for g in match.groups() if g)
                normalized = re.sub(r'\s+', ' ', quote)
                if normalized in hidden and normalized not in visible:
                    defects.append({'kind': 'scope', 'field': field, 'quote': quote,
                                    'issue': 'quoted_evidence_outside_user_scope', 'rule_id': 'evidence_scope'})
    return {'version': 1, 'defects': defects, 'observable_passed': not defects,
            'semantic_status': 'not_independently_assessed',
            'contract_sha256': hashlib.sha256(json.dumps(contract, sort_keys=True, ensure_ascii=False).encode()).hexdigest()}
