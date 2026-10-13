import os
import sys
import tempfile
from pathlib import Path

# Point the app at a throwaway data folder before tracker.config is imported.
os.environ["TRACKER_DATA_DIR"] = tempfile.mkdtemp(prefix="lax_test_")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
