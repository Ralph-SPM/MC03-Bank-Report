"""Convenience runner for MC03 Automation Portal.

Both commands support automatic hot-reload:
    python run.py
    fastapi dev run.py
"""

import sys
from pathlib import Path

# Ensure src/ is on the Python path
ROOT_DIR = Path(__file__).resolve().parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from mc03.services.web import create_demo_app

# Expose app instance at module level (for fastapi dev / uvicorn)
app = create_demo_app()

if __name__ == "__main__":
    import os
    import uvicorn
    port = int(os.environ.get("MC03_PRIVATE_PORTAL__PORT", os.environ.get("PORT", 8080)))
    uvicorn.run(
        "run:app",
        host="127.0.0.1",
        port=port,
        reload=True,
    )
