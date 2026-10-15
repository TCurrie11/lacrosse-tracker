"""
pytest setup: point the app at a throwaway data folder before tracker.config is imported.

Version history
---------------
1.0.0  2026-10-13  Initial release.
1.1.0  2026-10-15  Readability pass: file header.
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ["TRACKER_DATA_DIR"] = tempfile.mkdtemp(prefix="lax_test_")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
