import unittest

from integration_tests.support import UnsafeIntegrationDatabaseError, require_safe_test_database


class IntegrationSafetyTests(unittest.TestCase):
    def test_valid_disposable_url(self) -> None:
        safe = require_safe_test_database({
            "TEST_DATABASE_URL": "postgresql+psycopg://user:password@localhost/app_test"
        })
        self.assertEqual(safe.url.database, "app_test")

    def test_rejects_missing_wrong_driver_empty_and_unsafe_name(self) -> None:
        values = ({},
            {"TEST_DATABASE_URL": "sqlite:///app_test"},
            {"TEST_DATABASE_URL": "postgresql+psycopg://user:password@localhost"},
            {"TEST_DATABASE_URL": "postgresql+psycopg://user:password@localhost/app"})
        for mapping in values:
            with self.subTest(mapping=mapping), self.assertRaises(UnsafeIntegrationDatabaseError):
                require_safe_test_database(mapping)

    def test_errors_do_not_reveal_password(self) -> None:
        with self.assertRaises(UnsafeIntegrationDatabaseError) as caught:
            require_safe_test_database({
                "TEST_DATABASE_URL": "postgresql://user:supersecret@localhost/app_test"
            })
        self.assertNotIn("supersecret", str(caught.exception))

    def test_equal_urls_allowed_only_after_test_suffix_validation(self) -> None:
        url = "postgresql+psycopg://user:password@localhost/app_test"
        self.assertEqual(require_safe_test_database({"TEST_DATABASE_URL": url, "DATABASE_URL": url}).url.database, "app_test")
