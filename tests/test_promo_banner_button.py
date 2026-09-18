"""The promo banner's button is solid (18 Sep 2026).

On 26 Aug the pulse keyframes moved from a box-shadow ring to an ::after
ring's opacity and scale, and the button kept the same animation, so the
homepage's "Sign up free" faded between 55% and nothing and grew 14% every
2.4 seconds for three weeks. Only the ring may pulse.
"""
import re


def _rule(css: str, selector: str) -> str:
    match = re.search(r"(?m)^" + re.escape(selector) + r"\s*\{([^}]*)\}", css)
    assert match, selector
    # Declarations only: the rule's own comment explains the history.
    return re.sub(r"/\*.*?\*/", "", match.group(1), flags=re.S)


def test_the_button_itself_never_animates():
    css = open("app/static/css/style.css", encoding="utf-8").read()
    assert "animation" not in _rule(css, ".promo-banner-btn")
    assert "animation" not in _rule(css, ".promo-banner-btn:hover")
    # The ring still pulses, and the keyframes are the ring's.
    assert "promo-banner-pulse" in _rule(css, ".promo-banner-btn::after")
