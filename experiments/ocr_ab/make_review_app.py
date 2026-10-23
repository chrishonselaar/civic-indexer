"""Generate the ground-truth review app: data/ocr_ab/review/ (open index.html locally).

Left pane: page image. Right pane: editable transcript, seeded from the reference
engine (azure_di) with Claude's judged corrections applied on top
(gt_drafts_overrides.json). Corrections autosave to localStorage; "Export" downloads
corrections.json to drop into experiments/ocr_ab/results/.

Sample: page 1 + middle page of every test-set file (the protocol's ~2 pages/file).
"""
import json
from pathlib import Path

from common import MANIFEST, OUT

HERE = Path(__file__).resolve().parent
overrides = json.loads((HERE / "gt_drafts_overrides.json").read_text())

pages = []
for stem in MANIFEST:
    f = OUT / "azure_di" / f"{stem}.json"
    if not f.exists():
        continue
    by_page = {p["page"]: p["text"] for p in json.loads(f.read_text())}
    n = max(by_page)
    for pg in sorted({1, (n + 1) // 2}):
        pages.append({
            "id": f"{stem}:{pg}",
            "stem": stem,
            "page": pg,
            "img": f"../pages/{stem}/p{pg:04d}.jpg",
            "draft": overrides.get(f"{stem}:{pg}", by_page.get(pg, "")),
        })

app = OUT / "review"
app.mkdir(parents=True, exist_ok=True)
(app / "data.js").write_text("const PAGES = " + json.dumps(pages, ensure_ascii=False) + ";")

(app / "index.html").write_text("""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>OCR ground-truth review</title>
<link rel="stylesheet" href="style.css">
</head>
<body>
<header>
  <strong>OCR ground-truth review</strong>
  <span id="progress"></span>
  <button id="ok">Mark correct (Ctrl+Enter)</button>
  <button id="export">Export corrections.json</button>
  <label class="imp">Import <input type="file" id="import" accept=".json"></label>
</header>
<div id="layout">
  <nav id="list"></nav>
  <main>
    <div id="imgpane"><img id="img" alt="page"></div>
    <div id="editpane">
      <div id="meta"></div>
      <textarea id="text" spellcheck="false"></textarea>
    </div>
  </main>
</div>
<script src="data.js"></script>
<script src="app.js"></script>
</body>
</html>
""")

(app / "style.css").write_text("""* { box-sizing: border-box; }
body { margin: 0; font: 14px/1.4 system-ui, sans-serif; height: 100vh; display: flex; flex-direction: column; }
header { padding: 8px 12px; background: #1e293b; color: #fff; display: flex; gap: 12px; align-items: center; }
header button { padding: 4px 10px; }
.imp { font-size: 12px; }
#layout { flex: 1; display: flex; min-height: 0; }
#list { width: 300px; overflow-y: auto; border-right: 1px solid #cbd5e1; background: #f8fafc; }
#list div { padding: 6px 10px; cursor: pointer; border-bottom: 1px solid #e2e8f0; font-size: 12px; }
#list div.cur { background: #dbeafe; }
#list div.done { color: #15803d; }
#list div.edited { color: #b45309; }
main { flex: 1; display: flex; min-width: 0; }
#imgpane { flex: 1; overflow: auto; background: #475569; }
#img { width: 100%; cursor: zoom-in; display: block; }
#img.zoom { width: auto; max-width: none; cursor: zoom-out; }
#editpane { flex: 1; display: flex; flex-direction: column; }
#meta { padding: 6px 10px; background: #e2e8f0; font-size: 12px; }
#text { flex: 1; border: 0; padding: 10px; font: 13px/1.5 ui-monospace, monospace; resize: none; outline: none; }
""")

(app / "app.js").write_text("""const KEY = 'ocr-gt-corrections-v1';
let store = JSON.parse(localStorage.getItem(KEY) || '{}');
let cur = 0;
const $ = id => document.getElementById(id);

function save() { localStorage.setItem(KEY, JSON.stringify(store)); renderList(); }
function state(p) { return store[p.id] || { text: p.draft, status: 'pending' }; }

function renderList() {
  const list = $('list');
  list.innerHTML = '';
  let done = 0;
  PAGES.forEach((p, i) => {
    const s = state(p);
    if (s.status !== 'pending') done++;
    const d = document.createElement('div');
    d.textContent = `${p.stem.slice(0, 34)} p${p.page}` + (s.status === 'ok' ? ' \\u2713' : s.status === 'edited' ? ' \\u270e' : '');
    d.className = (i === cur ? 'cur ' : '') + (s.status === 'ok' ? 'done' : s.status === 'edited' ? 'edited' : '');
    d.onclick = () => show(i);
    list.appendChild(d);
  });
  $('progress').textContent = `${done}/${PAGES.length} reviewed`;
}

function show(i) {
  cur = i;
  const p = PAGES[i];
  $('img').src = p.img;
  $('img').classList.remove('zoom');
  $('meta').textContent = `${p.stem} — page ${p.page}. Edit the text to match the image, or mark correct.`;
  $('text').value = state(p).text;
  renderList();
}

$('text').addEventListener('input', () => {
  const p = PAGES[cur];
  store[p.id] = { text: $('text').value, status: 'edited' };
  save();
});
$('ok').onclick = () => {
  const p = PAGES[cur];
  store[p.id] = { text: $('text').value, status: store[p.id] && store[p.id].status === 'edited' ? 'edited' : 'ok' };
  save();
  if (cur < PAGES.length - 1) show(cur + 1);
};
document.addEventListener('keydown', e => {
  if (e.ctrlKey && e.key === 'Enter') $('ok').click();
});
$('img').onclick = () => $('img').classList.toggle('zoom');
$('export').onclick = () => {
  const out = {};
  PAGES.forEach(p => { out[p.id] = state(p); });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([JSON.stringify(out, null, 1)], { type: 'application/json' }));
  a.download = 'corrections.json';
  a.click();
};
$('import').onchange = async e => {
  const f = e.target.files[0];
  if (!f) return;
  store = JSON.parse(await f.text());
  save();
  show(cur);
};
show(0);
""")

print(f"review app: {app / 'index.html'} ({len(pages)} pages)")
