"""Explicit contrastive design, separate from factual quality judgments."""
from copy import deepcopy


DEFAULT_PREFERENCE_DESIGN = {"mode": "off_topic_short", "negative_reasoning_max_chars": 160,
                             "negative_reasoning_max_ratio": 0.25}


def preference_design_requested(config, prompts=None):
    return config is not None or (isinstance(prompts, dict) and any(isinstance(values, dict)
        and "workflow.preference_design_check" in values for values in prompts.values()))


def validate_preference_design(value=None):
    config = deepcopy(DEFAULT_PREFERENCE_DESIGN)
    if value is not None:
        if not isinstance(value, dict) or set(value) - set(config):
            raise ValueError("invalid_preference_design")
        config.update(value)
    if not isinstance(config["mode"], str) or config["mode"] not in {"opposite_short", "off_topic_short"}:
        raise ValueError("invalid_preference_design_mode")
    maximum = config["negative_reasoning_max_chars"]
    ratio = config["negative_reasoning_max_ratio"]
    if type(maximum) is not int or not 20 <= maximum <= 1000:
        raise ValueError("invalid_negative_reasoning_max_chars")
    if type(ratio) not in {int, float} or not 0.05 <= ratio <= 0.5:
        raise ValueError("invalid_negative_reasoning_max_ratio")
    return config
