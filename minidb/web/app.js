'use strict';
const $ = id => document.getElementById(id);
const token = document.querySelector('meta[name="minidb-token"]').content;
let client = sessionStorage.getItem('minidb-client');
if (!client) { client = crypto.randomUUID(); sessionStorage.setItem('minidb-client', client); }
let state = null, rows = [], columns = [], busy = false, selectedTable = null;
function status(message, error = false) { $('status').textContent = message; $('status').className = error ? 'error' : ''; }
async function api(path, sql) {
  const response = await fetch(path, {
    method: sql === undefined ? 'GET' : 'POST',
    headers: {'X-MiniDB-Token': token, 'X-MiniDB-Client': client, 'Content-Type': 'application/json'},
    body: sql === undefined ? undefined : JSON.stringify({sql})
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
  return data;
}
function controls() {
  $('run').disabled = busy; $('refresh').disabled = busy;
  $('begin').disabled = busy || !state || state.in_transaction;
  $('commit').disabled = $('rollback').disabled = busy || !state || !state.in_transaction;
  $('export').disabled = busy || !columns.length;
}
function renderState(next) {
  state = next;
  $('database').textContent = next.database;
  $('mode').textContent = next.in_transaction ? 'Transaction active' : 'Autocommit';
  $('footer-mode').textContent = next.in_transaction ? 'Changes pending · Commit or roll back when finished' : 'Autocommit saves each write';
  $('tables').replaceChildren();
  if (!next.tables.length) {
    const empty = document.createElement('p'); empty.className = 'muted'; empty.textContent = 'No tables yet. Run CREATE TABLE to get started.'; $('tables').append(empty);
  }
  if (!next.tables.some(t => t.name === selectedTable)) selectedTable = null;
  for (const table of next.tables) {
    const button = document.createElement('button');
    button.className = 'table-button' + (table.name === selectedTable ? ' active' : '');
    button.textContent = table.name; button.disabled = busy;
    button.title = `Browse ${table.name} (up to 1,000 rows)`;
    button.addEventListener('click', () => { selectedTable = table.name; $('sql').value = `SELECT * FROM ${table.name} LIMIT 1000;`; run($('sql').value); });
    $('tables').append(button);
  }
  $('schema').replaceChildren();
  const table = next.tables.find(t => t.name === selectedTable);
  if (!table) $('schema').textContent = 'Select a table to inspect its columns.';
  else for (const col of table.columns) {
    const div = document.createElement('div'); div.className = 'column'; div.textContent = col.name;
    const type = document.createElement('small'); type.textContent = [col.type, col.primary ? 'PRIMARY KEY' : '', col.required ? 'NOT NULL' : ''].filter(Boolean).join(' · ');
    div.append(type); $('schema').append(div);
  }
  controls();
}
function renderResults(data) {
  rows = data.rows || []; columns = data.columns || [];
  $('results').replaceChildren(); $('count').textContent = columns.length ? `· ${rows.length} rows` : '';
  if (!columns.length) { const empty = document.createElement('div'); empty.className = 'empty'; empty.textContent = 'Statement completed. Run SELECT to view data.'; $('results').append(empty); return; }
  const table = document.createElement('table'); const head = document.createElement('thead'); const tr = document.createElement('tr');
  for (const col of columns) { const th = document.createElement('th'); th.scope = 'col'; th.textContent = col; tr.append(th); }
  head.append(tr); table.append(head);
  const body = document.createElement('tbody');
  for (const row of rows) {
    const tr = document.createElement('tr');
    for (const col of columns) { const td = document.createElement('td'); td.textContent = row[col] === null ? 'NULL' : String(row[col]); if (row[col] === null) td.className = 'null'; tr.append(td); }
    body.append(tr);
  }
  table.append(body); $('results').append(table);
  if (!rows.length) { const empty = document.createElement('p'); empty.className = 'muted'; empty.textContent = 'No matching rows.'; $('results').append(empty); }
}
async function run(sql) {
  if (busy) return;
  if (!sql.trim()) { status('Enter a SQL statement first.', true); return; }
  busy = true; controls(); status('Running…');
  try {
    const data = await api('/api/query', sql); renderResults(data); renderState(data.state);
    status(data.rows ? `${data.rows.length} row(s) returned.` : `Statement completed. ${data.affected} row(s) affected.`);
  } catch (error) { status(error.message, true); }
  finally { busy = false; if (state) renderState(state); controls(); }
}
async function refresh() {
  if (busy) return;
  busy = true; controls();
  try { renderState(await api('/api/state')); status('Connected. Ready to run SQL.'); }
  catch (error) { status(error.message, true); }
  finally { busy = false; if (state) renderState(state); controls(); }
}
function runEditor() {
  const editor = $('sql');
  run(editor.selectionStart !== editor.selectionEnd ? editor.value.slice(editor.selectionStart, editor.selectionEnd) : editor.value);
}
$('run').addEventListener('click', runEditor);
$('sql').addEventListener('keydown', event => { if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') { event.preventDefault(); runEditor(); } });
$('refresh').addEventListener('click', refresh);
for (const action of ['begin', 'commit', 'rollback']) $(action).addEventListener('click', () => run(action.toUpperCase()));
$('export').addEventListener('click', () => {
  const quote = value => '"' + String(value ?? '').replaceAll('"', '""') + '"';
  const csv = [columns.map(quote).join(','), ...rows.map(row => columns.map(col => quote(row[col])).join(','))].join('\r\n');
  const url = URL.createObjectURL(new Blob([csv], {type: 'text/csv;charset=utf-8'}));
  const link = document.createElement('a'); link.href = url; link.download = 'minidb-results.csv'; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
});
window.addEventListener('beforeunload', event => { if (state && state.in_transaction) { event.preventDefault(); event.returnValue = ''; } });
refresh();
