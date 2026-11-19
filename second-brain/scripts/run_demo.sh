#!/usr/bin/env bash
# 离线跑一遍 demo + 测试
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m beeagent demo
python3 -m unittest discover -s tests
