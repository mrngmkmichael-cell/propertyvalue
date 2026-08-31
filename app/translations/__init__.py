"""Interface translations, one module per language, keyed by English.

Keyed by the English source text rather than by an invented key like
"report.share.button". Three reasons that matters here:

  * A missing entry falls back to the English source automatically, so
    a half-translated language is a usable page rather than a page full
    of blanks or key names.
  * The files are reviewable by someone who does not read code. Every
    line is "English": "translation", which is what you hand to a
    native speaker.
  * There is no way for a key to drift from the text it names, which is
    the usual way translation files rot.

The cost is that editing the English in a template orphans its
translations. scripts/i18n_report.py finds orphans, and the fallback
means an orphan degrades to English rather than breaking the page.
"""
import importlib

# Language code -> module name under this package. Hyphens are not legal
# in module names, so they become underscores.
_MODULES = {
    "zh-hant": "zh_hant",
    "zh-hans": "zh_hans",
    "hi": "hi",
    "es": "es",
    "ar": "ar",
    "fr": "fr",
    "ja": "ja",
    "ko": "ko",
}

_loaded: dict[str, dict] = {}


def catalogue(lang: str) -> dict:
    """Every translated string for one language. Loaded once, then
    cached: these are plain dict literals, so importing is cheap, but
    it should not happen per request."""
    if lang in _loaded:
        return _loaded[lang]
    module_name = _MODULES.get(lang)
    if module_name is None:
        _loaded[lang] = {}
        return _loaded[lang]
    try:
        module = importlib.import_module(f"{__name__}.{module_name}")
        # TEXT holds template literals: every key must appear inside a
        # tr("...") call somewhere, and a test enforces that. VOCAB
        # holds data vocabularies, the finite sets of words that arrive
        # from datasets rather than templates (Ofsted's rating words,
        # school phases and genders, radon and flood labels) and reach
        # the page through the trd filter or tr(variable). Their keys
        # exist in no template, so the orphan test checks TEXT only.
        table = {**getattr(module, "TEXT", {}), **getattr(module, "VOCAB", {})}
    except ImportError:
        # A language whose file has not been written yet reads as
        # untranslated, which renders in English. Never an error page.
        table = {}
    _loaded[lang] = table
    return table


def translate(source: str, lang: str) -> str:
    if not source or not lang or lang == "en":
        return source
    return catalogue(lang).get(source, source)
