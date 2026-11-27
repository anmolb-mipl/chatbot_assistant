"""Answers that come from OUR system (backend/app/faqs.json), not from the LLM.

Order for every customer message while chatting with the bot:
  1. fixed rules here (greetings, thanks, stock-tip refusal, exact FAQ wording)  -> instant, free, always the same
  2. everything else                                                             -> Gemini (ai.py)
Edit the answers in faqs.json. The LLM also reads them, so its answers stay consistent with yours.
"""
import json
import re
from dataclasses import dataclass
from pathlib import Path

_data = json.loads(Path(__file__).with_name("faqs.json").read_text(encoding="utf-8"))
FAQS: list[dict] = _data["faqs"]
_SMALLTALK = [(re.compile(s["pattern"], re.I), s) for s in _data["smalltalk"]]


def norm(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


# normalised question / trigger phrase -> FAQ. Only EXACT matches are answered here; anything fuzzier goes to the LLM.
_INDEX: dict[str, dict] = {}
for _f in FAQS:
    for _phrase in [_f["question"], *_f.get("triggers", [])]:
        _INDEX[norm(_phrase)] = _f


@dataclass
class SystemReply:
    text: str
    show_faqs: bool


def public_faqs() -> list[dict]:
    return [{"id": f["id"], "question": f["question"]} for f in FAQS]


def get_faq(faq_id: str) -> dict | None:
    return next((f for f in FAQS if f["id"] == faq_id), None)


def match_system_reply(text: str) -> SystemReply | None:
    for pattern, rule in _SMALLTALK:
        if pattern.search(text.strip()):
            return SystemReply(rule["reply"], bool(rule.get("show_faqs")))
    faq = _INDEX.get(norm(text))
    if faq:
        return SystemReply(faq["answer"], True)
    return None