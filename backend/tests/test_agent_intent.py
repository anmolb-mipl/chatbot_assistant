import pytest

from app.agent_intent import wants_agent

YES = [
    "talk to agent", "I want to talk to an agent", "could you connect me to an agent", "connect me to a human please",
    "i want to speak with someone", "can I talk to a real person?", "transfer me to customer care", "Agent", "human please",
    "need an agent", "I need to speak to a representative", "call an agent", "agent se baat karao", "mujhe agent chahiye",
    "insaan se baat karni hai",
]
NO = [
    "what is a demat agent", "how do I open a demat account?", "brokerage charges", "check TXN1002", "hi", "thanks",
    "talk about agent fees", "my money was deducted but the balance is not showing yet and I have waited two days already "
    "for it to be credited please help me understand what happened to my payment",
    "the app is slow", "who is the agent for my account manager fee",
]


@pytest.mark.parametrize("text", YES)
def test_requests_for_a_person_are_detected(text):
    assert wants_agent(text), text


@pytest.mark.parametrize("text", NO)
def test_everything_else_is_not(text):
    assert not wants_agent(text), text
