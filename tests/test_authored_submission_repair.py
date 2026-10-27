from copy import deepcopy
import json
import pytest

from lib.contexts.workflows.application.repair_authored_submission import RepairAuthoredSubmission
from test_strict_authored_submission import submission, derived_candidate


class Port:
    def __init__(self, reply):
        self.reply, self.calls, self.saved = reply, [], {}

    def check_reasoning_cancel(self):
        pass

    def generation_source_turn(self, key, messages, tools, *, final):
        binding = json.dumps(key)
        if binding not in self.saved:
            self.calls.append((messages, tools, final))
            self.saved[binding] = deepcopy(self.reply)
        return deepcopy(self.saved[binding])


def test_explicit_repair_keeps_history_native_thoughts_and_validates_new_submission():
    original = submission(); original.update(content='An extra argument.', reasoning_content='Native thought retained.')
    before = deepcopy(original); history = [{'role': 'user', 'content': 'An unchanged task.'}]
    port = Port(submission()); repair = RepairAuthoredSubmission(port)
    value, receipt = repair.execute(['case'], history, original, answer_required=True)
    assert value == derived_candidate() and original == before
    messages, tools, final = port.calls[0]
    assert messages[:-1] == history + [original]
    assert messages[-1]['role'] == 'tool' and messages[-1]['tool_call_id'] == original['tool_calls'][0]['id']
    assert json.loads(messages[-1]['content'])['protocol_issue'] == 'authored_submission_unassigned_content'
    assert receipt['semantic_status'] == 'not_assessed' and tools[0]['function']['strict'] is True
    assert repair.execute(['case'], history, original, answer_required=True)[0] == value
    assert len(port.calls) == 1


def test_repair_does_not_accept_preamble_or_weaken_schema_and_never_loops():
    original = submission(); original['content'] = 'Outside argument.'
    port = Port(original)
    with pytest.raises(ValueError, match='authored_submission_unassigned_content'):
        RepairAuthoredSubmission(port).execute(['case'], [], original, answer_required=True)
    assert len(port.calls) == 1
    bad = derived_candidate(); bad['reasoning_trace']['checks'] = []
    port = Port(submission(bad))
    with pytest.raises(ValueError, match='authored_submission_schema_invalid'):
        RepairAuthoredSubmission(port).execute(['case'], [], original, answer_required=True)
    assert len(port.calls) == 1


def test_finding_must_be_bound_and_repair_must_have_an_issue():
    port = Port(submission()); usecase = RepairAuthoredSubmission(port)
    with pytest.raises(ValueError, match='without_issue'):
        usecase.execute([], [], submission(), answer_required=True)
    with pytest.raises(ValueError, match='unbound_finding'):
        usecase.execute([], [], submission(), answer_required=True, findings=[{'quote':'Not in draft','issue':'Guess'}])
    assert not port.calls
    old = submission(); old['content'] = 'A quoted "claim".'
    candidate, receipt = usecase.execute([], [], old, answer_required=True,
        findings=[{'quote':'A quoted "claim".', 'issue':'Is this supported?'}])
    assert receipt['observation']['findings'][0]['quote'] == old['content']
