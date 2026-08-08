from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv

# Ensure `import src...` works when pytest is run from the repo root.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Loads .env for local runs (e.g. STRIPE_SECRET_KEY for the live integration
# test). No-op in CI, where no .env file exists, so the integration test
# continues to self-skip there via pytest.mark.skipif.
load_dotenv(ROOT / ".env")
