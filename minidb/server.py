"""Local HTTP interface to MiniDB, using only the Python standard library."""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import threading
import time
from urllib.parse import urlsplit

from .engine import Database, DatabaseError, Parser

MAX_BODY = 1_000_000
TRANSACTION_TIMEOUT = 300


class MiniDBServer(ThreadingHTTPServer):
    daemon_threads = False

    def __init__(self, address, database):
        self.db = database
        self.token = secrets.token_urlsafe(32)
        self.owner = None
        self.last_activity = 0
        self.db_lock = threading.RLock()
        super().__init__(address, Handler)

    def expire_transaction(self):
        if self.owner and time.monotonic() - self.last_activity > TRANSACTION_TIMEOUT:
            self.db.execute('ROLLBACK')
            self.owner = None

    def state(self):
        return {'database': str(self.db.path) if self.db.path else ':memory:',
                'in_transaction': self.db.in_transaction,
                'tables': [{'name': name, 'columns': self.db.describe_table(name)}
                           for name in self.db.list_tables()]}


class Handler(BaseHTTPRequestHandler):
    def _send(self, status, body, content_type='application/json; charset=utf-8'):
        if not isinstance(body, bytes):
            body = json.dumps(body).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(body)

    def _authorize(self):
        port = self.server.server_port
        host = self.headers.get('Host', '')
        if host not in (f'127.0.0.1:{port}', f'localhost:{port}'):
            self._send(403, {'error': 'Invalid Host header'})
            return False
        origin = self.headers.get('Origin')
        if origin and origin not in (f'http://127.0.0.1:{port}', f'http://localhost:{port}'):
            self._send(403, {'error': 'Cross-origin requests are not allowed'})
            return False
        if not secrets.compare_digest(self.headers.get('X-MiniDB-Token', ''), self.server.token):
            self._send(403, {'error': 'Missing or invalid API token'})
            return False
        client = self.headers.get('X-MiniDB-Client', '')
        if not client or len(client) > 128:
            self._send(400, {'error': 'A client identifier is required'})
            return False
        self.server.expire_transaction()
        if self.server.owner and self.server.owner != client:
            self._send(409, {'error': 'Another browser tab owns the active transaction. Retry after it commits, rolls back, or expires.'})
            return False
        if self.server.owner:
            self.server.last_activity = time.monotonic()
        return True

    def do_GET(self):
        with self.server.db_lock:
            self._get()

    def _get(self):
        path = urlsplit(self.path).path
        if path == '/api/state':
            if self._authorize():
                self._send(200, self.server.state())
            return
        port = self.server.server_port
        if self.headers.get('Host', '') not in (f'127.0.0.1:{port}', f'localhost:{port}'):
            self._send(403, {'error': 'Invalid Host header'})
            return
        assets = {'/': ('index.html', 'text/html; charset=utf-8'),
                  '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
                  '/style.css': ('style.css', 'text/css; charset=utf-8')}
        if path not in assets:
            self._send(404, {'error': 'Not found'})
            return
        filename, content_type = assets[path]
        body = (Path(__file__).parent / 'web' / filename).read_bytes()
        if path == '/':
            body = body.replace(b'__API_TOKEN__', self.server.token.encode())
        self._send(200, body, content_type)

    def do_POST(self):
        with self.server.db_lock:
            self._post()

    def _post(self):
        if urlsplit(self.path).path != '/api/query':
            self._send(404, {'error': 'Not found'})
            return
        if not self._authorize():
            return
        if self.headers.get('Content-Type', '').split(';')[0].strip() != 'application/json':
            self._send(415, {'error': 'Use application/json'})
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= MAX_BODY:
                self._send(413, {'error': 'Request body must be 1–1,000,000 bytes'})
                return
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict) or not isinstance(data.get('sql'), str) or not data['sql'].strip():
                raise ValueError('Supply a nonempty sql string')
            sql = data['sql']
            result = self.server.db.execute(sql)
            if self.server.db.in_transaction:
                self.server.owner = self.headers['X-MiniDB-Client']
                self.server.last_activity = time.monotonic()
            else:
                self.server.owner = None
            response = {'state': self.server.state()}
            if isinstance(result, list):
                parser = Parser(sql)
                parser.expect('SELECT')
                if parser.peek() == '*':
                    parser.pop()
                    parser.expect('FROM')
                    columns = [c['name'] for c in self.server.db.describe_table(parser.identifier())]
                else:
                    columns = [parser.identifier()]
                    while parser.peek() == ',':
                        parser.pop()
                        columns.append(parser.identifier())
                response.update(rows=result, columns=list(dict.fromkeys(columns)))
            else:
                response['affected'] = result
            self._send(200, response)
        except (ValueError, UnicodeError, DatabaseError) as exc:
            self._send(400, {'error': str(exc)})
        except OSError as exc:
            self._send(500, {'error': f'Storage error: {exc}'})

    def setup(self):
        super().setup()
        self.connection.settimeout(10)


def main():
    parser = argparse.ArgumentParser(description='MiniDB local web server')
    parser.add_argument('database', nargs='?', default='database.json')
    parser.add_argument('--port', type=int, default=8000)
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error('port must be between 0 and 65535')
    try:
        with Database(args.database) as db:
            with MiniDBServer(('127.0.0.1', args.port), db) as server:
                print(f'MiniDB: http://127.0.0.1:{server.server_port}', flush=True)
                print('Press Ctrl+C to stop. Uncommitted changes roll back on shutdown.', flush=True)
                try:
                    server.serve_forever()
                except KeyboardInterrupt:
                    pass
        return 0
    except (DatabaseError, OSError) as exc:
        print(f'Cannot start server: {exc}')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
