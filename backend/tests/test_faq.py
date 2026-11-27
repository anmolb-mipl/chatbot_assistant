from app.faq import get_faq, match_system_reply, public_faqs


def test_faq_wording_is_answered_by_our_system():
    r = match_system_reply("How do I open a demat account?")
    assert r and "OTP" in r.text and r.show_faqs
    r2 = match_system_reply("  brokerage charges!! ")
    assert r2 and "Profile > Pricing" in r2.text


def test_smalltalk_and_stock_tip_refusal():
    assert match_system_reply("Hi").text.startswith("Hello")
    assert "stock tips" in match_system_reply("which stock should I buy").text
    assert match_system_reply("thanks").show_faqs is False


def test_everything_else_goes_to_the_llm():
    assert match_system_reply("my withdrawal of 5000 is stuck since yesterday") is None
    assert match_system_reply("check TXN1002") is None
    assert match_system_reply("hi, I need help with my account") is None   # greeting rule is anchored


def test_public_list_and_lookup():
    ids = [f["id"] for f in public_faqs()]
    assert "kyc" in ids and get_faq("kyc")["answer"]
    assert get_faq("nope") is None
