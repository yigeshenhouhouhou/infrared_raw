"""Check the actual HTML served by the app, including its inline JavaScript."""
from pathlib import Path
import shutil
import subprocess

import pytest


def test_frontend_javascript_syntax(tmp_path):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is not installed')
    page = (Path(__file__).resolve().parents[1] / 'ui' / 'index.html').read_text('utf-8')
    script = page.split('<script>', 1)[1].split('</script>', 1)[0]
    source = tmp_path / 'frontend.js'
    source.write_text(script, encoding='utf-8')
    result = subprocess.run([node, '--check', str(source)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
