"""Send synthetic images/text to one explicitly selected provider."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from jmbot.model_routes import ModelRoutes
from jmbot.model_probe import probe_route

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--routes', required=True)
parser.add_argument('--provider', required=True)
parser.add_argument('--key-env', required=True, help='Environment variable containing the selected provider key')
args = parser.parse_args()
document = ModelRoutes(args.routes).read()['document']
ModelRoutes.validate(document)
async def main():
    async with httpx.AsyncClient(timeout=45, trust_env=False, proxy=os.environ.get('JM_BOT_CHAT_PROXY') or None) as client:
        print(json.dumps(await probe_route(document['providers'][args.provider], os.environ[args.key_env], client), ensure_ascii=False, indent=2))
asyncio.run(main())
