"""Run the current six-tab, read-only Playwright verification contract."""
import subprocess
from pathlib import Path

if __name__ == '__main__':
    raise SystemExit(subprocess.call(['node', str(Path(__file__).with_name('test_public.js'))]))
