import pytest
from lib.domain.training_surface import training_surface_issue

ARTICLE = [{'role':'user','content':'Source material:\nThe method prompts the LM with examples to classify instructions.\n\nUser message:\nExplain what this passage establishes.'}]
PUBLIC = ('Two things the passage does not establish: that this prompt yields a unique verdict '
          'on every instruction, and that the exemplars guarantee how the model will classify a given case.')


def test_article_attribution_does_not_leak_the_current_generation_prompt():
    assert training_surface_issue('', PUBLIC, visible_messages=ARTICLE, protocol_version=2) is None


@pytest.mark.parametrize('text', [
    'The passage explains why my system prompt requires English.',
    'The passage is relevant; this prompt requires me to output JSON.',
    'The passage tells me this prompt contains the internal rules.',
    'The passage is an example of this hidden prompt.',
    'The passage motivates this prompt and the source_memory field.',
    'The passage suggests this prompt must use the assigned style.',
])
def test_actual_execution_disclosures_still_fail(text):
    assert training_surface_issue('', text, visible_messages=ARTICLE, protocol_version=2) == 'training_surface_internal_process_reference'


@pytest.mark.parametrize('messages', [
    [{'role':'assistant','content':ARTICLE[0]['content']}],
    [{'role':'user','content':'Explain how prompts work.'}],
    [{'role':'user','content':'Source material:\nA sufficiently long article about rivers and lakes.\n\nUser message:\nExplain this passage.'}],
])
def test_general_topic_or_assistant_history_cannot_authorize_prompt_reference(messages):
    assert training_surface_issue('', PUBLIC, visible_messages=messages, protocol_version=2) == 'training_surface_internal_process_reference'
