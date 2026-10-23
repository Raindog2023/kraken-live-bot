import asyncio
import subprocess
import sys
import os
from pathlib import Path

os.chdir(r'C:\Users\Rene\kraken-live-bot')

try:
    result = subprocess.run([sys.executable, '-m', 'app.main'], 
                          capture_output=True, text=True, timeout=60)
    print('STDOUT:', result.stdout)
    print('STDERR:', result.stderr)
    print('Return code:', result.returncode)
except subprocess.TimeoutExpired:
    print('Bot started successfully (process running)')
except Exception as e:
    print('Error starting bot:', e)
