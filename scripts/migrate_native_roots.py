"""Run only against an offline snapshot; never modifies the source."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from jmbot.native_migration import migrate_native_roots

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--source', required=True)
parser.add_argument('--target', required=True)
parser.add_argument('--account', required=True)
parser.add_argument('--offline-snapshot', action='store_true', required=True)
args = parser.parse_args()
print(json.dumps(migrate_native_roots(args.source, args.target, args.account), ensure_ascii=False, indent=2))
