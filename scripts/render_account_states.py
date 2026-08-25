"""Render every page in all three account states, for the contrast audit.

Run:  .venv/Scripts/python.exe scripts/render_account_states.py
Then: open the dev server and sweep the output with
      scripts/audit_contrast.js, fetching each file with
      {cache: 'reload'} - a cached render will report a fix that is not
      really there, which happened twice during the dark rollout.

Runs against the test client, which conftest points at a throwaway
SQLite file, so signing up a premium user here never touches the real
Neon database that local dev otherwise shares with production.

Writes the finished HTML into app/static/_audit/ so the browser can
fetch each one from the dev server with the real stylesheet attached.
That directory is gitignored.
"""
import os
import pathlib
import sys

ROOT = pathlib.Path("E:/Claude/PropertyValue")
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

# Must import conftest FIRST: it repoints DATABASE_URL at SQLite and
# blanks the third-party keys before app.main is imported.
sys.path.insert(0, str(ROOT / "tests"))
import tests.conftest as conftest  # noqa: F401,E402

from fastapi.testclient import TestClient  # noqa: E402

from app import db, main as app_main  # noqa: E402
from app.models import User  # noqa: E402

OUT = ROOT / "app" / "static" / "_audit"
OUT.mkdir(parents=True, exist_ok=True)
for stale in OUT.glob("*.html"):
    stale.unlink()

PAGES = [
    ("home", "/"),
    ("areas", "/areas"),
    ("area-guide", "/area/M1"),
    ("schools-guide", "/schools/guide?q=M1"),
    ("schools-shortlist", "/schools/shortlist"),
    ("buying-guide", "/buying-guide"),
    ("extension", "/browser-extension"),
    ("pricing", "/premium"),
    ("accuracy", "/accuracy"),
    ("data", "/data"),
    ("methodology", "/methodology"),
    ("support", "/support"),
    ("terms", "/terms"),
    ("privacy", "/privacy"),
    ("market-report", "/market-report"),
    ("report", "/property?postcode=M14%205TG"),
    ("comparables", "/property/comparables?postcode=M14%205TG"),
    ("watchlist", "/watchlist"),
    ("compare", "/watchlist/compare"),
    ("login", "/login"),
    ("signup", "/signup"),
    ("forgot", "/forgot-password"),
    ("notfound", "/no-such-page"),
]


def install_report_fakes(monkey):
    """The report fans out to ~30 upstream services. Same fakes the test
    suite uses, so the page renders its real shape without network."""
    location = conftest.fake_location()
    gather = conftest.fake_gather()

    async def _lookup(_postcode):
        return location

    async def _gather(_location, _house_number, _premium_unlocked):
        # Mirrors what the real gather always puts in its result.
        return {"location": _location, "epc_configured": True, **gather}

    monkey.setattr(app_main, "lookup_postcode", _lookup)
    monkey.setattr(app_main, "_full_property_gather", _gather)


def render_all(client, label):
    written = []
    for name, path in PAGES:
        # A real browser gets the interim "building your report" page;
        # a crawler UA gets the finished render, which is what we want
        # to audit.
        r = client.get(path, headers={"User-Agent": "Googlebot/2.1"})
        html = r.text
        (OUT / f"{label}--{name}.html").write_text(html, encoding="utf-8")
        written.append((f"{label}--{name}", r.status_code, len(html)))
    return written


def main():
    from _pytest.monkeypatch import MonkeyPatch

    monkey = MonkeyPatch()
    with TestClient(app_main.app) as client:
        install_report_fakes(monkey)

        rows = []
        # 1. anonymous: no account, every Premium card locked
        rows += render_all(client, "anon")

        # 2. signed up, free: three full reports, no card
        client.post("/signup", data={"email": "free@example.com", "password": "test-password-1"})
        rows += render_all(client, "free")
        client.get("/logout")

        # 3. subscribed: is_premium is what the Stripe webhook sets
        client.post("/signup", data={"email": "prem@example.com", "password": "test-password-1"})
        with db.get_session() as s:
            u = s.query(User).filter(User.email == "prem@example.com").one()
            u.is_premium = True
            u.subscription_status = "active"
            u.plan = "monthly"
            s.commit()
        rows += render_all(client, "premium")

    monkey.undo()
    bad = [r for r in rows if r[1] >= 500]
    for name, status, size in rows:
        flag = "  <-- SERVER ERROR" if status >= 500 else ""
        print(f"  {name:<28} {status}  {size // 1024:>5} KiB{flag}")
    print(f"\n{len(rows)} pages written to {OUT}")
    print("server errors:", len(bad))


if __name__ == "__main__":
    main()
