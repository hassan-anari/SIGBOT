from __future__ import annotations

import logging
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from logging_config import LOGGER_NAME, setup_logging
from market import MarketProbe
from models import ExchangeStatus


class LoggingTests(unittest.TestCase):
    def tearDown(self) -> None:
        logger = logging.getLogger(LOGGER_NAME)
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            handler.close()

    def test_writes_persistent_log_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = setup_logging(
                Path(temp_dir),
                max_bytes=1024,
                backup_count=2,
            )
            logger = logging.getLogger(f"{LOGGER_NAME}.test")
            logger.info("logging-test-message")

            for handler in logging.getLogger(LOGGER_NAME).handlers:
                handler.flush()

            self.assertTrue(log_path.exists())
            self.assertIn(
                "logging-test-message",
                log_path.read_text(encoding="utf-8"),
            )

    def test_health_transitions_log_once_and_recovery_duration(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = setup_logging(Path(temp_dir))
            probe = MarketProbe()
            now = datetime(2026, 1, 1, tzinfo=timezone.utc)
            probe._set_health("ourbit", ExchangeStatus.DEGRADED, now, expected_count=2, received_count=1, missing_count=1)
            probe._set_health("ourbit", ExchangeStatus.DEGRADED, now + timedelta(seconds=5), expected_count=2, received_count=1, missing_count=1)
            probe._set_health("ourbit", ExchangeStatus.HEALTHY, now + timedelta(seconds=10), expected_count=2, received_count=2, missing_count=0)
            for handler in logging.getLogger(LOGGER_NAME).handlers:
                handler.flush()
            content = log_path.read_text(encoding="utf-8")
            self.assertEqual(content.count("current=degraded"), 1)
            self.assertIn("exchange recovered", content)
            self.assertIn("unhealthy_seconds=10.0", content)
