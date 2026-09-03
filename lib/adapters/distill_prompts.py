"""兼容层：旧的字符串常量入口。

M2 起提示词统一走 lib.prompts 资产库（版本/出处/约束声明）；
本模块仅保留旧名字，供 scripts/real_spike.py 等历史调用方使用。
"""
from lib.prompts.distill import GENERATOR, REFLECTOR, SUMMARIZER
from lib.prompts.magpie import QUERY

REFLECTOR_TEXT_PROMPT = REFLECTOR.template
GENERATOR_TEXT_PROMPT = GENERATOR.template
SUMMARIZER_TEXT_PROMPT = SUMMARIZER.template
MAGPIE_QUERY_SYSTEM_PROMPT = QUERY.template
