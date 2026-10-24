"""Repeated previews must round-trip through the real widget serializers."""
import pytest
from streamlit.elements.widgets.selectbox import SelectboxSerde
from streamlit.elements.widgets.multiselect import MultiSelectSerde

from lib.presentation.streamlit.selection_labels import unique_choice_labels


@pytest.mark.parametrize("captions", [
    {"new-session": "人工问答增强", "old-session": "人工问答增强"},
    {"sample-a": "Same question" * 20, "sample-b": "Same question" * 20},
    {"abcdefgh-first": "Same", "abcdefgh-second": "Same", "untouched": "Different"},
    {"a": "Same", "b": "Same", "c": "Same · #a"},
])
def test_formatted_selection_retains_identity_even_when_captions_or_short_ids_collide(captions):
    labels = unique_choice_labels(captions)
    assert len(set(labels.values())) == len(captions)
    options = list(labels)
    kwargs = dict(formatted_options=list(labels.values()),
                  formatted_option_to_option_index={label: index for index, label in enumerate(labels.values())},
                  format_func=labels.__getitem__)
    single = SelectboxSerde(options, **kwargs)
    multi = MultiSelectSerde(options, **kwargs)
    for identity in options:
        assert single.deserialize(single.serialize(identity)) == identity
        assert multi.deserialize(multi.serialize([identity])) == [identity]


def test_unique_user_names_are_kept_verbatim():
    captions = {"a": "泵站问答设计", "b": "My own session"}
    assert unique_choice_labels(captions) == captions
