"""A small relational database with atomic JSON storage and a SQL subset."""
from __future__ import annotations

import copy
import json
import math
import os
from pathlib import Path
import re
import tempfile
from contextlib import contextmanager
import fcntl


class DatabaseError(Exception):
    """Invalid query, schema, or stored database."""


_TOKEN = re.compile(r"\s*(?:('(?:''|[^'])*')|([A-Za-z_][A-Za-z_0-9]*)|(-?\d+(?:\.\d+)?)|(<=|>=|!=|<>|[(),;*=<>]))")


def tokenize(sql):
    tokens, pos = [], 0
    while pos < len(sql.rstrip()):
        match = _TOKEN.match(sql, pos)
        if not match:
            raise DatabaseError(f"Unexpected SQL near {sql[pos:pos + 30]!r}")
        tokens.append(next(part for part in match.groups() if part is not None))
        pos = match.end()
    if tokens and tokens[-1] == ';':
        tokens.pop()
    return tokens


class Parser:
    def __init__(self, sql):
        self.tokens = tokenize(sql)
        self.pos = 0

    def peek(self):
        return self.tokens[self.pos].upper() if self.pos < len(self.tokens) else ''

    def pop(self):
        if self.pos >= len(self.tokens):
            raise DatabaseError('Unexpected end of SQL')
        token = self.tokens[self.pos]
        self.pos += 1
        return token

    def expect(self, token):
        if self.pop().upper() != token:
            raise DatabaseError(f'Expected {token}')

    def identifier(self):
        token = self.pop()
        if not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*', token):
            raise DatabaseError(f'Invalid identifier: {token}')
        return token.lower()

    def literal(self):
        token = self.pop()
        if token.startswith("'"):
            return token[1:-1].replace("''", "'")
        if token.upper() == 'NULL':
            return None
        if re.fullmatch(r'-?\d+(?:\.\d+)?', token):
            return float(token) if '.' in token else int(token)
        raise DatabaseError(f'Expected a literal, got {token}')

    def done(self):
        if self.pos != len(self.tokens):
            raise DatabaseError(f'Unexpected token: {self.tokens[self.pos]}')


class Database:
    """One connection; use separate connections in separate threads.

    Disk connections take an exclusive advisory lock for their lifetime.
    A second connection to the same file fails immediately instead of risking
    lost writes. In-memory connections use path=None.
    """
    def __init__(self, path=None):
        self.path = Path(path).resolve() if path is not None else None
        self._lock = None
        self._closed = False
        self._snapshot = None
        self._tables = {}
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._lock = open(str(self.path) + '.lock', 'a+')
            try:
                fcntl.flock(self._lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                self._lock.close()
                raise DatabaseError('Database is already open by another connection') from exc
            try:
                if self.path.exists():
                    data = json.loads(self.path.read_text())
                    if data.get('version') != 1 or not isinstance(data.get('tables'), dict):
                        raise DatabaseError('Unsupported database format')
                    self._tables = data['tables']
                    for table in self._tables.values():
                        self._validate(table)
            except Exception as exc:
                self.close()
                raise DatabaseError(f'Cannot load database: {exc}') from exc

    def __enter__(self):
        self._check()
        return self

    def __exit__(self, *args):
        self.close()

    def _check(self):
        if self._closed:
            raise DatabaseError('Connection is closed')

    @property
    def in_transaction(self):
        self._check()
        return self._snapshot is not None

    def list_tables(self):
        """Return sorted table names without exposing mutable storage."""
        self._check()
        return sorted(self._tables)

    def describe_table(self, name):
        """Return a detached copy of a table's column definitions."""
        self._check()
        name = name.lower()
        if name not in self._tables:
            raise DatabaseError(f'Unknown table: {name}')
        return copy.deepcopy(self._tables[name]['columns'])

    def close(self):
        if self._snapshot is not None:
            self._tables = self._snapshot
            self._snapshot = None
        if self._lock and not self._lock.closed:
            self._lock.close()
        self._closed = True

    def _save(self):
        if self.path is None:
            return
        payload = json.dumps({'version': 1, 'tables': self._tables}, allow_nan=False)
        fd, temporary = tempfile.mkstemp(prefix=self.path.name + '.', dir=self.path.parent)
        try:
            with os.fdopen(fd, 'w') as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    @staticmethod
    def _validate(table):
        columns = table['columns']
        if not columns or len({c['name'] for c in columns}) != len(columns):
            raise DatabaseError('Columns must have unique names')
        if sum(c['primary'] for c in columns) > 1:
            raise DatabaseError('Only one primary key is supported')
        seen = set()
        for col in columns:
            if col['type'] not in ('INTEGER', 'REAL', 'TEXT'):
                raise DatabaseError(f"Unsupported type: {col['type']}")
        for row in table['rows']:
            if set(row) != {c['name'] for c in columns}:
                raise DatabaseError('Row does not match schema')
            for col in columns:
                value = row[col['name']]
                if value is None:
                    if col['primary'] or col['required']:
                        raise DatabaseError(f"{col['name']} cannot be NULL")
                    continue
                valid = {'INTEGER': type(value) is int,
                         'REAL': type(value) in (int, float),
                         'TEXT': type(value) is str}[col['type']]
                if not valid or (type(value) is float and not math.isfinite(value)):
                    raise DatabaseError(f"Invalid value for {col['name']} ({col['type']})")
                if col['primary']:
                    if value in seen:
                        raise DatabaseError('Duplicate primary key')
                    seen.add(value)

    @contextmanager
    def transaction(self):
        self.execute('BEGIN')
        try:
            yield self
            self.execute('COMMIT')
        except BaseException:
            if self._snapshot is not None:
                self.execute('ROLLBACK')
            raise

    def execute(self, sql):
        """Execute one statement. SELECT returns dict rows; writes return a count."""
        self._check()
        parser = Parser(sql)
        command = parser.pop().upper()
        if command in ('BEGIN', 'COMMIT', 'ROLLBACK'):
            parser.done()
            if command == 'BEGIN':
                if self._snapshot is not None:
                    raise DatabaseError('Transaction already active')
                self._snapshot = copy.deepcopy(self._tables)
            else:
                if self._snapshot is None:
                    raise DatabaseError('No active transaction')
                if command == 'ROLLBACK':
                    self._tables = self._snapshot
                else:
                    self._save()
                self._snapshot = None
            return 0
        before = copy.deepcopy(self._tables)
        try:
            result = self._statement(command, parser)
            parser.done()
            if command != 'SELECT':
                for table in self._tables.values():
                    self._validate(table)
                if self._snapshot is None:
                    self._save()
            return result
        except (KeyError, TypeError, ValueError) as exc:
            self._tables = before
            raise DatabaseError(str(exc)) from exc
        except BaseException:
            self._tables = before
            raise

    def _table(self, parser):
        name = parser.identifier()
        if name not in self._tables:
            raise DatabaseError(f'Unknown table: {name}')
        return self._tables[name]

    def _column(self, parser, table):
        name = parser.identifier()
        if name not in {c['name'] for c in table['columns']}:
            raise DatabaseError(f'Unknown column: {name}')
        return name

    def _where(self, parser, table):
        clauses = []
        if parser.peek() == 'WHERE':
            parser.pop()
            while True:
                col = self._column(parser, table)
                op = parser.pop().upper()
                if op == 'IS':
                    negate = parser.peek() == 'NOT'
                    if negate:
                        parser.pop()
                    parser.expect('NULL')
                    clauses.append((col, 'IS NOT' if negate else 'IS', None))
                else:
                    if op not in ('=', '!=', '<>', '<', '>', '<=', '>='):
                        raise DatabaseError('Unsupported comparison')
                    clauses.append((col, op, parser.literal()))
                if parser.peek() != 'AND':
                    break
                parser.pop()
        def matches(row):
            for col, op, value in clauses:
                actual = row[col]
                if op in ('IS', 'IS NOT'):
                    ok = (actual is None) == (op == 'IS')
                elif actual is None or value is None:
                    ok = False
                elif op == '=':
                    ok = actual == value
                elif op in ('!=', '<>'):
                    ok = actual != value
                elif op == '<':
                    ok = actual < value
                elif op == '>':
                    ok = actual > value
                elif op == '<=':
                    ok = actual <= value
                else:
                    ok = actual >= value
                if not ok:
                    return False
            return True
        return matches

    def _statement(self, command, p):
        if command == 'CREATE':
            p.expect('TABLE')
            name = p.identifier()
            if name in self._tables:
                raise DatabaseError(f'Table already exists: {name}')
            p.expect('(')
            columns = []
            while True:
                col = {'name': p.identifier(), 'type': p.pop().upper(),
                       'primary': False, 'required': False}
                while p.peek() in ('PRIMARY', 'NOT'):
                    if p.pop().upper() == 'PRIMARY':
                        p.expect('KEY')
                        col['primary'] = True
                    else:
                        p.expect('NULL')
                        col['required'] = True
                columns.append(col)
                if p.peek() != ',':
                    break
                p.pop()
            p.expect(')')
            self._tables[name] = {'columns': columns, 'rows': []}
            return 0
        if command == 'DROP':
            p.expect('TABLE')
            name = p.identifier()
            if name not in self._tables:
                raise DatabaseError(f'Unknown table: {name}')
            del self._tables[name]
            return 0
        if command == 'INSERT':
            p.expect('INTO')
            table = self._table(p)
            names = [c['name'] for c in table['columns']]
            if p.peek() == '(':
                p.pop()
                names = [self._column(p, table)]
                while p.peek() == ',':
                    p.pop()
                    names.append(self._column(p, table))
                p.expect(')')
            if len(set(names)) != len(names):
                raise DatabaseError('Duplicate insert columns')
            p.expect('VALUES')
            p.expect('(')
            values = [p.literal()]
            while p.peek() == ',':
                p.pop()
                values.append(p.literal())
            p.expect(')')
            if len(names) != len(values):
                raise DatabaseError('Column/value count mismatch')
            row = dict.fromkeys(c['name'] for c in table['columns'])
            row.update(zip(names, values))
            table['rows'].append(row)
            return 1
        if command == 'SELECT':
            names = []
            if p.peek() == '*':
                p.pop()
            else:
                names.append(p.identifier())
                while p.peek() == ',':
                    p.pop()
                    names.append(p.identifier())
            p.expect('FROM')
            table = self._table(p)
            available = [c['name'] for c in table['columns']]
            names = names or available
            if not set(names).issubset(available):
                raise DatabaseError('Unknown selected column')
            matches = self._where(p, table)
            rows = [row for row in table['rows'] if matches(row)]
            if p.peek() == 'ORDER':
                p.pop()
                p.expect('BY')
                col = self._column(p, table)
                descending = p.peek() == 'DESC'
                if p.peek() in ('ASC', 'DESC'):
                    p.pop()
                rows.sort(key=lambda row: (row[col] is not None, row[col]), reverse=descending)
            if p.peek() == 'LIMIT':
                p.pop()
                limit = p.literal()
                if type(limit) is not int or limit < 0:
                    raise DatabaseError('LIMIT must be a nonnegative integer')
                rows = rows[:limit]
            return [{name: row[name] for name in names} for row in rows]
        if command in ('UPDATE', 'DELETE'):
            if command == 'DELETE':
                p.expect('FROM')
            table = self._table(p)
            changes = {}
            if command == 'UPDATE':
                p.expect('SET')
                while True:
                    col = self._column(p, table)
                    p.expect('=')
                    changes[col] = p.literal()
                    if p.peek() != ',':
                        break
                    p.pop()
            matches = self._where(p, table)
            affected = [row for row in table['rows'] if matches(row)]
            if command == 'UPDATE':
                for row in affected:
                    row.update(changes)
            else:
                table['rows'] = [row for row in table['rows'] if not matches(row)]
            return len(affected)
        raise DatabaseError(f'Unsupported statement: {command}')
