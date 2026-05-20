#!/usr/bin/env python3
"""One-time migration for legacy output paths and memory files."""

import sys
from pathlib import Path

_root = Path(__file__).parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from server.paths import migrate_legacy_paths


if __name__ == "__main__":
    print("Running path migration...")
    migrate_legacy_paths()
    print("Done.")
