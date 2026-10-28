"""Run the current backend regressions; temporary files remain on E:."""
from pathlib import Path
import os
import sys
root = Path(__file__).resolve().parent
os.environ["TEMP"] = str(root / "runtime" / "tmp")
os.environ["TMP"] = os.environ["TEMP"]
if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([str(root / "tests"), "--basetemp=" + str(root / "runtime" / "pytest"), "-q", "-p", "no:cacheprovider"]))
