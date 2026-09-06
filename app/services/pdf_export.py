"""HTML-to-PDF via xhtml2pdf (pure Python, no native/system libraries -
unlike WeasyPrint or a headless-browser renderer, both of which need
build tooling this app's Render deployment doesn't have configured for).
xhtml2pdf's CSS support is limited to basic box-model properties - no
flexbox/grid - so app/templates/pdf_report_full.html is deliberately a
separate, simpler template rather than a reuse of the site's own CSS.
"""
import functools
import sys
import tempfile
import threading
import types
from io import BytesIO

from xhtml2pdf import files as _xhtml2pdf_files
from xhtml2pdf import pisa

if sys.platform == "win32":
    # xhtml2pdf copies each @font-face file into a NamedTemporaryFile and
    # hands its name to reportlab while the handle is still open, which
    # Windows refuses. Linux (Render) allows it. The substitute is scoped
    # to xhtml2pdf's own module, not the process: pytest and everything
    # else keep the real tempfile. Dev only, and the files are small.
    _xhtml2pdf_files.tempfile = types.SimpleNamespace(
        NamedTemporaryFile=functools.partial(tempfile.NamedTemporaryFile, delete=False),
    )


PDF_THREAD_STACK_BYTES = 128 * 1024 * 1024


def html_to_pdf(html: str) -> bytes | None:
    """Renders on a thread with a deep stack. xhtml2pdf walks the document
    recursively and the full report nests tables several levels deep; on
    a default-sized stack that walk hit an access violation about one
    run in three (Windows, 6 Sep 2026). A generous stack makes it
    deterministic. The caller should hand this to asyncio.to_thread so
    the join does not block the event loop."""
    out: dict = {}

    def _render() -> None:
        buffer = BytesIO()
        try:
            result = pisa.CreatePDF(html, dest=buffer)
            out["pdf"] = None if result.err else buffer.getvalue()
        except Exception as exc:  # pragma: no cover - surfaced to the caller as None
            out["error"] = exc
            out["pdf"] = None

    previous = threading.stack_size()
    threading.stack_size(PDF_THREAD_STACK_BYTES)
    try:
        worker = threading.Thread(target=_render, name="pdf-render", daemon=True)
        worker.start()
    finally:
        threading.stack_size(previous)
    worker.join()
    return out.get("pdf")
