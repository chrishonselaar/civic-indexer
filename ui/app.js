// Civic-indexer search UI — adapted from src-uitspraken-mcp/search-ui/app.js
// (same class vocabulary so styles.css applies; SRC tabs/regulation features dropped).
//
// Cross-corpus by default (design/cross-corpus-search.md): no dataset is sent, so
// the API searches every projected source, and `source` is an ordinary facet.

const API_BASE_URL = '';
const PAGE_SIZE = 20;

let currentPage = 1;
let currentQuery = '';
let selectedFilters = { source: [], publisher: [], file_type: [], disposition: [], months: [] };
// Chips the user dismissed (W4). Identified by `field:matched text`, so they
// stop applying the moment the query text they came from is edited.
let droppedChips = new Set();

// index field -> how the chip presents itself, and which facet it maps onto
// when an offered chip set is accepted.
const CHIP_FIELDS = {
    publisher: { icon: '🏛️', label: 'Organisatie', filterKey: 'publisher' },
    published_month: { icon: '📅', label: 'Periode', filterKey: 'months' },
    file_type: { icon: '📄', label: 'Bestandstype', filterKey: 'file_type' },
    disposition: { icon: '⚙️', label: 'Status', filterKey: 'disposition' },
};

// Provenance is displayed: an inferred chip must be visibly distinguishable
// from a literally matched one (design/query-understanding.md §4).
const CHIP_SOURCES = {
    vocab: { label: 'gevonden', title: 'Letterlijk gevonden in de facetwaarden' },
    alias: { label: 'afkorting', title: 'Afkorting, opgezocht in de facetwaarden' },
    date_grammar: { label: 'datum', title: 'Herkend als datum-uitdrukking' },
    llm: { label: 'AI', title: 'Afgeleid door een taalmodel' },
    user: { label: 'handmatig', title: 'Door jou ingesteld' },
};

document.addEventListener('DOMContentLoaded', () => {
    restoreFromUrl();
    initializeSearch();
    initializeModal();
    loadStats();
    performSearch();
});

// Chips are the URL state (design/query-understanding.md §4): the whole search
// — query, chips the user dismissed, facets, mode, page — is addressable, so it
// can be bookmarked and shared. Together with dates frozen to absolute months at
// parse time, a shared link keeps meaning what it meant when it was made.
function restoreFromUrl() {
    const p = new URLSearchParams(window.location.search);
    document.getElementById('searchInput').value = p.get('q') || '';
    for (const [key, param] of [['source', 'source'], ['publisher', 'publisher'],
                                ['file_type', 'file_type'],
                                ['disposition', 'disposition'], ['months', 'month']]) {
        selectedFilters[key] = p.getAll(param);
    }
    droppedChips = new Set(p.getAll('drop_chip'));
    for (const [id, param] of [['sortSelect', 'sort'], ['modeSelect', 'mode']]) {
        if (p.get(param)) document.getElementById(id).value = p.get(param);
    }
    currentPage = Math.max(1, parseInt(p.get('page') || '1', 10) || 1);
}

function buildParams() {
    const params = new URLSearchParams({
        q: currentQuery,
        page: currentPage,
        size: PAGE_SIZE,
        sort: document.getElementById('sortSelect').value,
        mode: document.getElementById('modeSelect').value,
    });
    for (const s of selectedFilters.source) params.append('source', s);
    for (const p of selectedFilters.publisher) params.append('publisher', p);
    for (const t of selectedFilters.file_type) params.append('file_type', t);
    for (const d of selectedFilters.disposition) params.append('disposition', d);
    for (const m of selectedFilters.months) params.append('month', m);
    for (const id of droppedChips) params.append('drop_chip', id);
    return params;
}

function initializeSearch() {
    const input = document.getElementById('searchInput');
    let debounce;
    input.addEventListener('input', () => {
        // Editing the query re-parses it from scratch; dismissals of chips that
        // came from the old text no longer mean anything.
        droppedChips.clear();
        clearTimeout(debounce);
        debounce = setTimeout(() => { currentPage = 1; performSearch(); }, 350);
    });
    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') { currentPage = 1; performSearch(); }
    });
    document.getElementById('sortSelect').addEventListener('change', () => {
        currentPage = 1; performSearch();
    });
    document.getElementById('modeSelect').addEventListener('change', () => {
        currentPage = 1; performSearch();
    });
    document.getElementById('clearFilters').addEventListener('click', () => {
        selectedFilters = { source: [], publisher: [], file_type: [], disposition: [], months: [] };
        droppedChips.clear();
        currentPage = 1; performSearch();
    });
}

async function loadStats() {
    try {
        const r = await fetch(`${API_BASE_URL}/api/stats`);
        const data = await r.json();
        document.getElementById('totalDocs').textContent =
            (data.total_docs ?? 0).toLocaleString('nl-NL');
        // A deployment with no rerank key still ran fine — the search falls back to
        // hybrid and `mode_used` says so — but the dropdown would go on offering
        // "hybride + herordening" as the default and top choice, i.e. naming a mode
        // this host cannot run. Relabel it rather than hide it: the option still
        // works, it just resolves to hybrid, and a demo must not display a capability
        // it does not deliver (funding/demo-bouw-ontwerp.md §2).
        if (data.rerank_available === false) {
            const opt = document.querySelector('#modeSelect option[value="hybrid_rerank"]');
            if (opt) {
                opt.textContent = 'Slim (hybride)';
                opt.title = 'Herordening is op deze server niet geconfigureerd; '
                    + 'deze keuze levert hybride zoeken.';
            }
        }
    } catch { /* stats are cosmetic */ }
    try {
        const r = await fetch(`${API_BASE_URL}/api/datasets`);
        const data = await r.json();
        document.getElementById('totalSources').textContent = (data.datasets || []).length;
    } catch { /* idem */ }
}

// A corpus id is derived into a label rather than looked up in a table: a table
// goes stale the moment the onboarding agent adds a source nobody edited the UI for.
function corpusLabel(sourceId) {
    if (!sourceId) return '';
    return String(sourceId)
        .replace(/_/g, ' ')
        .replace(/^\w/, c => c.toUpperCase());
}

async function performSearch() {
    currentQuery = document.getElementById('searchInput').value.trim();
    const params = buildParams();
    const shareable = new URLSearchParams(params);
    shareable.delete('size');
    history.replaceState(null, '', `?${shareable}`);

    const resultsList = document.getElementById('resultsList');
    try {
        const r = await fetch(`${API_BASE_URL}/api/search?${params}`);
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const data = await r.json();
        const modeLabel = {
            hybrid_rerank: ' · slim gerangschikt',
            hybrid: ' · hybride',
        }[data.mode_used] || '';
        // What was actually searched for: the residue, once chips took their words.
        const searched = data.chips_applied ? (data.q_effective ?? currentQuery) : currentQuery;
        document.getElementById('resultsCount').textContent =
            `${data.total.toLocaleString('nl-NL')} resultaten` +
            (searched ? ` voor "${searched}"` : '') + modeLabel;
        renderOperatorNote(data);
        renderChips(data);
        renderRerankNote(data);
        displayResults(data.results);
        updateFilters(data.facets, data);
        // Hybrid modes paginate within the fused pool, not the full BM25 total.
        updatePagination(data.pooled ? Math.min(data.total, data.pooled) : data.total);
    } catch (e) {
        resultsList.innerHTML = `<div class="no-results"><h3>Zoeken mislukt</h3><p>${e}</p></div>`;
    }
}

// --- W11(a) search operators ------------------------------------------------
//
// Three things get said, and none of them is optional:
//   * how the query was read (`parsed`) — the grouping is explicit, so a user can see
//     that `a AND b OR c` was taken as `(a AND b) OR c` rather than having to know the
//     precedence rule;
//   * what was repaired (`warnings`) — an auto-closed quote changes the result set;
//   * that ranking dropped to BM25, when a NOT under an OR made the dense leg unusable.
// The key is absent for a query without operator syntax, and then so is the panel.
function renderOperatorNote(data) {
    const el = document.getElementById('operatorNote');
    if (!el) return;
    const ops = data.operators;
    if (!ops) { el.classList.remove('active'); el.innerHTML = ''; return; }

    const lines = [];
    if (ops.used) {
        lines.push(`<span class="operator-read">Gelezen als</span>
            <code>${escapeHtml(ops.parsed || '')}</code>`);
    } else if (ops.consumed_by_chips) {
        // Not a failure: every search term was recognised as a filter, so there is no
        // text left to search. Saying "niet begrepen" here would be exactly backwards.
        lines.push(`<span class="operator-read">Alle zoektermen zijn als filter
            gelezen</span> — er wordt nu alleen gefilterd.`);
    } else {
        lines.push(`⚠️ Operatoren niet begrepen — letterlijk gezocht.`);
    }
    for (const w of ops.warnings || []) lines.push(`⚠️ ${escapeHtml(w)}`);
    if (ops.exclusion_in_or && data.mode_used === 'bm25') {
        lines.push(`ℹ️ <span title="${escapeHtml(data.mode_fallback_reason || '')}">Klassiek
            (BM25) gebruikt: een uitsluiting binnen een OF-groep is niet als filter uit
            te drukken, dus het semantische kanaal blijft erbuiten — anders zou het
            precies terugbrengen wat je uitsluit.</span>`);
    }
    if (ops.used && data.understanding_used === false && !ops.consumed_by_chips) {
        lines.push(`<span class="operator-hint">Geen losse woorden om als filter te
            lezen — alles staat tussen aanhalingstekens, haakjes, een OF of een
            uitsluiting, en daar blijven we vanaf.</span>`);
    }
    el.classList.add('active');
    el.innerHTML = lines.map(l => `<div>${l}</div>`).join('');
}

// --- W4 query understanding: free text -> editable filter chips -------------

function renderChips(data) {
    const bar = document.getElementById('chipBar');
    const chips = data.chips || [];
    bar.classList.toggle('offered', data.chips_applied === false);
    if (!chips.length) { bar.innerHTML = ''; return; }

    const offered = data.chips_applied === false;
    const note = offered
        ? 'Bedoelde je te filteren op:'
        : 'Begrepen als filter:';
    bar.innerHTML = `
        <span class="chip-note">${note}</span>
        ${chips.map(renderChip).join('')}
        ${offered
            ? '<button class="chip-apply" id="chipApply">Toch toepassen</button>'
            : ''}`;

    bar.querySelectorAll('.chip-remove').forEach(btn => {
        btn.addEventListener('click', () => {
            droppedChips.add(btn.dataset.id);
            currentPage = 1;
            performSearch();
        });
    });
    const apply = document.getElementById('chipApply');
    // The offer is accepted by turning the chips into ordinary facet
    // selections — explicit facets already beat the parser, so no extra
    // server-side "force" switch is needed.
    if (apply) apply.addEventListener('click', () => {
        for (const chip of chips) {
            const key = (CHIP_FIELDS[chip.field] || {}).filterKey;
            if (!key) continue;
            for (const v of chip.values) {
                if (!selectedFilters[key].includes(v)) selectedFilters[key].push(v);
            }
        }
        currentPage = 1;
        performSearch();
    });
}

function renderChip(chip) {
    const field = CHIP_FIELDS[chip.field] || { icon: '🔎', label: chip.field };
    const src = CHIP_SOURCES[chip.source] || { label: chip.source, title: chip.source };
    // The count is this chip's own narrowing power, counted on text alone —
    // comparable between chips, but not with a hybrid run's pooled total.
    const count = chip.count === undefined ? '' : `
        <span class="chip-count" title="${chip.count.toLocaleString('nl-NL')} documenten met alleen dit filter (telling op tekst)">${chip.count.toLocaleString('nl-NL')}</span>`;
    return `
        <span class="chip chip-${escapeHtml(chip.source)}" title="${escapeHtml(field.label)}: uit &quot;${escapeHtml(chip.text)}&quot;">
            <span class="chip-icon">${field.icon}</span>
            <span class="chip-label">${escapeHtml(chip.label)}</span>
            <span class="chip-source" title="${escapeHtml(src.title)}">${escapeHtml(src.label)}</span>
            ${count}
            <button class="chip-remove" data-id="${escapeHtml(chip.id)}" title="Verwijder dit filter — de woorden gaan terug naar de zoekopdracht">×</button>
        </span>`;
}

// Two ways a cross-corpus answer can be less than it looks, both of which would
// otherwise be invisible:
//   incomplete      one index dropped out of the union — the result set is short by
//                   a whole source, and it still returned HTTP 200.
//   rerank_blocked  reranking ships text to a third party, so it runs only where
//                   EVERY source in the pool opted in (engine/exposure.py). Mixed
//                   pools are the normal case once you search everything, and the
//                   fix is an admin decision on those manifests, not a UI setting.
function renderRerankNote(data) {
    const el = document.getElementById('rerankNote');
    if (!el) return;
    const notes = [];
    if (data.incomplete) {
        notes.push(`⚠️ <strong>Onvolledig resultaat</strong> — ${escapeHtml(data.incomplete)}.
            Er ontbreekt mogelijk een hele bron.`);
    }
    if (data.rerank_blocked) {
        notes.push(`⚖️ Zonder herordening: ${escapeHtml(data.rerank_blocked)}.
            Deze bronnen hebben geen toestemming voor verzending naar de externe
            herordenaar. Filter op één toegestane bron om herordening te gebruiken.`);
    }
    el.classList.toggle('active', notes.length > 0);
    el.innerHTML = notes.map(n => `<div>${n}</div>`).join('');
}

function displayResults(results) {
    const container = document.getElementById('resultsList');
    if (!results.length) {
        container.innerHTML = `
            <div class="no-results">
                <h3>Geen resultaten gevonden</h3>
                <p>Probeer andere zoektermen of pas de filters aan</p>
            </div>`;
        return;
    }
    container.innerHTML = results.map(r => {
        const excerpt = r.highlight || escapeHtml((r.content_preview || '')) || '';
        const partial = r.disposition === 'partial'
            ? '<span class="badge badge-warning">deels (OCR wacht)</span>' : '';
        return `
            <div class="result-item">
                <div class="result-header">
                    <div>
                        <div class="result-title" data-urn="${r.urn}">${escapeHtml(r.title)}</div>
                    </div>
                    ${partial}
                </div>
                <div class="result-meta">
                    ${r.source_id ? `<span class="meta-item corpus-tag">🗂️ ${escapeHtml(corpusLabel(r.source_id))}</span>` : ''}
                    ${r.published ? `<span class="meta-item">📅 ${r.published}</span>` : ''}
                    ${r.publisher ? `<span class="meta-item">🏛️ ${escapeHtml(r.publisher)}</span>` : ''}
                    ${r.pages ? `<span class="meta-item">📄 ${r.pages} pag.</span>` : ''}
                </div>
                <div class="result-excerpt">${excerpt}</div>
            </div>`;
    }).join('');
    container.querySelectorAll('.result-title').forEach(el => {
        el.addEventListener('click', () => openDocModal(el.dataset.urn));
    });
}

// A facet count means one of two things and the difference is worth a sentence
// (PROJ-3). In bm25 mode it is exhaustive: every document in the whole result set.
// In hybrid mode it counts within the fused pool — the documents you can actually
// page to — which is smaller, and is the same bound pagination already uses. Saying
// so is the whole point of the ruling: unexplained smaller numbers read as a defect.
function renderFacetScope(data) {
    const el = document.getElementById('facetScopeNote');
    if (!el) return;
    if (data.facets_scope !== 'pool') { el.classList.remove('active'); el.textContent = ''; return; }
    const n = data.pooled ?? 0;
    el.classList.add('active');
    el.title = 'Hybride zoeken rangschikt een pool van de best passende documenten. '
        + 'De facettellingen — en de paginering — gaan over die pool, niet over de hele index.';
    el.textContent = `Tellingen binnen de ${n.toLocaleString('nl-NL')} best passende resultaten`;
}

function updateFilters(facets, data) {
    renderFacetScope(data || {});
    updateCheckboxFilter('sourceFilter', (facets.source_id || []).map(
        f => ({ ...f, label: corpusLabel(f.value) })), 'source');
    updateCheckboxFilter('publisherFilter', (facets.publisher || []).slice(0, 15), 'publisher');
    updateCheckboxFilter('fileTypeFilter', facets.file_type || [], 'file_type');
    updateCheckboxFilter('dispositionFilter', facets.disposition || [], 'disposition');

    const months = (facets.published_month || []).slice(0, 12);
    const monthButtons = document.getElementById('monthButtons');
    monthButtons.innerHTML = months.map(f => `
        <button class="year-btn ${selectedFilters.months.includes(f.value) ? 'active' : ''}"
                data-value="${f.value}">${f.value} (${f.count})</button>`).join('');
    monthButtons.querySelectorAll('.year-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            toggleFilter('months', btn.dataset.value);
        });
    });
}

function updateCheckboxFilter(containerId, facets, filterKey) {
    const container = document.getElementById(containerId);
    const title = container.querySelector('.filter-title').outerHTML;
    container.innerHTML = title + facets.map(f => {
        const checked = selectedFilters[filterKey].includes(f.value) ? 'checked' : '';
        return `
            <div class="filter-option">
                <!-- filter-label carries flex:1 + min-width:0: without it a long
                     publisher name grows the row and pushes its count out of the
                     panel, which is a poor look generally and a contradiction
                     specifically, since PROJ-3 is about counts being visible. -->
                <label class="filter-label" title="${escapeHtml(f.label ?? f.value)}">
                    <input type="checkbox" ${checked} data-filter="${filterKey}" data-value="${escapeHtml(f.value)}">
                    <span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${escapeHtml(f.label ?? f.value) || '(leeg)'}</span>
                </label>
                <span class="filter-count">${f.count}</span>
            </div>`;
    }).join('');
    container.querySelectorAll('input[type=checkbox]').forEach(cb => {
        cb.addEventListener('change', () => toggleFilter(cb.dataset.filter, cb.dataset.value));
    });
}

function toggleFilter(key, value) {
    const list = selectedFilters[key];
    const i = list.indexOf(value);
    if (i >= 0) list.splice(i, 1); else list.push(value);
    currentPage = 1;
    performSearch();
}

function updatePagination(total) {
    const pages = Math.ceil(total / PAGE_SIZE);
    const el = document.getElementById('pagination');
    if (pages <= 1) { el.innerHTML = ''; return; }
    let html = '';
    const windowStart = Math.max(1, currentPage - 2);
    const windowEnd = Math.min(pages, currentPage + 2);
    if (currentPage > 1) html += `<button class="page-btn" data-page="${currentPage - 1}">←</button>`;
    for (let p = windowStart; p <= windowEnd; p++) {
        html += `<button class="page-btn ${p === currentPage ? 'active' : ''}" data-page="${p}">${p}</button>`;
    }
    if (currentPage < pages) html += `<button class="page-btn" data-page="${currentPage + 1}">→</button>`;
    el.innerHTML = html;
    el.querySelectorAll('.page-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            currentPage = parseInt(btn.dataset.page, 10);
            performSearch();
            window.scrollTo({ top: 0, behavior: 'smooth' });
        });
    });
}

function initializeModal() {
    const modal = document.getElementById('docModal');
    document.getElementById('modalClose').addEventListener('click', () => {
        modal.classList.remove('active');
    });
    modal.addEventListener('click', (e) => {
        if (e.target === modal) modal.classList.remove('active');
    });
}

async function openDocModal(urn) {
    const modal = document.getElementById('docModal');
    const body = document.getElementById('modalBody');
    document.getElementById('modalTitle').textContent = 'Laden…';
    body.innerHTML = '';
    modal.classList.add('active');
    try {
        const r = await fetch(`${API_BASE_URL}/api/doc/${encodeURIComponent(urn)}`);
        const doc = await r.json();
        document.getElementById('modalTitle').textContent = doc.title || urn;
        const meta = [
            doc.source_id ? { label: 'Bron', value: corpusLabel(doc.source_id) } : null,
            doc.published ? { label: 'Gepubliceerd', value: doc.published } : null,
            doc.publisher ? { label: 'Publisher', value: doc.publisher } : null,
            doc.pages ? { label: "Pagina's", value: doc.pages } : null,
            doc.extraction_method ? { label: 'Extractie', value: doc.extraction_method } : null,
        ].filter(Boolean);
        body.innerHTML = `
            ${doc.landing_url ? `<div class="modal-links"><a class="modal-link" target="_blank" href="${doc.landing_url}">📄 Bekijk op ${escapeHtml(hostOf(doc.landing_url))}</a></div>` : ''}
            <div class="modal-meta">
                ${meta.map(m => `<div class="modal-meta-item"><div class="modal-meta-label">${m.label}</div><div class="modal-meta-value">${escapeHtml(String(m.value))}</div></div>`).join('')}
            </div>
            <div class="modal-content-section" id="relatedSection"></div>
            <div class="modal-content-section">
                <div class="modal-content-title">Volledige tekst</div>
                <div class="modal-text" style="white-space:pre-wrap">${escapeHtml(doc.content || 'Geen tekst beschikbaar')}</div>
            </div>`;
        loadRelated(urn, doc.source_id);
    } catch (e) {
        body.innerHTML = `<p>Kon document niet laden: ${e}</p>`;
    }
}

// The source is no longer implied by the app, so it is shown — and a neighbour from
// a *different* corpus is called out, because that is the thing a per-source search
// cannot do at all.
function hostOf(url) {
    try { return new URL(url).hostname; } catch { return 'de bron'; }
}

async function loadRelated(urn, seedSource) {
    const section = document.getElementById('relatedSection');
    if (!section) return;
    try {
        const r = await fetch(`${API_BASE_URL}/api/doc/${encodeURIComponent(urn)}/related`);
        const data = await r.json();
        if (!data.related || !data.related.length) { section.remove(); return; }
        section.innerHTML = `
            <div class="modal-content-title">Vergelijkbare documenten</div>
            ${data.related.map(d => {
                const other = d.source_id && seedSource && d.source_id !== seedSource;
                const tag = d.source_id
                    ? `<span class="corpus-tag ${other ? 'cross-source' : ''}">${escapeHtml(corpusLabel(d.source_id))}</span>`
                    : '';
                return `
                <div class="filter-option" style="cursor:pointer" data-urn="${d.urn}">
                    <span>🔗 ${escapeHtml(d.title)} ${tag}</span>
                    <span class="filter-count">${d.published || ''}</span>
                </div>`;
            }).join('')}`;
        section.querySelectorAll('[data-urn]').forEach(el => {
            el.addEventListener('click', () => openDocModal(el.dataset.urn));
        });
    } catch { section.remove(); }
}

function escapeHtml(s) {
    return String(s ?? '').replace(/[&<>"']/g, c => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[c]));
}
