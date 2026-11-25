from __future__ import annotations

from agent.schema import Answer
from agent.v3 import verify


def test_verifier_adapter_passes_when_v2_verifier_passes(monkeypatch):
    monkeypatch.setattr(verify, "verify_multi", lambda answer, handoffs: (True, ""))
    answer, verified, reason, handoffs = verify.verify_answer(
        Answer(text="evidence-backed"),
        [{"tool": "get_forecast", "args": {"as_of": "2020-07-01", "model": "M1"},
          "status": "ok", "rows": [{"date": "2020-07-01", "floored_30d": 0.0025}], "source": "fixture"}],
        [{"tool": "get_forecast", "args": {"as_of": "2020-07-01", "model": "M1"}, "agent": "forecast"}],
    )
    assert verified
    assert not answer.refused
    assert reason == ""
    assert handoffs[0].agent == "forecast"
    assert handoffs[0].evidence[0].rows[0]["date"] == "2020-07-01"


def test_verifier_adapter_refuses_when_v2_verifier_fails(monkeypatch):
    monkeypatch.setattr(verify, "verify_multi", lambda answer, handoffs: (False, "unsupported"))
    answer, verified, reason, _ = verify.verify_answer(Answer(text="unsupported"), [], [])
    assert not verified
    assert answer.refused
    assert answer.refusal_reason == "could not verify"
    assert reason == "unsupported"
