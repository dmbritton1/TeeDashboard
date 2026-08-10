"""Set the dashboard password before any test module imports main.

main.py exits at import time when DASHBOARD_PASSWORD is unset, and its
load_dotenv() call does not override values already in the environment, so
this stays deterministic even on a machine with a real password in .env.
"""
import os

os.environ["DASHBOARD_PASSWORD"] = "test-password"
