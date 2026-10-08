"""Tk desktop interface. Launch with python3 -m minidb.gui."""
from __future__ import annotations

import argparse
import csv
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .engine import Database, DatabaseError, Parser


class DatabaseGUI:
    def __init__(self, root, path=None):
        self.root = root
        self.db = Database(path)
        self.result_rows = []
        self.result_columns = []
        root.title('MiniDB')
        root.geometry('1050x720')
        root.minsize(760, 520)
        root.protocol('WM_DELETE_WINDOW', self.close)
        self.status = tk.StringVar()
        self.location = tk.StringVar()
        self.transaction_status = tk.StringVar()
        self.schema = tk.StringVar(value='Select a table to view its columns.')
        self._build()
        root.bind('<Control-Return>', self.run_query)
        root.bind('<Command-Return>', self.run_query)
        self.editor.insert('1.0', 'CREATE TABLE people (id INTEGER PRIMARY KEY, name TEXT NOT NULL, age INTEGER);')
        self.refresh()
        self.status.set('Ready. Create a table or open a database to begin.')

    def _build(self):
        toolbar = ttk.Frame(self.root, padding=10)
        toolbar.pack(fill='x')
        for label, command in [('New database', self.new_database), ('Open database', self.open_database),
                               ('Refresh tables', self.refresh)]:
            ttk.Button(toolbar, text=label, command=command).pack(side='left', padx=(0, 6))
        ttk.Label(toolbar, textvariable=self.location).pack(side='left', padx=12)
        panes = ttk.Panedwindow(self.root, orient='horizontal')
        panes.pack(fill='both', expand=True, padx=10)
        sidebar = ttk.Frame(panes, padding=8)
        panes.add(sidebar, weight=1)
        ttk.Label(sidebar, text='TABLES', font=('Helvetica', 12, 'bold')).pack(anchor='w', pady=(0, 8))
        self.tables = tk.Listbox(sidebar, exportselection=False, width=22)
        self.tables.pack(fill='both', expand=True)
        self.tables.bind('<<ListboxSelect>>', self.select_table)
        ttk.Button(sidebar, text='Browse selected table', command=self.browse_table).pack(fill='x', pady=8)
        ttk.Label(sidebar, textvariable=self.schema, wraplength=220, justify='left').pack(anchor='w', pady=8)
        main = ttk.Frame(panes, padding=8)
        panes.add(main, weight=4)
        ttk.Label(main, text='SQL EDITOR', font=('Helvetica', 12, 'bold')).pack(anchor='w')
        ttk.Label(main, text='Run one statement, or select one in the editor. ⌘/Ctrl + Enter to run.').pack(anchor='w', pady=6)
        self.editor = tk.Text(main, height=8, wrap='word', undo=True, font=('Menlo', 12))
        self.editor.pack(fill='x')
        actions = ttk.Frame(main)
        actions.pack(fill='x', pady=8)
        ttk.Button(actions, text='Run SQL', command=self.run_query).pack(side='left', padx=(0, 12))
        self.begin_button = ttk.Button(actions, text='Begin', command=lambda: self.run_sql('BEGIN'))
        self.commit_button = ttk.Button(actions, text='Commit', command=lambda: self.run_sql('COMMIT'))
        self.rollback_button = ttk.Button(actions, text='Rollback', command=lambda: self.run_sql('ROLLBACK'))
        for button in (self.begin_button, self.commit_button, self.rollback_button):
            button.pack(side='left', padx=3)
        ttk.Label(actions, textvariable=self.transaction_status).pack(side='left', padx=10)
        result_header = ttk.Frame(main)
        result_header.pack(fill='x', pady=6)
        ttk.Label(result_header, text='RESULTS', font=('Helvetica', 12, 'bold')).pack(side='left')
        ttk.Button(result_header, text='Export CSV', command=self.export_csv).pack(side='right')
        grid = ttk.Frame(main)
        grid.pack(fill='both', expand=True)
        grid.rowconfigure(0, weight=1)
        grid.columnconfigure(0, weight=1)
        self.results = ttk.Treeview(grid, show='headings')
        self.results.grid(row=0, column=0, sticky='nsew')
        vertical = ttk.Scrollbar(grid, orient='vertical', command=self.results.yview)
        horizontal = ttk.Scrollbar(grid, orient='horizontal', command=self.results.xview)
        vertical.grid(row=0, column=1, sticky='ns')
        horizontal.grid(row=1, column=0, sticky='ew')
        self.results.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        ttk.Label(self.root, textvariable=self.status, padding=10, wraplength=1000).pack(fill='x')

    def refresh(self):
        selected = self.selected_table()
        self.tables.delete(0, 'end')
        names = self.db.list_tables()
        for name in names:
            self.tables.insert('end', name)
        if selected in names:
            self.tables.selection_set(names.index(selected))
        self.select_table()
        self.location.set(str(self.db.path) if self.db.path else 'In-memory database')
        active = self.db.in_transaction
        self.transaction_status.set('Transaction active' if active else 'Autocommit')
        self.begin_button.configure(state='disabled' if active else 'normal')
        for button in (self.commit_button, self.rollback_button):
            button.configure(state='normal' if active else 'disabled')

    def selected_table(self):
        selection = self.tables.curselection()
        return self.tables.get(selection[0]) if selection else None

    def select_table(self, event=None):
        name = self.selected_table()
        if not name:
            self.schema.set('Select a table to view its columns.')
            return
        columns = self.db.describe_table(name)
        self.schema.set('\n\n'.join(
            f"{c['name']}  {c['type']}" + (' · PRIMARY KEY' if c['primary'] else '')
            + (' · NOT NULL' if c['required'] else '') for c in columns))

    def browse_table(self):
        name = self.selected_table()
        if name:
            sql = f'SELECT * FROM {name} LIMIT 1000;'
            self.editor.delete('1.0', 'end')
            self.editor.insert('1.0', sql)
            self.run_sql(sql)
        else:
            self.status.set('Select a table first.')

    def run_query(self, event=None):
        sql = self.editor.get('sel.first', 'sel.last') if self.editor.tag_ranges('sel') else self.editor.get('1.0', 'end')
        self.run_sql(sql.strip())
        return 'break'

    def run_sql(self, sql):
        if not sql:
            self.status.set('Enter a SQL statement first.')
            return False
        try:
            result = self.db.execute(sql)
            self.refresh()
            if isinstance(result, list):
                # Get projected column names even when a query returns no rows.
                parser = Parser(sql)
                parser.expect('SELECT')
                if parser.peek() == '*':
                    parser.pop()
                    parser.expect('FROM')
                    columns = [c['name'] for c in self.db.describe_table(parser.identifier())]
                else:
                    columns = [parser.identifier()]
                    while parser.peek() == ',':
                        parser.pop()
                        columns.append(parser.identifier())
                self.show_results(result, columns)
                self.status.set(f'{len(result)} row(s) returned. NULL is displayed as ⟨NULL⟩.')
            else:
                self.show_results([], [])
                self.status.set(f'Statement completed. {result} row(s) affected.')
            return True
        except (DatabaseError, OSError) as exc:
            self.status.set(f'Error: {exc}')
            messagebox.showerror('SQL error', str(exc), parent=self.root)
            return False

    def show_results(self, rows, columns):
        self.result_rows = rows
        self.result_columns = list(dict.fromkeys(columns))
        self.results.delete(*self.results.get_children())
        # Internal IDs avoid collisions with Treeview's reserved column names.
        ids = [f'column_{i}' for i in range(len(self.result_columns))]
        self.results.configure(columns=ids)
        for column_id, name in zip(ids, self.result_columns):
            self.results.heading(column_id, text=name)
            self.results.column(column_id, width=160, minwidth=80, stretch=True)
        for row in rows:
            self.results.insert('', 'end', values=[
                '⟨NULL⟩' if row[name] is None else str(row[name]) for name in self.result_columns])

    def export_csv(self):
        if not self.result_columns:
            self.status.set('Run a SELECT query before exporting.')
            return
        path = filedialog.asksaveasfilename(parent=self.root, defaultextension='.csv', filetypes=[('CSV', '*.csv')])
        if not path:
            return
        try:
            with open(path, 'w', newline='', encoding='utf-8') as stream:
                writer = csv.DictWriter(stream, fieldnames=self.result_columns)
                writer.writeheader()
                writer.writerows(self.result_rows)
            self.status.set(f'Exported {len(self.result_rows)} rows to {path}. NULL exports as an empty field.')
        except OSError as exc:
            messagebox.showerror('Export error', str(exc), parent=self.root)

    def resolve_transaction(self):
        if not self.db.in_transaction:
            return True
        answer = messagebox.askyesnocancel('Active transaction',
            'Commit the active transaction?\nYes: commit. No: roll back. Cancel: keep working.', parent=self.root)
        if answer is None:
            return False
        return self.run_sql('COMMIT' if answer else 'ROLLBACK')

    def new_database(self):
        path = filedialog.asksaveasfilename(parent=self.root, title='Create database',
            defaultextension='.json', filetypes=[('MiniDB database', '*.json')])
        if not path:
            return
        from pathlib import Path
        if Path(path).exists():
            messagebox.showerror('File exists', 'Choose a new filename. Use Open database for existing files.', parent=self.root)
            return
        self.switch_database(path, create=True)

    def open_database(self):
        path = filedialog.askopenfilename(parent=self.root, filetypes=[('MiniDB database', '*.json'), ('All files', '*')])
        if path:
            self.switch_database(path)

    def switch_database(self, path, create=False):
        from pathlib import Path
        if self.db.path == Path(path).resolve():
            self.status.set('This database is already open.')
            return
        if not self.resolve_transaction():
            return
        if self.db.path is None and self.db.list_tables():
            if not messagebox.askokcancel('In-memory database', 'Switching databases will discard the in-memory tables. Continue?', parent=self.root):
                return
        candidate = None
        try:
            candidate = Database(path)
            if create:
                # An empty committed transaction persists the new database.
                with candidate.transaction():
                    pass
        except (DatabaseError, OSError) as exc:
            if candidate:
                candidate.close()
            messagebox.showerror('Cannot open database', str(exc), parent=self.root)
            return
        self.db.close()
        self.db = candidate
        self.show_results([], [])
        self.refresh()
        self.status.set(f'Opened {path}.')

    def close(self):
        if not self.resolve_transaction():
            return
        if self.db.path is None and self.db.list_tables():
            if not messagebox.askokcancel('In-memory database', 'Closing will discard the in-memory tables. Continue?', parent=self.root):
                return
        self.db.close()
        self.root.destroy()


def main():
    parser = argparse.ArgumentParser(description='MiniDB desktop GUI')
    parser.add_argument('database', nargs='?', help='Database file; defaults to in-memory')
    args = parser.parse_args()
    root = tk.Tk()
    try:
        DatabaseGUI(root, args.database)
    except (DatabaseError, OSError) as exc:
        messagebox.showerror('Cannot open database', str(exc), parent=root)
        root.destroy()
        return 1
    root.mainloop()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
