from __future__ import annotations

import logging
import tempfile
import unittest
from pathlib import Path

from logging_config import LOGGER_NAME, setup_logging


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
