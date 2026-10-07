"""The Premium page, answer first (7 Oct 2026, approved by Michael from
before-and-after screenshots).

Before you pay printed seven answers of 29 to 81 words in full, about
three phone screens under the prices. Each now leads with its direct
answer and keeps the rest behind a tap named for what it holds; the
structured data still gives the whole answer. Doing this yourself leads
with its 28 websites and 36 minutes, and what that involved is a tap.
"""
import json
import re

from app import main as app_main


def _premium(client, monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_placeholder")
    monkeypatch.setenv("STRIPE_PRICE_ID_MONTHLY", "price_m")
    monkeypatch.setenv("STRIPE_PRICE_ID_QUARTERLY", "price_q")
    monkeypatch.delenv("STRIPE_PRICE_ID_PASS", raising=False)
    from tests.test_ai_search_readiness import _forget_html
    _forget_html()
    return client.get("/premium").text


def test_each_answer_leads_and_its_detail_is_a_named_tap(client, monkeypatch):
    body = _premium(client, monkeypatch)
    section = body.split('id="before-you-pay"', 1)[1].split("</section>", 1)[0]
    pairs = re.findall(r"<dt>(.*?)</dt>\s*<dd>(.*?)</dd>", section, re.S)
    labels = {q: re.findall(r"<summary>(.*?)</summary>", a) for q, a in pairs}
    assert labels == {
        "Do I need a card for the free report?": [],
        "I only have one house to check. Am I stuck with a monthly bill?": ["Renewing and cancelling"],
        "Can I cancel?": ["What happens to your homes"],
        "What do I get that the free report does not show?": ["What that includes"],
        "I am buying outside England. What does Premium cover there?": ["Which checks are England only"],
        "Where does the data come from?": ["Which bodies, and OpenStreetMap"],
    }
    cancel = dict(pairs)["Can I cancel?"]
    lead = cancel.split("<details", 1)[0]
    assert " ".join(lead.split()) == ("Yes, any time. Once you have subscribed there is a Cancel subscription "
                                      "button on this page that takes you straight to Stripe to confirm it.")
    assert "The home you opened with your free full report stays open for good." in cancel.split("<details", 1)[1]


def test_the_structured_data_still_gives_the_whole_answer(client, monkeypatch):
    body = _premium(client, monkeypatch)
    data = next(json.loads(s) for s in re.findall(r'<script type="application/ld\+json">(.*?)</script>', body, re.S)
                if '"FAQPage"' in s)
    answers = {q["name"]: q["acceptedAnswer"]["text"] for q in data["mainEntity"]}
    assert answers["I am buying outside England. What does Premium cover there?"] == app_main.premium_reach_summary()
    assert answers["Can I cancel?"].startswith("Yes, any time.")
    assert answers["Can I cancel?"].endswith("The home you opened with your free full report stays open for good.")
    assert " ".join(app_main.premium_reach_parts()) == app_main.premium_reach_summary()


def test_doing_it_yourself_leads_with_its_two_figures(client, monkeypatch):
    body = _premium(client, monkeypatch)
    block = body.split('id="by-hand-summary"', 1)[1].split("</section>", 1)[0]
    minutes = block.index("It took 36 minutes")
    tap = block.index("<summary>What it involved</summary>")
    assert block.index("<strong>28 websites.</strong>") < minutes < tap
    assert block.index("That is what those checks cost by hand") > tap
