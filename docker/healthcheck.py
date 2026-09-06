import os, sys, urllib.request
url=f"http://127.0.0.1:{os.getenv('PORT','8000')}/health"
try:
    with urllib.request.urlopen(url, timeout=4) as response:
        if response.status != 200: raise RuntimeError(response.status)
except Exception as exc:
    print(f"healthcheck failed: {exc}", file=sys.stderr); raise SystemExit(1)
print("ok")
