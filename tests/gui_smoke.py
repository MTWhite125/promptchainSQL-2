"""Desktop integration check: python3 tests/gui_smoke.py from project root."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tempfile
import tkinter as tk
from unittest.mock import patch
from minidb.gui import DatabaseGUI

root = tk.Tk()
root.withdraw()
app = DatabaseGUI(root)
try:
    assert app.run_sql('CREATE TABLE people (id INTEGER PRIMARY KEY, name TEXT, age INTEGER)')
    assert app.db.list_tables() == ['people']
    assert app.run_sql("INSERT INTO people VALUES (1, 'Ada', 36)")
    app.tables.selection_set(0)
    app.browse_table()
    assert app.result_rows == [{'id': 1, 'name': 'Ada', 'age': 36}]
    assert len(app.results.get_children()) == 1
    assert app.run_sql('SELECT name FROM people WHERE id = 99')
    assert app.result_columns == ['name']
    assert app.run_sql('BEGIN')
    assert app.run_sql("UPDATE people SET name = 'Grace'")
    assert app.run_sql('ROLLBACK')
    assert app.db.execute('SELECT name FROM people') == [{'name': 'Ada'}]
    with patch('minidb.gui.messagebox.showerror') as error:
        assert not app.run_sql('SELECT bad FROM people')
        error.assert_called_once()
    with tempfile.TemporaryDirectory() as folder:
        csv_path = Path(folder) / 'results.csv'
        app.run_sql('SELECT * FROM people')
        with patch('minidb.gui.filedialog.asksaveasfilename', return_value=str(csv_path)):
            app.export_csv()
        assert 'Ada' in csv_path.read_text()
        path = Path(folder) / 'db.json'
        with patch('minidb.gui.messagebox.askokcancel', return_value=True):
            app.switch_database(path, create=True)
        assert path.exists()
        app.run_sql('CREATE TABLE saved (id INTEGER PRIMARY KEY)')
        app.run_sql('INSERT INTO saved VALUES (1)')
        app.run_sql('BEGIN')
        app.run_sql('INSERT INTO saved VALUES (2)')
        with patch('minidb.gui.messagebox.askyesnocancel', return_value=None):
            assert not app.resolve_transaction()
            assert app.db.in_transaction
        with patch('minidb.gui.messagebox.askyesnocancel', return_value=False):
            assert app.resolve_transaction()
        assert app.db.execute('SELECT * FROM saved') == [{'id': 1}]
        app.switch_database(Path(folder) / 'other.json', create=True)
        app.switch_database(path)
        assert app.db.execute('SELECT * FROM saved') == [{'id': 1}]
    root.update_idletasks()
    print('GUI integration checks passed: queries, browsing, errors, CSV, transactions, file creation and reopening.')
finally:
    app.db.close()
    root.destroy()
