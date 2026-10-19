import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize('module', ['app.language_profiles', 'app.check_update'])
def test_early_import_does_not_require_the_initialized_user_agent(tmp_path: Path, module: str) -> None:
    root = Path(__file__).resolve().parents[2]
    environment = {key: value for key, value in os.environ.items()
                   if key not in ('SZ_USER_AGENT', 'BAZARR_VERSION')}
    environment['NO_CLI'] = 'true'
    program = '''
import importlib
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path.cwd() / 'bazarr'))
import app.libs
from app.get_args import args
args.config_dir = sys.argv[2]
for name in ('config', 'db'):
    Path(args.config_dir, name).mkdir(parents=True, exist_ok=True)
importlib.import_module(sys.argv[1])
assert 'SZ_USER_AGENT' not in os.environ
assert 'init' not in sys.modules
from app.language_profiles import ProfileItem
assert ProfileItem(1, 'en', hi='excluded').wire()['hi'] == 'Excluded'
'''
    result = subprocess.run([sys.executable, '-c', program, module, str(tmp_path)], cwd=root,
                            env=environment, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
