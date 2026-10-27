"""A retry decision is not an exception and never wraps programmer/cancellation errors."""
from dataclasses import dataclass
from typing import Callable, Protocol


@dataclass(frozen=True)
class RetryDirective:
    error: Exception
    retryable: bool = True
    delay: float = 0.0
    counted: bool = True


class RetryPort(Protocol):
    def run(self, action: Callable[[int], object], *, attempts: int,
            wait: Callable[[float], None]) -> object: ...
