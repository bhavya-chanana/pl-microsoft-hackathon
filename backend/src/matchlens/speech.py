"""Voice narration through Azure AI Speech (BRD §6.2, now in scope).

The backend turns an insight's fan text into speech with the text to speech
REST API. Authentication is Microsoft Entra ID only (the backend's managed
identity, or ``az login`` locally): the bearer token is
``aad#<resource id>#<Entra token>``, which needs a Speech resource with a
custom subdomain and the Cognitive Services Speech User role. No keys.

Only match content is ever spoken (the API takes an insight id, never free
text), so the endpoint cannot be used as a general text to speech service.
Without configuration, ``/api/health`` reports no speech and the browser
falls back to its own ``speechSynthesis`` voices.

Settings: ``MATCHLENS_SPEECH_REGION`` (e.g. ``centralindia``) and
``MATCHLENS_SPEECH_RESOURCE_ID`` (the Speech resource's ARM id).

MAI voices (preview): when ``MATCHLENS_MAI_SPEECH_REGION`` and ``MATCHLENS_MAI_SPEECH_RESOURCE_ID`` point at a
Speech resource in a MAI region, narration tries the MAI voice first (``MATCHLENS_MAI_VOICE_MODEL``, default
``MAI-Voice-2.1-Flash``, the low-latency one) and falls back to the neural voice above if the call fails, then
backs off the MAI resource for a minute. Either resource alone is enough.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import time
import urllib.request
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from xml.sax.saxutils import escape

log = logging.getLogger(__name__)

SCOPE = "https://cognitiveservices.azure.com/.default"
OUTPUT_FORMAT = "audio-24khz-48kbitrate-mono-mp3"
# A broadcast-style male voice per fan language; all GA neural voices.
VOICES = {"en": ("en-GB", "en-GB-RyanNeural"), "hi": ("hi-IN", "hi-IN-MadhurNeural"),
          "es": ("es-ES", "es-ES-AlvaroNeural")}
# A broadcast-style male MAI voice per fan language. The model is a suffix on the voice name.
MAI_MODEL = "MAI-Voice-2.1-Flash"
MAI_VOICES = {"en": ("en-GB", "en-GB-Harry"), "hi": ("hi-IN", "hi-IN-Arjun"), "es": ("es-MX", "es-MX-Alejo")}
MAI_RETRY_SECONDS = 60  # after a MAI failure, go straight to the fallback for this long
MAX_CHARS = 600  # one insight, not a recap; keeps cost and latency down
CACHE_ITEMS = 200


@dataclass(frozen=True)
class SpeechSettings:
    region: str | None
    resource_id: str | None
    mai_region: str | None = None
    mai_resource_id: str | None = None
    mai_model: str = MAI_MODEL

    @classmethod
    def from_env(cls) -> SpeechSettings:
        return cls(os.getenv("MATCHLENS_SPEECH_REGION") or None, os.getenv("MATCHLENS_SPEECH_RESOURCE_ID") or None,
                   os.getenv("MATCHLENS_MAI_SPEECH_REGION") or None,
                   os.getenv("MATCHLENS_MAI_SPEECH_RESOURCE_ID") or None,
                   os.getenv("MATCHLENS_MAI_VOICE_MODEL") or MAI_MODEL)

    @property
    def configured(self) -> bool:
        """The neural-voice resource (also the fallback when MAI is set up)."""
        return bool(self.region and self.resource_id)

    @property
    def mai_configured(self) -> bool:
        return bool(self.mai_region and self.mai_resource_id)

    @property
    def endpoint(self) -> str:
        return f"https://{self.region}.tts.speech.microsoft.com/cognitiveservices/v1"

    @property
    def mai_endpoint(self) -> str:
        return f"https://{self.mai_region}.tts.speech.microsoft.com/cognitiveservices/v1"


def ssml(text: str, language: str, mai_model: str | None = None) -> str:
    body = escape(" ".join(text.split())[:MAX_CHARS])
    if mai_model:
        locale, voice = MAI_VOICES.get(language, MAI_VOICES["en"])
        return f"<speak version='1.0' xml:lang='{locale}'><voice name='{voice}:{mai_model}'>{body}</voice></speak>"
    locale, voice = VOICES.get(language, VOICES["en"])
    return (f"<speak version='1.0' xml:lang='{locale}'><voice name='{voice}'>"
            f"<prosody rate='+5%'>{body}</prosody></voice></speak>")


Post = Callable[[str, bytes, dict[str, str]], bytes]


def _post(url: str, body: bytes, headers: dict[str, str]) -> bytes:
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read()


class Narrator:
    """Synthesises fan text to MP3, with an in-process cache (the same insight is often
    played by many fans in the same language)."""

    def __init__(self, settings: SpeechSettings | None = None, credential: object | None = None,
                 post: Post = _post) -> None:
        self.settings = settings or SpeechSettings.from_env()
        self._credential = credential
        self._post = post
        self._token: tuple[str, float] | None = None
        self._mai_down_until = 0.0
        self._cache: OrderedDict[str, bytes] = OrderedDict()

    @property
    def available(self) -> bool:
        return self.settings.configured or self.settings.mai_configured

    @property
    def mode(self) -> str | None:
        """What /api/health reports: which voices are set up."""
        s = self.settings
        if s.mai_configured:
            return "mai+azure" if s.configured else "mai"
        return "azure" if s.configured else None

    def _bearer(self, resource_id: str | None) -> str:
        now = time.time()
        if self._token is None or self._token[1] - now < 120:
            if self._credential is None:
                from azure.identity import DefaultAzureCredential

                self._credential = DefaultAzureCredential(exclude_interactive_browser_credential=True)
            token = self._credential.get_token(SCOPE)  # type: ignore[attr-defined]
            self._token = (token.token, float(token.expires_on))
        return f"aad#{resource_id}#{self._token[0]}"

    def _call(self, endpoint: str, resource_id: str | None, document: str) -> bytes:
        headers = {
            "Authorization": f"Bearer {self._bearer(resource_id)}",
            "Content-Type": "application/ssml+xml",
            "X-Microsoft-OutputFormat": OUTPUT_FORMAT,
            "User-Agent": "matchlens",
        }
        return self._post(endpoint, document.encode(), headers)

    def _synthesize(self, text: str, language: str) -> bytes:
        s = self.settings
        if s.mai_configured and (not s.configured or time.time() >= self._mai_down_until):
            try:
                return self._call(s.mai_endpoint, s.mai_resource_id, ssml(text, language, s.mai_model))
            except Exception as exc:  # preview service: any failure falls back to the neural voice
                if not s.configured:
                    raise
                self._mai_down_until = time.time() + MAI_RETRY_SECONDS
                log.warning("MAI voice failed (%s); using the neural voice for %ss", exc, MAI_RETRY_SECONDS)
        return self._call(s.endpoint, s.resource_id, ssml(text, language))

    async def speak(self, text: str, language: str) -> bytes:
        if not self.available:
            raise LookupError("speech is not configured")
        key = hashlib.sha256(f"{language}\n{text}".encode()).hexdigest()
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        audio = await asyncio.to_thread(self._synthesize, text, language)
        self._cache[key] = audio
        if len(self._cache) > CACHE_ITEMS:
            self._cache.popitem(last=False)
        return audio
