#!/bin/bash
# 09:25 启动网格策略
cd /home/administrator/.openclaw/workspace/FlashNewsTrade/grid_trader
python3 -c "
import json
with open('state.json') as f:
    s = json.load(f)
s['enabled'] = True
with open('state.json', 'w') as f:
    json.dump(s, f, indent=2)
print('enabled=True')
"
nohup python3 main.py > /dev/null 2>&1 &
echo "Grid started at $(date)"
