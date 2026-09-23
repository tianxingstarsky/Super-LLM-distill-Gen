"""Compatibility imports for the human-review editor's layered implementation.

Existing review-center callers retain this module's functions while the sample
rules live in Domain and model suggestions cross an Application port.
"""
from __future__ import annotations

from lib.domain.review_edit import (
    EDITABLE_ASSISTANT_FIELDS,
    EDITABLE_OTHER_FIELDS,
    IMAGE_META_RE,
    _merge_text_field,
    _validate_messages,
    merge_scoped_fields,
    normalize_sample,
    pack_sample,
    unpack_record,
    validate_edits,
)


def propose_field(messages, index, field, instruction, client, selection=None):
    from lib.bootstrap.review_edits import review_edit_application

    return review_edit_application(client).propose_field(
        messages, index, field, instruction, selection=selection,
    )
