"""Compatibility import for the domain quality contracts."""
from lib.domain.workflow_quality import (
    PERSONAL_DATA, POLICY, SECRET, accepted, canonical, conversation_issue,
    same_answer, text_issue, verdict,
)

__all__ = ["PERSONAL_DATA", "POLICY", "SECRET", "accepted", "canonical",
           "conversation_issue", "same_answer", "text_issue", "verdict"]
