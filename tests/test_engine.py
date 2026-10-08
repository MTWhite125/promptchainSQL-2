import tempfile
import unittest
from pathlib import Path
from minidb import Database, DatabaseError


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.db = Database()
        self.db.execute('CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT NOT NULL, score REAL)')
        self.db.execute("INSERT INTO users VALUES (1, 'Ada', 9.5)")

    def tearDown(self):
        self.db.close()

    def test_crud_and_query(self):
        self.db.execute("INSERT INTO users (id, name) VALUES (2, 'O''Brien')")
        self.assertEqual(self.db.execute('SELECT id FROM users WHERE score IS NULL'), [{'id': 2}])
        self.assertEqual(self.db.execute("UPDATE users SET score = 10 WHERE id = 2"), 1)
        self.assertEqual(self.db.execute('SELECT name FROM users WHERE score >= 9 AND id > 0 ORDER BY score DESC LIMIT 1'), [{'name': "O'Brien"}])
        self.assertEqual(self.db.execute('DELETE FROM users WHERE id = 1'), 1)
        self.assertEqual(len(self.db.execute('SELECT * FROM users')), 1)

    def test_constraints_and_statement_atomicity(self):
        for sql in ("INSERT INTO users VALUES (1, 'Duplicate', 2)",
                    "INSERT INTO users VALUES (2, NULL, 2)",
                    "INSERT INTO users VALUES ('bad', 'Name', 2)",
                    "UPDATE users SET name = NULL",
                    "DELETE FROM users unexpected"):
            with self.assertRaises(DatabaseError):
                self.db.execute(sql)
        self.assertEqual(self.db.execute('SELECT * FROM users'), [{'id': 1, 'name': 'Ada', 'score': 9.5}])

    def test_transactions(self):
        with self.assertRaises(RuntimeError):
            with self.db.transaction():
                self.db.execute('DELETE FROM users')
                raise RuntimeError('cancel')
        self.assertEqual(len(self.db.execute('SELECT * FROM users')), 1)
        with self.db.transaction():
            self.db.execute("UPDATE users SET name = 'Grace'")
        self.assertEqual(self.db.execute('SELECT name FROM users'), [{'name': 'Grace'}])

    def test_schema_introspection(self):
        self.assertEqual(self.db.list_tables(), ['users'])
        columns = self.db.describe_table('USERS')
        self.assertTrue(columns[0]['primary'])
        columns[0]['name'] = 'changed'
        self.assertEqual(self.db.describe_table('users')[0]['name'], 'id')
        self.assertFalse(self.db.in_transaction)
        self.db.execute('BEGIN')
        self.assertTrue(self.db.in_transaction)
        self.db.execute('ROLLBACK')
        with self.assertRaises(DatabaseError):
            self.db.describe_table('missing')

    def test_disk_persistence_lock_and_uncommitted_close(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'db.json'
            with Database(path) as db:
                db.execute('CREATE TABLE items (id INTEGER PRIMARY KEY)')
                with self.assertRaises(DatabaseError):
                    Database(path)
                with db.transaction():
                    db.execute('INSERT INTO items VALUES (1)')
                db.execute('BEGIN')
                db.execute('INSERT INTO items VALUES (2)')
            with Database(path) as reopened:
                self.assertEqual(reopened.execute('SELECT * FROM items'), [{'id': 1}])

    def test_invalid_sql_and_schema(self):
        for sql in ('', 'SELECT missing FROM users', 'SELECT * FROM missing',
                    'CREATE TABLE bad (x BLOB)', 'CREATE TABLE bad (x TEXT, x TEXT)',
                    'SELECT * FROM users LIMIT -1', 'COMMIT',
                    "SELECT * FROM users WHERE id = 1 OR id = 2"):
            with self.assertRaises(DatabaseError):
                self.db.execute(sql)
        self.db.execute('CREATE TABLE bad (x TEXT)')

    def test_null_and_detached_results(self):
        self.db.execute("INSERT INTO users VALUES (2, 'Bob', NULL)")
        self.assertEqual(self.db.execute('SELECT id FROM users WHERE score = NULL'), [])
        rows = self.db.execute('SELECT * FROM users')
        rows[0]['name'] = 'Changed'
        self.assertEqual(self.db.execute('SELECT name FROM users WHERE id = 1'), [{'name': 'Ada'}])

    def test_corrupt_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'db.json'
            path.write_text('{broken')
            with self.assertRaises(DatabaseError):
                Database(path)
            path.write_text('{"version": 1, "tables": {}}')
            with Database(path) as db:
                db.execute('CREATE TABLE repaired (id INTEGER)')

    def test_failed_statement_inside_transaction(self):
        with self.db.transaction():
            self.db.execute("INSERT INTO users VALUES (2, 'Bob', 8)")
            with self.assertRaises(DatabaseError):
                self.db.execute('UPDATE users SET id = 1')
        self.assertEqual(self.db.execute('SELECT id FROM users ORDER BY id'), [{'id': 1}, {'id': 2}])


if __name__ == '__main__':
    unittest.main()
