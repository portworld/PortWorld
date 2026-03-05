from __future__ import annotations

import sys
from pathlib import Path

# Ensure `import backend...` resolves from repository root in all pytest runners.
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
