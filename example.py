from minidb import Database

with Database() as db:
    db.execute('CREATE TABLE people (id INTEGER PRIMARY KEY, name TEXT NOT NULL, age INTEGER)')
    with db.transaction():
        db.execute("INSERT INTO people VALUES (1, 'Ada', 36)")
        db.execute("INSERT INTO people VALUES (2, 'Grace', 85)")
    print(db.execute('SELECT name, age FROM people WHERE age >= 40 ORDER BY age DESC'))
