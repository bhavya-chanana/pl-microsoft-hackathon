"""Minimal MAI-Voice text to speech check against an Azure Speech resource (Entra ID, no keys).

    az login
    export MATCHLENS_MAI_REGION=centralindia
    export MATCHLENS_MAI_RESOURCE_ID=/subscriptions/.../accounts/speech-mai-...
    uv run python scripts/mai_voice.py --list                       # which MAI voices this resource offers
    uv run python scripts/mai_voice.py "Goal!" --voice en-US-Harper:MAI-Voice-2.1-Flash

The caller needs the "Cognitive Services Speech User" role on the resource. The bearer token is
``aad#<resource id>#<Entra token>``, the same scheme as matchlens/speech.py.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from xml.sax.saxutils import escape

from azure.identity import DefaultAzureCredential

SCOPE = "https://cognitiveservices.azure.com/.default"


def auth_header() -> dict[str, str]:
    resource_id = os.environ["MATCHLENS_MAI_RESOURCE_ID"]
    token = DefaultAzureCredential().get_token(SCOPE).token
    return {"Authorization": f"Bearer aad#{resource_id}#{token}"}


def host() -> str:
    return f"https://{os.environ['MATCHLENS_MAI_REGION']}.tts.speech.microsoft.com/cognitiveservices"


def call(url: str, data: bytes | None, headers: dict[str, str]) -> bytes:
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read()
    except urllib.error.HTTPError as err:
        sys.exit(f"HTTP {err.code} from {url}: {err.read().decode(errors='replace')[:500]}")


def list_mai_voices() -> None:
    voices = json.loads(call(f"{host()}/voices/list", None, auth_header()))
    mai = sorted(v["ShortName"] for v in voices if "MAI" in v["ShortName"] or "MAI" in str(v.get("VoiceType", "")))
    print(f"{len(mai)} MAI voices of {len(voices)} total")
    print("\n".join(mai))


def synthesize(text: str, voice: str, out: str) -> None:
    locale = "-".join(voice.split(":")[0].split("-")[:2])
    ssml = (f"<speak version='1.0' xmlns='http://www.w3.org/2001/10/synthesis' xml:lang='{locale}'>"
            f"<voice name='{voice}'>{escape(text)}</voice></speak>")
    headers = {**auth_header(), "Content-Type": "application/ssml+xml",
               "X-Microsoft-OutputFormat": "audio-24khz-160kbitrate-mono-mp3"}
    audio = call(f"{host()}/v1", ssml.encode(), headers)
    with open(out, "wb") as f:
        f.write(audio)
    print(f"wrote {len(audio)} bytes to {out} with {voice}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("text", nargs="?", default="Hello, this is a sample from MAI Voice.")
    ap.add_argument("--voice", default="en-US-Harper:MAI-Voice-2.1-Flash")
    ap.add_argument("--out", default="mai-voice.mp3")
    ap.add_argument("--list", action="store_true", help="list the MAI voices the resource offers")
    args = ap.parse_args()
    list_mai_voices() if args.list else synthesize(args.text, args.voice, args.out)
