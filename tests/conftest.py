"""Pytest isolation: keep the test suite out of the PRODUCTION log file.

Importing any goldtrader module runs `log = get_logger(...)` at module load, which calls
setup_logging() and attaches a RotatingFileHandler to logs/goldtrader.jsonl — the live log the
dashboard streams. Without this, every test's events (circuit-breaker failures, synthetic
orders, settings writes) get written into the live log and alarm anyone watching the dashboard.

Setting this env var BEFORE the first goldtrader import makes setup_logging() skip the file
handler for the whole test session. conftest.py is imported before any test module, so this
runs first.
"""
import os

os.environ.setdefault("GOLDTRADER_LOG_NO_FILE", "1")
