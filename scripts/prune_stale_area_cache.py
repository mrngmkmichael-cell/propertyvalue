"""Delete area-guide cache rows left behind by an older payload version.

AREA_GUIDE_PAYLOAD_VERSION is part of the cache key, so bumping it (which
is how a new field is made to appear on already-cached districts) orphans
every row written under the previous number. Those rows are never read
again, because nothing will ever ask for that key, and the TTL only
expires an entry when something reads it. They just sit there.

Safe to run any time: these are cache entries and regenerate on demand
or through the prewarm job. Only area_guide rows from other versions are
touched, and only when they do not match the current one.

    .venv/Scripts/python.exe scripts/prune_stale_area_cache.py [--dry-run]
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import select  # noqa: E402

from app.db import get_session  # noqa: E402
from app.main import AREA_GUIDE_PAYLOAD_VERSION as CURRENT  # noqa: E402
from app.models import PageCache  # noqa: E402

KEY_RE = re.compile(r"^area_guide:(\d+):")
LEGACY_RE = re.compile(r"^area_guide:[A-Z]")   # the original unversioned keys


def main():
    dry = "--dry-run" in sys.argv
    with get_session() as session:
        rows = session.scalars(select(PageCache)).all()

        stale = []
        for row in rows:
            key = row.cache_key or ""
            m = KEY_RE.match(key)
            if m and int(m.group(1)) != CURRENT:
                stale.append(row)
            elif LEGACY_RE.match(key):
                stale.append(row)

        keep = sum(1 for r in rows if (r.cache_key or "").startswith(f"area_guide:{CURRENT}:"))
        print(f"current version   : v{CURRENT}")
        print(f"rows on it        : {keep}")
        print(f"superseded rows   : {len(stale)}")
        if dry:
            print("\n--dry-run, nothing deleted")
            return
        for row in stale:
            session.delete(row)
        session.commit()
        print(f"\ndeleted {len(stale)} superseded rows")


if __name__ == "__main__":
    main()
