"""Run with python3 -m minidb [database.json] [--execute SQL]."""
import argparse
import json
import sys
from .engine import Database, DatabaseError


def main():
    parser = argparse.ArgumentParser(description='MiniDB SQL shell')
    parser.add_argument('database', nargs='?', default='database.json')
    parser.add_argument('-e', '--execute', help='Execute one SQL statement and exit')
    args = parser.parse_args()
    try:
        with Database(args.database) as db:
            if args.execute:
                print(json.dumps(db.execute(args.execute), indent=2))
                return 0
            print('MiniDB — one statement per line. Type .quit to exit or .help for help.')
            while True:
                try:
                    sql = input('minidb> ').strip()
                except (EOFError, KeyboardInterrupt):
                    print()
                    break
                if sql in ('.quit', '.exit'):
                    break
                if sql == '.help':
                    print('CREATE TABLE, INSERT INTO, SELECT, UPDATE, DELETE FROM, DROP TABLE')
                    print('BEGIN, COMMIT, ROLLBACK; WHERE with AND; ORDER BY; LIMIT')
                    continue
                if not sql:
                    continue
                try:
                    print(json.dumps(db.execute(sql), indent=2))
                except DatabaseError as exc:
                    print(f'Error: {exc}', file=sys.stderr)
        return 0
    except (DatabaseError, OSError) as exc:
        print(f'Error: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
