"""Apply migrations and seed SAMPLE data. Equivalent to `alembic upgrade head && python -m app.db.seed`."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.migrate import upgrade_to_head
from app.db.seed import main

if __name__ == "__main__":
    upgrade_to_head()
    main()
