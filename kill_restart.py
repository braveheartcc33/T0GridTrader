#!/usr/bin/env python3
import os, signal, subprocess, sys

# Kill all grid trader processes
result = subprocess.run(['pgrep', '-f', 'grid_trader.*main.py'], capture_output=True, text=True)
pids = [int(x) for x in result.stdout.strip().split('\n') if x]
print('Grid PIDs to kill:', pids)
for pid in pids:
    try:
        os.kill(pid, signal.SIGKILL)
        print(f'Killed {pid}')
    except Exception as e:
        print(f'Failed to kill {pid}: {e}')

# Write enabled=true to state.json
import json
state_path = '/home/administrator/.openclaw/workspace/FlashNewsTrade/grid_trader/state.json'
with open(state_path) as f:
    state = json.load(f)
state['enabled'] = True
with open(state_path, 'w') as f:
    json.dump(state, f, indent=2)
print(f'State updated: enabled={state.get("enabled")}')

# Restart grid
os.chdir('/home/administrator/.openclaw/workspace/FlashNewsTrade/grid_trader')
pid = subprocess.Popen([sys.executable, 'main.py'], 
                       stdout=open('grid_trader.log', 'a'),
                       stderr=subprocess.STDOUT).pid
print(f'Started grid PID: {pid}')
