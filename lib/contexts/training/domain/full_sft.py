"""Optimizer settings shared by preflight and native full-language training."""
import math


def validate_full_sft_optimizer(learning_rate, gradient_accumulation):
    if (type(learning_rate) not in {float, int} or not math.isfinite(learning_rate)
            or learning_rate <= 0 or type(gradient_accumulation) is not int or gradient_accumulation < 1):
        raise ValueError("invalid_full_language_optimizer_settings")
