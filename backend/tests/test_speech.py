"""Voice narration: SSML, Entra token format, caching, and what gets read aloud. No Azure calls."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from matchlens.agents.contracts import FanProfile, Language, Mode
from matchlens.agents.services import narration_text
from matchlens.speech import Narrator, SpeechSettings, ssml

SETTINGS = SpeechSettings(region="centralindia", resource_id="/subscriptions/s/resourceGroups/r/providers/x/speech")


class FakeCredential:
    def __init__(self) -> None:
        self.calls = 0

    def get_token(self, scope: str):
        self.calls += 1
        assert scope == "https://cognitiveservices.azure.com/.default"
        return SimpleNamespace(token="entra-token", expires_on=10**12)


def test_ssml_escapes_and_picks_the_voice():
    doc = ssml("Rovers <3 & a  late   winner", "hi")
    assert "hi-IN-MadhurNeural" in doc and "xml:lang='hi-IN'" in doc
    assert "Rovers &lt;3 &amp; a late winner" in doc
    assert "en-GB-RyanNeural" in ssml("x", "unknown")


def test_narrator_uses_entra_and_caches():
    sent = []

    def post(url, body, headers):
        sent.append((url, body, headers))
        return b"mp3"

    cred = FakeCredential()
    narrator = Narrator(SETTINGS, credential=cred, post=post)
    assert asyncio.run(narrator.speak("Goal!", "en")) == b"mp3"
    assert asyncio.run(narrator.speak("Goal!", "en")) == b"mp3"  # cached
    asyncio.run(narrator.speak("Goal!", "es"))  # another language is another clip
    assert len(sent) == 2 and cred.calls == 1  # the token is reused until it nears expiry
    url, body, headers = sent[0]
    assert url == "https://centralindia.tts.speech.microsoft.com/cognitiveservices/v1"
    assert headers["Authorization"] == f"Bearer aad#{SETTINGS.resource_id}#entra-token"
    assert headers["X-Microsoft-OutputFormat"].endswith("mp3") and b"Goal!" in body


MAI = SpeechSettings(region="centralindia", resource_id="/r/neural", mai_region="centralindia",
                     mai_resource_id="/r/mai")


def test_ssml_mai_voice_carries_the_model_suffix():
    doc = ssml("Goal & more", "hi", "MAI-Voice-2.1-Flash")
    assert "<voice name='hi-IN-Arjun:MAI-Voice-2.1-Flash'>" in doc and "xml:lang='hi-IN'" in doc
    assert "Goal &amp; more" in doc and "prosody" not in doc
    assert "en-GB-Harry:MAI-Voice-2.1" in ssml("x", "unknown", "MAI-Voice-2.1")


def test_mai_is_tried_first_with_its_own_resource():
    sent = []

    def post(url, body, headers):
        sent.append((headers["Authorization"], body))
        return b"mai-mp3"

    narrator = Narrator(MAI, credential=FakeCredential(), post=post)
    assert narrator.mode == "mai+azure" and narrator.available
    assert asyncio.run(narrator.speak("Goal!", "en")) == b"mai-mp3"
    assert len(sent) == 1 and sent[0][0] == "Bearer aad#/r/mai#entra-token"
    assert b"en-GB-Harry:MAI-Voice-2.1-Flash" in sent[0][1]


def test_mai_failure_falls_back_to_neural_and_backs_off():
    calls = []

    def post(url, body, headers):
        calls.append(headers["Authorization"])
        if "/r/mai" in headers["Authorization"]:
            raise OSError("MAI is down")
        return b"neural-mp3"

    narrator = Narrator(MAI, credential=FakeCredential(), post=post)
    assert asyncio.run(narrator.speak("One", "en")) == b"neural-mp3"
    assert calls == ["Bearer aad#/r/mai#entra-token", "Bearer aad#/r/neural#entra-token"]
    calls.clear()
    assert asyncio.run(narrator.speak("Two", "en")) == b"neural-mp3"  # inside the back-off window: MAI skipped
    assert calls == ["Bearer aad#/r/neural#entra-token"]
    narrator._mai_down_until = 0.0  # back-off over: MAI is tried again
    calls.clear()
    asyncio.run(narrator.speak("Three", "en"))
    assert calls[0] == "Bearer aad#/r/mai#entra-token"


def test_mai_only_has_no_fallback_and_reports_the_error():
    def post(url, body, headers):
        raise OSError("MAI is down")

    narrator = Narrator(SpeechSettings(None, None, "centralindia", "/r/mai"), credential=FakeCredential(), post=post)
    assert narrator.mode == "mai" and narrator.available
    with pytest.raises(OSError):
        asyncio.run(narrator.speak("x", "en"))


def test_mode_reflects_what_is_configured():
    assert Narrator(SETTINGS).mode == "azure"
    assert Narrator(SpeechSettings(None, None)).mode is None


def test_unconfigured_narrator_refuses():
    narrator = Narrator(SpeechSettings(None, None))
    assert not narrator.available
    with pytest.raises(LookupError):
        asyncio.run(narrator.speak("x", "en"))


def test_narration_text_follows_the_fan_profile():
    insight = {
        "stage": "agent", "headline": "Goal", "explanation": "",
        "variants": {"casual": {"en": {"headline": "Rovers lead!", "body": "A header from the corner."},
                                "es": {"headline": "¡Rovers por delante!", "body": "Un cabezazo."}},
                     "analyst": {"en": {"headline": "0.31 xG header", "body": "Near-post run."}}},
        "club_angles": {"KES": {"en": "Your side are in front."}},
    }
    session = SimpleNamespace(insights={"i1": insight, "gone": {**insight, "stage": "retracted"}})

    def text(**kw):
        profile = FanProfile(**{"mode": Mode.CASUAL, "language": Language.EN, **kw})
        return narration_text(session, kw.pop("insight_id", "i1"), profile)

    assert text() == "Rovers lead. A header from the corner."
    assert text(language=Language.ES) == "¡Rovers por delante. Un cabezazo."
    assert text(mode=Mode.ANALYST) == "0.31 xG header. Near-post run."
    assert text(club_id="KES").endswith("Your side are in front.")
    assert narration_text(session, "gone", FanProfile()) is None
    assert narration_text(session, "missing", FanProfile()) is None
