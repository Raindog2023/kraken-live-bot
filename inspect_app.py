import os, sys, inspect, asyncio, subprocess

os.chdir(r'C:\Users\Rene\kraken-live-bot')
sys.path.insert(0, r'C:\Users\Rene\kraken-live-bot')

import app.main

print('app.main functions:')
for name in dir(app.main):
    if not name.startswith('_'):
        try:
            func = getattr(app.main, name)
            sig = inspect.signature(func)
            print(f'  {name}{sig}')
        except:
            print(f'  {name}: (no signature)')

# Try to find main entry point
if hasattr(app.main, 'main'):
    print('\nmain() found')
elif hasattr(app.main, 'run'):
    print('\nrun() found')
elif hasattr(app.main, 'app'):
    print('\napp (FastAPI) found')

# Check for uvicorn/fastapi app
if hasattr(app.main, 'app'):
    print('FastAPI app exists')

# Check if it has a startup function
for attr in ['start', 'startup', 'run_server', 'serve', 'main']:
    if hasattr(app.main, attr):
        print(f'Has {attr}()')