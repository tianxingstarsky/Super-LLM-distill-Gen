"""兼容层：旧的字符串常量入口。

M2 起提示词统一走 lib.prompts 资产库（版本/出处/约束声明）；
本模块仅保留旧名字，供 scripts/real_spike.py 等历史调用方使用。
注意：generator 自 v1.2.0 起含 style_guide 变量，此处预填充为"默认风格"以兼容旧调用。
"""
from lib.prompts.distill import GENERATOR, REFLECTOR, SUMMARIZER
from lib.prompts.magpie import QUERY

REFLECTOR_TEXT_PROMPT = REFLECTOR.template
GENERATOR_TEXT_PROMPT = GENERATOR.template.replace("{style_guide}", "默认风格")
SUMMARIZER_TEXT_PROMPT = SUMMARIZER.template
MAGPIE_QUERY_SYSTEM_PROMPT = QUERY.template
