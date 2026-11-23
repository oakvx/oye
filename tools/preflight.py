"""Windows launch preflight (may also be run from Linux for update tests)."""
from pathlib import Path
import json
import os
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from observer.upgrade import prepare_update

if __name__ == '__main__':
    old = (Path(os.environ['LOCALAPPDATA']) / 'Oye') if os.environ.get('LOCALAPPDATA') else None
    # When users extract the patch over the portable project, ROOT already
    # contains everything. Use %LOCALAPPDATA% only if this folder is empty.
    result = prepare_update(ROOT, previous=old, defaults=ROOT / 'defaults')
    print('OYE UPDATE:', json.dumps(result, ensure_ascii=False))
    if result['training']['status'].startswith('db_error'):
        print('ATTENZIONE: il database di apprendimento esiste ma non si legge. Non e stato modificato.')
