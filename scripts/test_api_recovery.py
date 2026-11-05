"""Safe contract checks: no production DB and no external campaign calls."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from src.site_api import app as module


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        # No lifespan/startup: these checks must never migrate or reset a real DB.
        self.client = TestClient(module.app)

    def test_api_address_redirects_to_existing_pages(self):
        response = self.client.get('/', follow_redirects=False)
        self.assertEqual(response.status_code, 307)
        self.assertEqual(response.headers['location'], 'https://profitia.github.io/prasowka-SpendGuru/')

    def test_health_contract_unchanged(self):
        self.assertEqual(self.client.get('/health').json(), {'status': 'ok'})

    def test_pages_origin_can_read_and_preflight(self):
        with patch.object(module, 'load_press_articles', return_value=[{'id': 'test', 'data_quality_status': 'ok'}]):
            response = self.client.get('/api/articles', headers={'Origin': 'https://profitia.github.io'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['access-control-allow-origin'], 'https://profitia.github.io')
        response = self.client.options('/api/articles/contact', headers={
            'Origin': 'https://profitia.github.io', 'Access-Control-Request-Method': 'POST',
            'Access-Control-Request-Headers': 'content-type',
        })
        self.assertEqual(response.status_code, 200)

    def test_contact_write_returns_committed_record(self):
        record = {'tier1_email': 'test@example.invalid', 'apollo_status': 'waiting'}
        with patch.object(module, 'update_tier_email', return_value=record) as update:
            response = self.client.post('/api/articles/contact', json={
                'article_url': 'https://example.invalid/test', 'tier': 'tier_1_c_level', 'email': record['tier1_email'],
            })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), record)
        update.assert_called_once()

    def test_db_unavailable_never_reports_a_successful_write(self):
        with patch.object(module, 'update_tier_email', side_effect=ConnectionError('test unavailable')):
            response = self.client.post('/api/articles/contact', json={
                'article_url': 'https://example.invalid/test', 'tier': 'tier_1_c_level', 'email': 'test@example.invalid',
            })
        self.assertEqual(response.status_code, 503)


if __name__ == '__main__':
    unittest.main()
