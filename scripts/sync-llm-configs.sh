#!/bin/bash
#
# LLM Config Sync & Cache Flush (All Environments)
#
# Force sync LLM configs to Redis and flush caches without redeploying.
# Affects DEV + PRD + STG simultaneously.
#
# Usage:
#   ./sync-llm-configs.sh                    # Sync all + flush (all envs)
#   ./sync-llm-configs.sh --flush-only       # Just flush caches (all envs)
#   ./sync-llm-configs.sh --pattern='...'    # Flush specific pattern
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

FLUSH_ONLY="false"
INVALIDATE_PATTERN="*"
CONFIG_DIR="$PROJECT_ROOT/config/llm"

# Parse arguments
while [[ $# -gt 0 ]]; do
  case $1 in
    --flush-only) FLUSH_ONLY="true"; shift ;;
    --pattern=*) INVALIDATE_PATTERN="${1#*=}"; shift ;;
    --config-dir=*) CONFIG_DIR="${1#*=}"; shift ;;
    --help|-h)
      echo "LLM Config Sync & Cache Flush (All Environments)"
      echo ""
      echo "Usage: $0 [OPTIONS]"
      echo ""
      echo "Options:"
      echo "  --flush-only             Only invalidate caches, don't sync files"
      echo "  --pattern=PATTERN        Invalidation pattern (default: '*' = all)"
      echo "  --config-dir=DIR         Config directory (default: config/llm)"
      echo ""
      echo "Examples:"
      echo "  $0                                        # Sync all + flush"
      echo "  $0 --flush-only                           # Just flush caches"
      echo "  $0 --flush-only --pattern='hrkg:*'"
      echo ""
      echo "Patterns:"
      echo "  '*'                       All configs"
      echo "  'hrkg:*'                  All HRKG module configs"
      echo "  'hrkg:extraction'         Specific profile"
      echo ""
      echo "NOTE: Affects DEV + PRD + STG simultaneously."
      exit 0
      ;;
    *) echo -e "\033[0;31m[ERROR]\033[0m Unknown option: $1"; exit 1 ;;
  esac
done

if [[ "$FLUSH_ONLY" != "true" && ! -d "$CONFIG_DIR" ]]; then
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

flush_only = '$FLUSH_ONLY' == 'true'
pattern = '$INVALIDATE_PATTERN'
config_dir = Path('$CONFIG_DIR')
dev_redis_password = '$DEV_REDIS_PASSWORD'

# PRD Redis URL (from Doppler prd config)
prd_redis_url = os.environ.get('REDIS_KV_URL', '')

print()
if flush_only:
    print(f'{BLUE}╔══════════════════════════════════════════════════════════════╗{NC}')
    print(f'{BLUE}║{NC}         {CYAN}LLM Config Cache Flush (DEV + PRD + STG){NC}            {BLUE}║{NC}')
    print(f'{BLUE}╚══════════════════════════════════════════════════════════════╝{NC}')
else:
    print(f'{BLUE}╔══════════════════════════════════════════════════════════════╗{NC}')
    print(f'{BLUE}║{NC}        {CYAN}LLM Config Sync & Flush (DEV + PRD + STG){NC}            {BLUE}║{NC}')
    print(f'{BLUE}╚══════════════════════════════════════════════════════════════╝{NC}')
print()

def parse_config_file(file_path: Path, module: str, profile: str, version: int) -> dict:
    \"\"\"Parse YAML config and add metadata.\"\"\"
    with open(file_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)

    # Add metadata
    config['configId'] = f'default:{module}:_:{profile}:v{version}'
    config['scope'] = 'default'
    config['module'] = module
    config['profile'] = profile
    config['version'] = version

    return config

def sync_to_redis(client, config_dir: Path, env_name: str) -> int:
    \"\"\"Sync all config files to Redis.\"\"\"
    # Pattern: {module}/{profile}.v{version}.yaml
    version_pattern = re.compile(r'^(.+)\.v(\d+)$')
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
            module = module[1:]  # Remove leading underscore for default

        filename = yaml_file.stem
        match = version_pattern.match(filename)
        if not match:
            continue

        profile = match.group(1)
        version = int(match.group(2))

        try:
            config = parse_config_file(yaml_file, module, profile, version)
            # Redis key format: llm:{scope}:{module}:{userId}:{profile}:v{version}
            redis_key = f'llm:default:{module}:_:{profile}:v{version}'
            client.setex(redis_key, 86400, json.dumps(config))
            synced += 1
        except Exception as e:
            print(f'{YELLOW}[WARN]{NC} Failed to sync {yaml_file}: {e}')

    return synced

def flush_cache(client, pattern: str, env_name: str) -> int:
    \"\"\"Flush matching LLM config keys.\"\"\"
    cursor = 0
    deleted = 0
    redis_pattern = f'llm:*{pattern}*' if pattern != '*' else 'llm:*'

    while True:
        cursor, keys = client.scan(cursor, match=redis_pattern, count=100)
        if keys:
            client.delete(*keys)
            deleted += len(keys)
        if cursor == 0:
            break

    return deleted

# ============================================================================
# DEV REDIS (Docker: localhost:16379)
# ============================================================================
print(f'{BLUE}━━━ DEV Redis (Docker) ━━━{NC}')

dev_client = None
try:
    dev_redis_url = f'redis://:{dev_redis_password}@localhost:16379/0' if dev_redis_password else 'redis://localhost:16379/0'
    dev_client = redis.from_url(dev_redis_url, decode_responses=True, socket_connect_timeout=5)
    dev_client.ping()
    print(f'{GREEN}[OK]{NC} Dev Redis connected (localhost:16379)')
except Exception as e:
    print(f'{YELLOW}[WARN]{NC} Dev Redis not available: {e}')
    dev_client = None

# Sync to dev if connected and not flush-only
if dev_client and not flush_only:
    print(f'{BLUE}[INFO]{NC} Syncing LLM configs to dev Redis...')
    synced = sync_to_redis(dev_client, config_dir, 'dev')
    print(f'{GREEN}[OK]{NC} Synced {synced} configs to dev Redis')

# Flush dev cache
if dev_client:
    try:
        deleted = flush_cache(dev_client, pattern, 'dev')
        subscribers = dev_client.publish('llm:invalidate', pattern)
        print(f'{GREEN}[OK]{NC} Dev cache flushed: deleted {deleted} keys, published to {subscribers} subscribers')
        dev_client.close()
    except Exception as e:
        print(f'{YELLOW}[WARN]{NC} Dev flush failed: {e}')

print()

# ============================================================================
# PRD + STG REDIS (Upstash - shared)
# ============================================================================
print(f'{BLUE}━━━ PRD + STG Redis (Upstash) ━━━{NC}')

if not prd_redis_url:
    print(f'{RED}[ERROR]{NC} REDIS_KV_URL not found')
    sys.exit(1)

# Mask URL
masked = '***@' + prd_redis_url.split('@')[-1] if '@' in prd_redis_url else prd_redis_url[:20] + '...'
print(f'{BLUE}[INFO]{NC} Redis: {masked}')

try:
    client = redis.from_url(prd_redis_url, decode_responses=True, socket_connect_timeout=10)
    client.ping()
    print(f'{GREEN}[OK]{NC} PRD/STG Redis connected')
except Exception as e:
    print(f'{RED}[ERROR]{NC} Cannot connect: {e}')
    sys.exit(1)

# Sync files if not flush-only
if not flush_only:
    print(f'{BLUE}[INFO]{NC} Syncing LLM configs from: {config_dir}')
    synced = sync_to_redis(client, config_dir, 'prd')
    print(f'{GREEN}[OK]{NC} Synced {synced} configs to PRD/STG Redis')

# Flush and publish invalidation
print(f'{BLUE}[INFO]{NC} Flushing cache: pattern=\"{pattern}\"')
try:
    deleted = flush_cache(client, pattern, 'prd')
    subscribers = client.publish('llm:invalidate', pattern)
    print(f'{GREEN}[OK]{NC} PRD/STG cache flushed: deleted {deleted} keys, published to {subscribers} subscribers')
except Exception as e:
    print(f'{RED}[ERROR]{NC} Failed to flush: {e}')
    sys.exit(1)

print()
print(f'{GREEN}╔══════════════════════════════════════════════════════════════╗{NC}')
print(f'{GREEN}║{NC}                      {CYAN}All Done!{NC}                              {GREEN}║{NC}')
print(f'{GREEN}╚══════════════════════════════════════════════════════════════╝{NC}')
print()
print(f'  {CYAN}Pattern:{NC} {pattern}')
print(f'  {CYAN}Effect:{NC} All DEV + PRD + STG instances will reload configs from file')
print()

client.close()
"
