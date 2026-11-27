"""Detects when a customer TYPES a request to talk to a person ("connect me to an agent", "I want to speak to a human").

Such a message is NOT sent to Gemini and is not part of the FAQ list. The chat page just opens the Chat / Call card.
Plain standard-library code, easy to test and to extend: add words to the sets below.
"""
import re

_VERBS = {"talk", "speak", "chat", "connect", "connecting", "transfer", "escalate", "reach", "contact", "call", "put", "get", "give", "forward"}
_WANT = {"want", "need", "require", "wanna", "like"}
_PERSON = {"agent", "agents", "human", "representative", "executive", "advisor", "adviser", "operator", "manager",
           "person", "someone", "somebody", "staff", "employee"}
_PERSON_PHRASES = ("customer care", "customer service", "support team", "support staff", "real person", "live person")
_ALONE = {"agent", "human", "representative", "operator", "executive"}        # one-word requests: "agent", "human please"
_FILLER = {"please", "pls", "plz", "now", "live", "a", "an", "the", "me", "to", "with", "i", "want", "need"}
_STOP_BETWEEN = {"about", "regarding", "on", "of", "for", "fees", "fee", "charges", "charge"}   # "talk about agent fees" is not a request
# Hinglish / Hindi
_HI_PERSON = {"agent", "insaan", "insan", "banda", "executive", "representative", "aadmi"}
_HI_ACTION = {"baat", "bat", "chahiye", "chahie", "milao", "milwao", "milwa", "connect", "karao", "karvao", "karwao"}
_HI_DEVANAGARI = ("एजेंट", "इंसान", "इन्सान", "प्रतिनिधि")
_HI_DEVANAGARI_ACTION = ("बात", "चाहिए", "मिलाओ", "कनेक्ट")


def wants_agent(text: str) -> bool:
    lower = text.lower().strip()
    if any(w in text for w in _HI_DEVANAGARI) and any(a in text for a in _HI_DEVANAGARI_ACTION):
        return True
    tokens = re.findall(r"[a-z']+", lower)
    if not tokens or len(tokens) > 15:          # long, detailed messages go to Gemini, which can use the details
        return False
    for phrase in _PERSON_PHRASES:              # treat "customer care" as one person word
        if phrase in lower:
            lower = lower.replace(phrase, "agent")
            tokens = re.findall(r"[a-z']+", lower)

    # "agent", "human please", "live agent"
    if len(tokens) <= 3 and any(t in _ALONE for t in tokens) and all(t in _ALONE | _FILLER for t in tokens):
        return True

    # Hinglish: "agent se baat karao", "mujhe agent chahiye", "insaan se baat karni hai"
    if len(tokens) <= 10 and any(t in _HI_PERSON for t in tokens) and any(t in _HI_ACTION for t in tokens):
        return True

    # English: a request verb, then a person word shortly after, with nothing like "about" in between
    for i, t in enumerate(tokens):
        if t in _VERBS or t in _WANT:
            for j in range(i + 1, min(i + 7, len(tokens))):
                if tokens[j] in _PERSON:
                    between = set(tokens[i + 1:j])
                    if not (between & _STOP_BETWEEN):
                        return True
    return False
