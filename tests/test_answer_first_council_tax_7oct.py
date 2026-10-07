"""The council tax page, answer first (7 Oct 2026, approved by Michael from
before-and-after screenshots).

Ashford's page opened on one paragraph holding six figures, and under the
band table sat 81 words on the statutory ratios and a 132-word paragraph
of discounts. The bill and its monthly figure now lead; the ranking has a
line of its own; how the bands are worked out and the discounts are one
tap away, the discounts as a list of the same words.
"""
import re


def test_the_bill_and_its_month_lead_and_the_ranking_follows(client):
    body = client.get("/running-costs/council-tax/basildon").text
    dek = re.search(r'<p class="dek">(.*?)</p>', body, re.S).group(1)
    assert "a month over the usual ten instalments from April" in dek
    assert "highest Band D" not in dek
    after = body[body.index('<p class="dek">'):]
    assert "highest Band D of the" in after[:900]


def test_the_method_and_the_discounts_are_one_tap_away_and_the_source_in_view(client):
    body = client.get("/running-costs/council-tax/basildon").text
    bands = body[body.index("<h2>Every band in"):]
    bands = bands[:bands.index("</section>")]
    assert re.search(r'<p class="source-line">Source: MHCLG council tax tables, 20\d\d-\d\d</p>', bands)
    assert "<summary>How the bands are worked out</summary>" in bands
    assert "<summary>Discounts that may apply</summary>" in bands
    discounts = bands[bands.index("<summary>Discounts that may apply</summary>"):]
    assert '<ul class="note-list">' in discounts
    assert re.search(r"<li>One adult living alone pays <strong>25% less</strong>.</li>", discounts)
