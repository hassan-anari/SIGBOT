import unittest
from redis_integration_tests.support import require_safe_test_redis, UnsafeRedisTestConfiguration, create_client
class RedisSafetyTests(unittest.TestCase):
    def test_rejects_missing_default_missing_db_scheme_and_host(self):
        for value in [None,'redis://localhost:6379/0','redis://localhost:6379','http://localhost/15','redis:///15']:
            mapping={} if value is None else {'TEST_REDIS_URL':value}
            with self.subTest(value=value), self.assertRaises(UnsafeRedisTestConfiguration): require_safe_test_redis(mapping)
    def test_accepts_15_and_hides_secret(self):
        safe=require_safe_test_redis({'TEST_REDIS_URL':'redis://user:password@localhost:6379/15'})
        self.assertEqual(safe.database,15); self.assertNotIn('password',repr(safe))
    def test_raw_url_cannot_create_client(self):
        with self.assertRaises(UnsafeRedisTestConfiguration): create_client('redis://localhost/15')
