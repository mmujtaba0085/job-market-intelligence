# scripts/

Permanent, maintained tooling only. Every file here should stay re-runnable
against the current schema and codebase.

If you're writing a one-off script — a backfill you'll run once, a debug
check, a quick test of an API integration — it does **not** belong here
long-term. Write it, run it, then either move it to [`../archive/`](../archive/)
for reference or delete it if it's truly disposable. `scripts/` is not a
place for history; it's a toolbox.

## sys.path bootstrap convention

Files in this directory live one level below the repo root, so anything
that imports from `config` or `src` needs this at the top, before those
imports:

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.markets import TARGET_MARKETS
from src.storage.db import get_connection
```

Any file-relative paths (e.g. loading `.env`) should use
`Path(__file__).resolve().parent.parent / ".env"` for the same reason.
