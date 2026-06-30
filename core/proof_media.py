from __future__ import annotations
"""
Proof / legitimacy media library.

A small catalog of lab videos and product/warehouse photos the sales agent can
send to a prospect on request (e.g. "are you legit? / show me proof / do you have
pictures of the product?"). The agent uses judgement to pick the most fitting
asset; the system serves the file from the Flask app and sends it over WhatsApp
as a Twilio media attachment.

Files live in  static/proof/  and are described in  static/proof/manifest.json:

    [
      {"key": "lab_tour",     "file": "lab_tour.mp4",     "type": "video",
       "description": "Walkthrough video of our lab with today's paper/date shown — best general proof we are a real lab."},
      {"key": "vials_closeup","file": "vials_closeup.jpg", "type": "image",
       "description": "Close-up photo of finished peptide vials — good when they ask to see the actual product."}
    ]

To add an asset: drop the file in static/proof/, add an entry to manifest.json
(unique key, exact filename, type image|video, a clear description so the agent
can choose well), commit, and redeploy. No code change needed.

Keep files within WhatsApp's media limit (≈16 MB); compress long videos first.
"""
import json
from pathlib import Path

PROOF_DIR = Path(__file__).resolve().parent.parent / "static" / "proof"
MANIFEST_PATH = PROOF_DIR / "manifest.json"

_VALID_TYPES = {"image", "video"}


def load_manifest() -> list[dict]:
    """Return the list of available proof assets whose files actually exist.

    Best-effort and defensive: a missing/broken manifest or a manifest entry
    pointing at a missing file is skipped, never raised — the agent simply has
    fewer (or no) assets to offer."""
    try:
        raw = json.loads(MANIFEST_PATH.read_text())
    except Exception:
        return []
    out = []
    for e in raw if isinstance(raw, list) else []:
        try:
            key, file, typ = e["key"], e["file"], e.get("type", "image")
            if typ not in _VALID_TYPES:
                continue
            if not (PROOF_DIR / file).is_file():
                continue
            out.append({"key": str(key), "file": str(file), "type": typ,
                        "description": str(e.get("description", "")).strip()})
        except Exception:
            continue
    return out


def get_media_by_key(key: str) -> dict | None:
    key = (key or "").strip()
    for e in load_manifest():
        if e["key"] == key:
            return e
    return None


def get_media_catalog_text() -> str:
    """Prompt-facing list of what the agent may send. Empty string if none."""
    items = load_manifest()
    if not items:
        return ""
    lines = [f"- {e['key']} ({e['type']}): {e['description']}" for e in items]
    return "\n".join(lines)


def has_media() -> bool:
    return bool(load_manifest())
