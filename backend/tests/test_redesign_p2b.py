"""P2-b: key numbers carry the unit once."""

from __future__ import annotations

import pytest

from matchlens.ingest.adapters import SyntheticReplayAdapter
from matchlens.runtime.session import MatchSession

from .conftest import replay_order


@pytest.fixture(scope="module")
def played(demo_match):
    session = MatchSession(SyntheticReplayAdapter(demo_match))
    for kind, item in replay_order(demo_match):
        session.engine.ingest_event(item) if kind else session.engine.ingest_frame(item)
    return session

from matchlens.agents.contracts import KeyNumber, TacticalDraft
from matchlens.agents.pipeline import InsightPipeline
from matchlens.interpret.signals import SignalType

from .test_agents import ScriptedClient, fresh_session, run, signal_of, team_for


@pytest.mark.parametrize("value,unit,expected", [
    ("12.7 m", "m", "12.7"),
    ("12.7m", "m", "12.7"),
    ("45%", "%", "45"),
    ("7/100", "/100", "7"),
    ("32.1 KM/H", "km/h", "32.1"),
    ("3 v 2", None, "3 v 2"),
    ("12.7", "m", "12.7"),
    ("12 km", "m", "12 km"),  # "m" is not a trailing copy of "km"
    ("m", "m", "m"),  # never leave an empty value
])
def test_key_number_unit_appears_once(value, unit, expected):
    assert KeyNumber(label="x", value=value, unit=unit).value == expected


def test_key_number_schema_still_builds_for_structured_output():
    schema = TacticalDraft.model_json_schema()
    kn = schema["$defs"]["KeyNumber"]
    assert kn["properties"]["value"]["description"] == "The number only, without the unit"
    assert kn.get("additionalProperties") is False and set(kn["required"]) == {"label", "value", "unit"}


def test_pipeline_strips_a_unit_the_model_wrote_into_the_value(played):
    def with_unit(draft, payload):
        bad = KeyNumber.model_construct(label="Pass length", value="12.7 m", unit="m")  # skips the validator
        return draft.model_copy(update={"key_numbers": [bad]})

    session = fresh_session(played)
    pipe = InsightPipeline(team_for(ScriptedClient(tamper={"tactical": with_unit})))
    goal = signal_of(played, SignalType.GOAL, "23'")
    run(pipe.handle(goal, session))
    ins = session.insights[goal.signal_id]
    assert ins["fact_check"]["status"] == "verified"
    assert [(k["value"], k["unit"]) for k in ins["key_numbers"]] == [("12.7", "m")]
