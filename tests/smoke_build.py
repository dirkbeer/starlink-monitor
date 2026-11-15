"""Assert that a bundled GUI remains alive and has no startup traceback."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile

binary = Path(sys.argv[1]).resolve()
diagnostics = [binary.parent/'data'/name for name in ('console.log','crash.log')]
offsets = {path: path.stat().st_size if path.exists() else 0 for path in diagnostics}
with tempfile.TemporaryDirectory() as tmp:
    log = Path(tmp) / 'startup.log'
    with log.open('w') as output:
        process = subprocess.Popen([str(binary)], stdout=output, stderr=subprocess.STDOUT,
                                   env={**os.environ, 'STARLINK_EXTERNAL_SERVICES': '0'})
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            if os.name == 'nt':
                subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],
                               capture_output=True, check=False)
            else:
                process.terminate()
            process.wait(timeout=10)
        else:
            raise AssertionError(f'GUI exited during startup: {process.returncode}')
    text = log.read_text(errors='replace')
    for path in diagnostics:
        if path.exists():
            with path.open('rb') as diagnostic:
                diagnostic.seek(offsets[path])
                text += diagnostic.read().decode(errors='replace')
    print(text)
    assert not any(marker in text for marker in
                   ('Traceback', 'Failed to compile proto', 'Failed to execute'))
print('Bundled startup passed')
