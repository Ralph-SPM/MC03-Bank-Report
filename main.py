"""Main application entry point for the FastAPI CLI.

Allows running directly with:
    fastapi dev
    fastapi run
    uvicorn main:app --reload
"""

import sys
from pathlib import Path

# Ensure src/ is on the path so mc03 package resolves
ROOT_DIR = Path(__file__).resolve().parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from mc03.services.web import create_demo_app

# FastAPI CLI looks for 'app' by default in main.py
app = create_demo_app()
