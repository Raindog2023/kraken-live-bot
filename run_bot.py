import asyncio
import sys
import os
import signal
from pathlib import Path

# Set working directory
os.chdir(r'C:\Users\Rene\kraken-live-bot')
sys.path.insert(0, r'C:\Users\Rene\kraken-live-bot')

print("Starting Kraken Live Bot server...")
print(f"Python executable: {sys.executable}")
print(f"Working directory: {os.getcwd()}")

# Import app module
import app.main

print("\nAvailable FastAPI routes:")
for route in app.main.app.routes:
    methods = []
    if hasattr(route, 'methods'):
        methods = list(route.methods)
    elif hasattr(route, 'methods'):
        methods = route.methods
    elif hasattr(route, 'methods'):
        methods = route.methods
    print(f"  {route.path} -> {methods}")

# Check if app has routes
print(f"\nApp type: {type(app.main.app)}")

# Import uvicorn and run the app
import uvicorn

if __name__ == "__main__":
    # Print server configuration
    print(f"\nStarting FastAPI server on http://localhost:8000")
    print(f"Documentation: http://localhost:8000/docs")
    print(f"OpenAPI: http://localhost:8000/openapi.json")
    
    # Start the server
    uvicorn.run(
        app.main.app,
        host="127.0.0.1",
        port=8000,
        reload=False,
        access_log=True,
        log_level="info"
    )