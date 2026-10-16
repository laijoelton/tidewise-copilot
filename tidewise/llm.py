"""Optional language model (Gemini).

The model has two narrow jobs and no authority:
  * classify the owner's free-text request into one of the agent's known
    intents when the deterministic router can't;
  * phrase the final reply from structured tool results.

It never sees raw external content (emails, documents) — only the owner's
own words and Tidewise's structured, masked outputs — and its output is
never executed: intents are validated against a fixed list, and every
action still goes through policy.Policy. Without a key, Tidewise runs fully
offline on the deterministic router and templates.
"""

import json
import os
from typing import Any, Dict, List, Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    from google import genai
    from google.genai import types
except ImportError:
    genai = None


class LLM:
    def __init__(self):
        keys = os.environ.get("GEMINI_API_KEYS") or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or ""
        self.keys = [k.strip() for k in keys.split(",") if k.strip() and "your_" not in k]
        self.model = os.environ.get("GEMINI_MODEL") or "gemini-3.6-flash"
        self.client = genai.Client(api_key=self.keys[0]) if (self.keys and genai) else None

    @property
    def available(self) -> bool:
        return self.client is not None

    def _call(self, prompt: str, json_mode: bool) -> Optional[str]:
        if not self.client:
            return None
        try:
            cfg = types.GenerateContentConfig(temperature=0.2,
                                              response_mime_type="application/json" if json_mode else "text/plain")
            return self.client.models.generate_content(model=self.model, contents=prompt, config=cfg).text
        except Exception:
            return None

    def classify(self, text: str, intents: Dict[str, str]) -> List[str]:
        menu = "\n".join(f"- {k}: {v}" for k, v in intents.items())
        raw = self._call(
            "You route a small-business owner's request to finance tools. Choose zero or more intents from this "
            f"list, and nothing else:\n{menu}\n\nRequest: {text!r}\n"
            'Reply as JSON: {"intents": ["..."]}', json_mode=True)
        try:
            chosen = json.loads(raw or "{}").get("intents", [])
        except (ValueError, AttributeError):
            return []
        return [i for i in chosen if i in intents]

    def narrate(self, question: str, facts: Dict[str, Any], fallback: str) -> str:
        raw = self._call(
            "You are Tidewise, a finance copilot for a small exporter. Answer the owner in at most 6 short "
            "sentences, using only these facts (amounts in RM unless stated). Never invent numbers, never say "
            "an action happened unless it is listed as executed, and say clearly which actions wait for the "
            f"owner's approval.\n\nOwner asked: {question!r}\n\nFacts:\n{json.dumps(facts, default=str)[:6000]}",
            json_mode=False)
        return raw.strip() if raw else fallback
