"""提示词资产库包。所有提示词经 lib.prompts.get(id) 获取并声明版本/出处/约束。"""
from lib.prompts.base import PromptRenderError, PromptSpec, get, registry, render  # noqa: F401
