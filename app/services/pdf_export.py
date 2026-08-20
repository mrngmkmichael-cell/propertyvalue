"""HTML-to-PDF via xhtml2pdf (pure Python, no native/system libraries -
unlike WeasyPrint or a headless-browser renderer, both of which need
build tooling this app's Render deployment doesn't have configured for).
xhtml2pdf's CSS support is limited to basic box-model properties - no
flexbox/grid - so app/templates/pdf_report.html is deliberately a
separate, simpler template rather than a reuse of the site's own CSS.
"""
from io import BytesIO

from xhtml2pdf import pisa


def html_to_pdf(html: str) -> bytes | None:
    buffer = BytesIO()
    result = pisa.CreatePDF(html, dest=buffer)
    if result.err:
        return None
    return buffer.getvalue()
