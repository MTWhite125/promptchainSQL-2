import http.client
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from minidb import Database
from minidb.server import MiniDBServer, TRANSACTION_TIMEOUT


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.path = Path(self.folder.name) / 'database.json'
        self.db = Database(self.path)
        self.server = MiniDBServer(('127.0.0.1', 0), self.db)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': 0.01})
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        self.db.close()
        self.folder.cleanup()

    def request(self, method, path, data=None, client='test-client', token=True, extra=None, raw=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        headers = {'X-MiniDB-Client': client, 'Content-Type': 'application/json'}
        if token:
            headers['X-MiniDB-Token'] = self.server.token
        headers.update(extra or {})
        body = raw if raw is not None else json.dumps(data) if data is not None else None
        conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        content = response.read()
        status = response.status
        conn.close()
        return status, content

    def query(self, sql, client='test-client'):
        status, body = self.request('POST', '/api/query', {'sql': sql}, client=client)
        return status, json.loads(body)

    def test_assets_and_authorization(self):
        status, body = self.request('GET', '/')
        self.assertEqual(status, 200)
        self.assertIn(self.server.token.encode(), body)
        self.assertNotIn(b'__API_TOKEN__', body)
        for path in ('/style.css', '/app.js'):
            self.assertEqual(self.request('GET', path)[0], 200)
        self.assertEqual(self.request('GET', '/api/state', token=False)[0], 403)
        self.assertEqual(self.request('GET', '/api/state', extra={'Host': 'evil.example'})[0], 403)
        self.assertEqual(self.request('POST', '/api/query', {'sql': 'BEGIN'}, extra={'Origin': 'https://evil.example'})[0], 403)
        self.assertEqual(self.request('GET', '/../engine.py')[0], 404)

    def test_crud_results_schema_and_persistence(self):
        self.assertEqual(self.query('CREATE TABLE people (id INTEGER PRIMARY KEY, name TEXT)')[0], 200)
        status, data = self.query("INSERT INTO people VALUES (1, 'Ada')")
        self.assertEqual(data['affected'], 1)
        self.assertEqual(data['state']['tables'][0]['name'], 'people')
        status, data = self.query('SELECT * FROM people')
        self.assertEqual(data['rows'], [{'id': 1, 'name': 'Ada'}])
        self.assertEqual(data['columns'], ['id', 'name'])
        self.assertEqual(self.query('SELECT name FROM people WHERE id = 99')[1]['columns'], ['name'])
        self.assertEqual(self.query("UPDATE people SET name = 'Grace'")[1]['affected'], 1)
        self.assertEqual(json.loads(self.path.read_text())['tables']['people']['rows'][0]['name'], 'Grace')
        self.assertEqual(self.query('DELETE FROM people')[1]['affected'], 1)

    def test_transaction_ownership_and_expiry(self):
        self.query('CREATE TABLE items (id INTEGER)')
        self.query('BEGIN')
        self.query('INSERT INTO items VALUES (1)')
        self.assertEqual(self.query('COMMIT', client='other-tab')[0], 409)
        self.assertEqual(self.request('GET', '/api/state', client='other-tab')[0], 409)
        self.assertEqual(self.query('ROLLBACK')[0], 200)
        self.assertEqual(self.query('SELECT * FROM items')[1]['rows'], [])
        self.query('BEGIN')
        self.query('INSERT INTO items VALUES (2)')
        self.query('COMMIT')
        self.query('BEGIN')
        self.query('INSERT INTO items VALUES (3)')
        self.server.last_activity = time.monotonic() - TRANSACTION_TIMEOUT - 1
        self.assertEqual(self.query('SELECT * FROM items', client='other-tab')[1]['rows'], [{'id': 2}])
        self.assertIsNone(self.server.owner)

    def test_errors_leave_server_usable(self):
        self.assertEqual(self.request('POST', '/api/query', raw='{broken')[0], 400)
        self.assertEqual(self.request('POST', '/api/query', {'sql': 1})[0], 400)
        self.assertEqual(self.request('POST', '/api/query', {'sql': 'BEGIN'}, extra={'Content-Type': 'text/plain'})[0], 415)
        self.assertEqual(self.query('SELECT * FROM missing')[0], 400)
        self.assertEqual(self.request('GET', '/api/state')[0], 200)

    def test_reserved_column_and_empty_results(self):
        self.query('CREATE TABLE words (from TEXT)')
        status, data = self.query('SELECT from FROM words')
        self.assertEqual(status, 200)
        self.assertEqual(data['columns'], ['from'])
        self.assertEqual(data['rows'], [])


if __name__ == '__main__':
    unittest.main()
