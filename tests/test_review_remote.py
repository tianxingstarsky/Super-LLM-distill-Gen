"""分布式评审客户端离线测试（fake Argilla SDK 全流程）。"""
from __future__ import annotations

import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent


class FakeArgilla:
    """模拟中心机 SDK：records 迭代、Response 提交记录。"""

    def __init__(self):
        self.me = type("Me", (), {"id": "user-666"})()
        self.submitted = []
        self._records = [
            type("Rec", (), {
                "id": "rec-1",
                "fields": {"sample_id": "s1", "instruction": "解释过拟合", "conversation": "过拟合是…", "meta": "m"},
                "suggestions": {},
                "responses": {},
                "status": "pending",
            })(),
            type("Rec", (), {
                "id": "rec-2",
                "fields": {"sample_id": "s2", "instruction": "写代码", "conversation": "def f…", "meta": "m"},
                "suggestions": {},
                "responses": {},
                "status": "pending",
            })(),
        ]

    class Datasets:
        def __init__(self, owner): self._owner = owner
        def __call__(self, name=None): return self
        def records(self, **kw):
            return self._owner._records if not kw else (self._owner._records[0] if kw.get("record_id") == "rec-1" else None)

    def datasets(self, name=None):
        self.added_questions = []
        self.logged = []
        class _Records:
            def __init__(self, owner):
                self.owner = owner
            def __iter__(self):
                return iter(self.owner._records)
            def log(self, records=None, **kw):
                self.owner.logged = records or []
        class _Questions:
            def __init__(self, owner):
                self.owner = owner
            def add(self, q):
                self.owner.added_questions.append(q)
        return type("", (), {
            "records": _Records(self),
            "questions": _Questions(self),
        })()


def test_pull_skips_submitted():
    from lib import review_remote as rr

    cfg = {"server": "http://x", "api_key": "k", "dataset": "rollout_review"}
    inbox = rr.pull(cfg, batch=5, client=FakeArgilla())
    assert len(inbox) == 2
    inbox_text = (ROOT / "data" / "output" / "remote_inbox.jsonl").read_text(encoding="utf-8")
    # 缓存文件可回读
    cached = [json.loads(l) for l in inbox_text.splitlines() if l.strip()]
    assert cached[0]["sample_id"] == "s1"


def test_agent_model_override_passed_to_judge(monkeypatch):
    """agent 模式：模型来自配置/--model，不是中心机决定（自己的 AGENT 用自己的模型）。"""
    import lib.review_remote as rr

    seen = {}

    class FakeJudger:
        def chat(self, messages, **kw):
            seen["touched"] = True
            return '{"correctness": 5, "keep": true}'

    decisions = [{"record_id": "r1", "sample_id": "s1", "instruction": "q", "conversation": "a", "meta": ""}]
    out = rr._judge_answers(decisions, FakeJudger(), "my-local-model")
    assert seen.get("touched")
    assert out[0]["model"] == "my-local-model"
    assert out[0]["decision"] == "keep"
    assert "correctness=5" in out[0]["reason"]


def test_submit_carries_reason_and_audit_identity(monkeypatch):
    """提交必须带身份（我的 user_id）+ 判定理由；理由题缺失时幂等补建。"""
    from lib import review_remote as rr

    class _DummyQ:
        """离线场景没有真 Argilla server，替换 TextQuestion 构造（参数照收）。"""
        def __init__(self, *args, **kwargs):
            self.name = kwargs.get("name") or (args[0] if args else None)

    monkeypatch.setattr("argilla.TextQuestion", _DummyQ)

    cfg = {"server": "http://x", "api_key": "k", "dataset": "rollout_review"}
    fake = FakeArgilla()
    decisions = [{
        "record_id": "rec-1", "sample_id": "s1", "instruction": "q",
        "conversation": "a", "meta": "m", "decision": "reject",
        "reason": "本地 agent 判定: correctness=2 (幻觉)", "model": "my-model",
    }]
    n = rr.submit(decisions, cfg, client=fake)
    assert n == 1
    assert len(fake.added_questions) == 1  # reason 题幂等补建
    rec = fake.logged[0]
    assert rec.id == "rec-1" and rec.fields["sample_id"] == "s1"
    resps = {r.question_name: r for r in rec.responses}
    assert resps["keep_label"].value == "reject"
    assert resps["reason"].value.startswith("本地 agent 判定")
    assert all(r.user_id == "user-666" for r in rec.responses)  # 身份=我的账号
