#!/bin/bash
#
# LLM Config Sync (All Environments)
#
# Sync LLM configs to Redis and publish invalidation.
# Affects DEV + PRD + STG simultaneously.
#
# Usage:
#   ./sync-llm-configs.sh
#

set -euo pipefail

# Resolve symlink to get actual script location
SCRIPT_PATH="${BASH_SOURCE[0]}"
while [ -L "$SCRIPT_PATH" ]; do
  SCRIPT_DIR="$( cd -P "$( dirname "$SCRIPT_PATH" )" && pwd )"
  SCRIPT_PATH="$(readlink "$SCRIPT_PATH")"
  [[ $SCRIPT_PATH != /* ]] && SCRIPT_PATH="$SCRIPT_DIR/$SCRIPT_PATH"
done
SCRIPT_DIR="$( cd -P "$( dirname "$SCRIPT_PATH" )" && pwd )"
PROJECT_ROOT="$( cd "$SCRIPT_DIR/../../.." && pwd )"

CONFIG_DIR="$PROJECT_ROOT/config/llm"

if [[ "${1:-}" == "--help" ]] || [[ "${1:-}" == "-h" ]]; then
  echo "LLM Config Sync (All Environments)"
  echo ""
  echo "Usage: $0"
  echo ""
  echo "Syncs all LLM configs from config/llm to Redis and publishes"
  echo "invalidation so all services reload fresh configs."
  echo ""
  echo "NOTE: Affects DEV + PRD + STG simultaneously."
  exit 0
fi

if [[ ! -d "$CONFIG_DIR" ]]; then
  echo -e "\033[0;31m[ERROR]\033[0m Config directory not found: $CONFIG_DIR"
  exit 1
fi

cd "$PROJECT_ROOT"

# Get dev Redis password from Doppler
DEV_REDIS_PASSWORD=$(doppler secrets get REDIS_PASSWORD --config dev --plain 2>/dev/null || echo "")

exec doppler run --config prd -- uv run python -c "
import os
import sys
import re
import json
import redis
from pathlib import Path

try:
    import yaml
except ImportError:
    print('Installing PyYAML...')
    import subprocess
    subprocess.check_call([sys.executable, '-m', 'pip', 'install', 'pyyaml', '-q'])
    import yaml

RED = '\033[0;31m'
GREEN = '\033[0;32m'
BLUE = '\033[0;34m'
CYAN = '\033[0;36m'
YELLOW = '\033[1;33m'
NC = '\033[0m'

config_dir = Path('$CONFIG_DIR')
dev_redis_password = '$DEV_REDIS_PASSWORD'
prd_redis_url = os.environ.get('REDIS_KV_URL', '')

print()
print(f'{BLUE}╔══════════════════════════════════════════════════════════════╗{NC}')
print(f'{BLUE}║{NC}           {CYAN}LLM Config Sync (DEV + PRD + STG){NC}                 {BLUE}║{NC}')
print(f'{BLUE}╚══════════════════════════════════════════════════════════════╝{NC}')
print()

def parse_config_file(file_path: Path, module: str, profile: str, version: int) -> dict:
    with open(file_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
    config['configId'] = f'default:{module}:_:{profile}:v{version}'
    config['scope'] = 'default'
    config['module'] = module
    config['profile'] = profile
    config['version'] = version
    return config

def sync_to_redis(client, config_dir: Path) -> int:
    version_pattern = re.compile(r'^(.+)\.v(\d+)\$')
    synced = 0
    for yaml_file in config_dir.rglob('*.yaml'):
        if '.backup' in yaml_file.parts:
            continue
        rel_path = yaml_file.relative_to(config_dir)
        parts = rel_path.parts
        if len(parts) < 2:
            continue
        module = parts[0]
        if module.startswith('_'):
            module = module[1:]
        filename = yaml_file.stem
        match = version_pattern.match(filename)
        if not match:
            continue
        profile = match.group(1)
        version = int(match.group(2))
        try:
            config = parse_config_file(yaml_file, module, profile, version)
            redis_key = f'llm:default:{module}:_:{profile}:v{version}'
            client.setex(redis_key, 86400, json.dumps(config))
            synced += 1
        except Exception as e:
            print(f'{YELLOW}[WARN]{NC} Failed to sync {yaml_file}: {e}')
    return synced

# ============================================================================
# DEV REDIS
# ============================================================================
print(f'{BLUE}━━━ DEV Redis (Docker) ━━━{NC}')
dev_client = None
try:
    dev_redis_url = f'redis://:{dev_redis_password}@localhost:16379/0' if dev_redis_password else 'redis://localhost:16379/0'
    dev_client = redis.from_url(dev_redis_url, decode_responses=True, socket_connect_timeout=5)
    dev_client.ping()
    print(f'{GREEN}[OK]{NC} Connected')
except Exception as e:
    print(f'{YELLOW}[WARN]{NC} Not available: {e}')

if dev_client:
    synced = sync_to_redis(dev_client, config_dir)
    subscribers = dev_client.publish('llm:invalidate', '*')
    print(f'{GREEN}[OK]{NC} Synced {synced} configs, invalidated ({subscribers} subscribers)')
    dev_client.close()

print()

# ============================================================================
# PRD + STG REDIS
# ============================================================================
print(f'{BLUE}━━━ PRD + STG Redis (Upstash) ━━━{NC}')
if not prd_redis_url:
    print(f'{RED}[ERROR]{NC} REDIS_KV_URL not found')
    sys.exit(1)

masked = '***@' + prd_redis_url.split('@')[-1] if '@' in prd_redis_url else prd_redis_url[:20] + '...'
print(f'{BLUE}[INFO]{NC} Redis: {masked}')

try:
    client = redis.from_url(prd_redis_url, decode_responses=True, socket_connect_timeout=10)
    client.ping()
    print(f'{GREEN}[OK]{NC} Connected')
except Exception as e:
    print(f'{RED}[ERROR]{NC} Cannot connect: {e}')
    sys.exit(1)

synced = sync_to_redis(client, config_dir)
subscribers = client.publish('llm:invalidate', '*')
print(f'{GREEN}[OK]{NC} Synced {synced} configs, invalidated ({subscribers} subscribers)')
client.close()

print()
print(f'{GREEN}╔══════════════════════════════════════════════════════════════╗{NC}')
print(f'{GREEN}║{NC}                      {CYAN}All Done!{NC}                              {GREEN}║{NC}')
print(f'{GREEN}╚══════════════════════════════════════════════════════════════╝{NC}')
print()
"
