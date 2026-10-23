'use strict';

// ------------------------------------------------------------------ helpers
const $ = (id) => document.getElementById(id);
const el = (html) => { const t = document.createElement('template'); t.innerHTML = html.trim(); return t.content.firstChild; };
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const nf = (n) => (n ?? 0).toLocaleString('nl-NL');

function bytes(b) {
    b = Number(b || 0);
    if (b < 1024) return b + ' B';
    const u = ['KB', 'MB', 'GB', 'TB'];
    let i = -1;
    do { b /= 1024; i++; } while (b >= 1024 && i < u.length - 1);
    return b.toFixed(b >= 100 ? 0 : 1) + ' ' + u[i];
}

function ago(iso) {
    if (!iso) return '—';
    const d = new Date(iso), s = (Date.now() - d.getTime()) / 1000;
    if (s < 90) return Math.round(s) + 's geleden';
    if (s < 5400) return Math.round(s / 60) + 'm geleden';
    if (s < 129600) return Math.round(s / 3600) + 'u geleden';
    return Math.round(s / 86400) + 'd geleden';
}

function dur(s) {
    if (s == null) return '—';
    if (s < 90) return s.toFixed(0) + 's';
    if (s < 5400) return (s / 60).toFixed(1) + 'm';
    return (s / 3600).toFixed(1) + 'u';
}

function verdictBadge(v, unfinished) {
    if (unfinished || (!v)) return `<span class="m-badge running">bezig / geen</span>`;
    const label = { reconciled: 'reconciled', reconciled_with_warnings: 'warnings', NOT_reconciled: 'NIET reconciled' }[v] || v;
    return `<span class="m-badge ${esc(v)}">${esc(label)}</span>`;
}

const STAGES = ['discovered', 'fetched', 'extracted', 'enriched', 'indexed'];
const STAGE_ABBR = { discovered: 'disc', fetched: 'fetch', extracted: 'extr', enriched: 'enr', indexed: 'idx' };
const DISP_ORDER = ['ok', 'partial', 'deferred', 'failed', 'stub', 'superseded', 'gone_from_source', 'out_of_scope'];
const DISP_CLASS = { ok: 'ok', partial: 'partial', deferred: 'deferred', failed: 'failed', stub: 'stub' };

// ------------------------------------------------------------------ fetch
async function getJSON(url) {
    const r = await fetch(url);
    if (!r.ok) throw new Error(url + ' → ' + r.status);
    return r.json();
}

// ------------------------------------------------------------------ render: KPIs
function renderKPIs(t) {
    const tiles = [
        { v: nf(t.sources), l: 'Bronnen', sub: t.paused_sources ? `${t.paused_sources} gepauzeerd` : 'geen gepauzeerd', alert: t.paused_sources > 0 },
        { v: nf(t.items), l: 'Items totaal', sub: `${nf(t.indexed)} geïndexeerd` },
        { v: nf(t.open_queue), l: 'Open wachtrij', sub: 'uitgesteld werk', alert: t.open_queue > 0 },
        { v: bytes(t.disk_bytes), l: 'Op schijf', sub: `${nf(t.disk_blobs)} blobs` },
        { v: bytes(t.reclaimed_bytes), l: 'Teruggewonnen', sub: 'na distillatie' },
        { v: '€' + (t.spend_eur || 0).toFixed(2), l: 'Uitgaven', sub: 'cost ledger' },
        { v: nf(t.runs), l: 'Runs', sub: `${t.unfinished_runs} onvoltooid`, alert: t.unfinished_runs > 0 },
        { v: nf(t.not_reconciled_runs), l: 'NIET reconciled', sub: 'runs', alert: t.not_reconciled_runs > 0 },
    ];
    $('kpis').replaceChildren(...tiles.map((k) => el(
        `<div class="m-kpi ${k.alert ? 'alert' : ''}">
            <div class="m-kpi-value">${k.v}</div>
            <div class="m-kpi-label">${esc(k.l)}</div>
            <div class="m-kpi-sub">${esc(k.sub)}</div>
        </div>`)));
}

// ------------------------------------------------------------------ render: alerts
function renderAlerts(alerts) {
    const box = $('alerts');
    if (!alerts.length) {
        box.replaceChildren(el(`<div class="m-alert-ok">✓ Geen problemen — alle bronnen gezond, geen onvoltooide runs.</div>`));
        return;
    }
    box.replaceChildren(...alerts.map((a) => el(
        `<div class="m-alert ${a.level === 'red' ? 'red' : 'amber'}">
            <span>${a.level === 'red' ? '⛔' : '⚠️'}</span>
            ${a.source ? `<span class="m-alert-src">${esc(a.source)}</span>` : ''}
            <span>${esc(a.msg)}</span>
        </div>`)));
}

// ------------------------------------------------------------------ render: source cards
function funnelHTML(funnel) {
    const max = Math.max(1, ...STAGES.map((s) => funnel[s] || 0));
    return `<div class="m-funnel-wrap"><div class="m-funnel">` + STAGES.map((s) => {
        const n = funnel[s] || 0;
        const h = 40 + Math.round((n / max) * 60); // visual weight 40–100%
        return `<div class="m-funnel-seg" data-empty="${n ? 0 : 1}" style="opacity:${(h / 100).toFixed(2)}" title="${esc(s)}: ${nf(n)}">
            ${n ? nf(n) : ''}<span class="m-funnel-stage">${STAGE_ABBR[s]}</span></div>`;
    }).join('') + `</div></div>`;
}

function chips(map, klassFor) {
    const keys = Object.keys(map).filter((k) => map[k]);
    if (!keys.length) return '';
    return `<div class="m-chips">` + keys.map((k) =>
        `<span class="m-chip ${klassFor(k)}">${esc(k)} <b>${nf(map[k])}</b></span>`).join('') + `</div>`;
}

function renderSources(sources) {
    $('sourceCards').replaceChildren(...sources.map((s) => {
        const lr = s.last_run;
        const disp = {};
        DISP_ORDER.forEach((d) => { if (s.dispositions[d]) disp[d] = s.dispositions[d]; });
        const card = el(
            `<div class="m-card ${s.health}" data-src="${esc(s.id)}">
                <div class="m-card-head">
                    <div>
                        <div class="m-card-name">${esc(s.name)}</div>
                        <div class="m-card-id">${esc(s.id)}</div>
                    </div>
                    <div class="m-dot ${s.health}"></div>
                </div>
                <div class="m-card-total"><strong>${nf(s.items_total)}</strong> items · ${nf(s.indexed)} geïndexeerd</div>
                ${funnelHTML(s.funnel)}
                ${chips(disp, (k) => DISP_CLASS[k] || 'other')}
                ${Object.keys(s.missing).length ? `<div class="m-chip-label">ontbreekt</div>${chips(s.missing, () => 'miss')}` : ''}
                ${Object.keys(s.open_queue).length ? `<div class="m-chip-label">wachtrij</div>${chips(s.open_queue, () => 'queue')}` : ''}
                <div class="m-card-foot">
                    <span>${lr ? `laatste run #${lr.id} · ${ago(lr.finished_at || lr.started_at)}` : 'geen runs'}</span>
                    ${lr ? verdictBadge(lr.verdict, lr.unfinished) : ''}
                </div>
            </div>`);
        card.addEventListener('click', () => openSource(s.id));
        return card;
    }));
}

// ------------------------------------------------------------------ render: runs table
function renderRuns(runs) {
    if (!runs.length) { $('runsTable').innerHTML = '<div class="m-empty">Nog geen runs.</div>'; return; }
    const rows = runs.map((r) =>
        `<tr class="clickable" data-run="${r.id}">
            <td class="m-mono">#${r.id}</td>
            <td>${esc(r.source_id)}</td>
            <td>${esc(r.mode)}</td>
            <td class="m-mono">${esc((r.phases || []).join(',')) || '—'}</td>
            <td class="m-muted">${ago(r.finished_at || r.started_at)}</td>
            <td>${dur(r.duration_s)}</td>
            <td class="num">${r.harvested != null ? nf(r.harvested) : '—'}${r.claimed != null ? ' / ' + nf(r.claimed) : ''}</td>
            <td>${verdictBadge(r.verdict, r.finished_at == null)}</td>
        </tr>`).join('');
    $('runsTable').innerHTML =
        `<table class="m-table"><thead><tr>
            <th>Run</th><th>Bron</th><th>Modus</th><th>Fasen</th><th>Wanneer</th>
            <th>Duur</th><th class="num">Geoogst / geclaimd</th><th>Verdict</th>
        </tr></thead><tbody>${rows}</tbody></table>`;
    $('runsTable').querySelectorAll('tr[data-run]').forEach((tr) =>
        tr.addEventListener('click', () => openRun(tr.dataset.run)));
}

// ------------------------------------------------------------------ render: queues
function renderQueues(queues) {
    if (!queues.length) { $('queues').innerHTML = '<div class="m-empty">Geen wachtrij-items.</div>'; return; }
    $('queues').replaceChildren(...queues.map((q) => {
        const total = q.open + q.resolved || 1;
        return el(
            `<div class="m-meter-row">
                <div class="m-meter-head">
                    <span class="m-meter-name">${esc(q.reason)}</span>
                    <span><b>${nf(q.open)}</b> open<span class="m-muted"> / ${nf(q.resolved)} klaar</span></span>
                </div>
                <div class="m-meter-bar"><div class="m-meter-fill ${q.open ? 'open' : 'resolved'}" style="width:${Math.round((q.open / total) * 100)}%"></div></div>
                ${q.oldest_open ? `<div class="m-kpi-sub">oudste open: ${ago(q.oldest_open)}</div>` : ''}
            </div>`);
    }));
}

// ------------------------------------------------------------------ render: blobs
function renderBlobs(blobs) {
    const rows = blobs.by_reason;
    if (!rows.length) { $('blobs').innerHTML = '<div class="m-empty">Geen blobs.</div>'; return; }
    const max = Math.max(1, ...rows.map((r) => Number(r.bytes)));
    const label = (r) => r.kept_reason || (r.kept ? 'kept' : 'niet bewaard');
    $('blobs').replaceChildren(...rows.map((r) => {
        const onDisk = r.on_disk_count > 0;
        return el(
            `<div class="m-meter-row">
                <div class="m-meter-head">
                    <span class="m-meter-name">${esc(label(r))}</span>
                    <span>${bytes(r.bytes)}<span class="m-muted"> · ${nf(r.count)} blobs${onDisk ? '' : ' · schijf vrij'}</span></span>
                </div>
                <div class="m-meter-bar"><div class="m-meter-fill ${onDisk ? 'keep' : 'reclaim'}" style="width:${Math.round((Number(r.bytes) / max) * 100)}%"></div></div>
            </div>`);
    }));
}

// ------------------------------------------------------------------ render: projection
// Mapping drift (PROJ-2): the live civic-* mappings vs the declared schema. The
// distinction between the three findings is the point — wrong_type breaks queries
// now, undeclared means something wrote a field nobody designed, unmaterialised is
// harmless today but is a wrong_type waiting for its first document.
function renderProjection(d) {
    const box = $('projection');
    if (!d.available) {
        box.replaceChildren(el(
            `<div class="m-alert amber">⚠️ Projectie niet te controleren — ${esc(d.error || 'onbereikbaar')}</div>`));
        return;
    }
    if (!d.indices.length) {
        box.innerHTML = '<div class="m-empty">Geen civic-* indices gevonden.</div>';
        return;
    }
    const nodes = d.indices.map((i) => {
        const notes = [];
        i.wrong_type.forEach((w) => notes.push(
            `<span class="m-chip failed">${esc(w.field)}: ${esc(w.declared)} → <b>${esc(w.live)}</b></span>`));
        if (i.undeclared.length) notes.push(
            `<span class="m-chip miss">ongedeclareerd: ${esc(i.undeclared.join(', '))}</span>`);
        if (i.unmaterialised.length) notes.push(
            `<span class="m-chip stub">nog niet geschreven: ${esc(i.unmaterialised.join(', '))}</span>`);
        return el(
            `<div class="m-meter-row">
                <div class="m-meter-head">
                    <span class="m-meter-name m-mono">${esc(i.index)}</span>
                    <span class="m-badge ${i.clean ? 'reconciled' : 'NOT_reconciled'}">${i.clean ? 'ok' : 'DRIFT'}</span>
                </div>
                ${notes.length ? `<div class="m-chips">${notes.join('')}</div>` : ''}
            </div>`);
    });
    if (d.drifted) {
        nodes.push(el(`<div class="m-kpi-sub">Herstel: <code>${esc(d.fix)}</code> — de mapping wordt
            alleen bij aanmaak toegepast; Postgres houdt tekst en vectoren, dus een herbouw
            kost geen crawl en geen GPU.</div>`));
    }
    box.replaceChildren(...nodes);
}

// ------------------------------------------------------------------ render: costs
function renderCosts(costs) {
    const cats = costs.by_category;
    let html = '';
    if (!cats.length) { html = '<div class="m-empty">Geen kosten geregistreerd.</div>'; }
    else {
        html = `<table class="m-table"><thead><tr><th>Categorie</th><th class="num">Aantal</th><th class="num">€</th></tr></thead><tbody>` +
            cats.map((c) => `<tr><td>${esc(c.category)}</td><td class="num">${nf(c.amount)}</td><td class="num">${(c.eur || 0).toFixed(4)}</td></tr>`).join('') +
            `</tbody></table>`;
    }
    if (costs.budget_stops.length) {
        html += `<div class="m-chip-label" style="margin-top:1rem">budget-stops</div>` +
            costs.budget_stops.map((b) =>
                `<div class="m-empty" style="color:#7c2d12">⚠️ run #${b.run_id ?? '—'} ${esc(b.source_id || '')}: ${esc(b.note)}</div>`).join('');
    }
    $('costs').innerHTML = html;
}

// ------------------------------------------------------------------ modal: source detail
async function openSource(id) {
    showModal('Bron: ' + id, '<div class="m-loading">laden…</div>');
    try {
        const d = await getJSON('/api/monitor/source/' + encodeURIComponent(id));
        $('detailTitle').textContent = d.source.name;
        $('detailBody').innerHTML = sourceDetailHTML(d);
        $('detailBody').querySelectorAll('tr[data-run]').forEach((tr) =>
            tr.addEventListener('click', () => openRun(tr.dataset.run)));
    } catch (e) {
        $('detailBody').innerHTML = `<div class="m-empty">Fout: ${esc(e.message)}</div>`;
    }
}

function matrixHTML(matrix) {
    const present = DISP_ORDER.filter((d) => STAGES.some((s) => matrix[s] && matrix[s][d]));
    if (!present.length) return '<div class="m-empty">Geen items.</div>';
    const colTot = {}; present.forEach((d) => colTot[d] = 0);
    let body = STAGES.map((s) => {
        const row = matrix[s] || {};
        const rowTot = Object.values(row).reduce((a, b) => a + b, 0);
        if (!rowTot) return '';
        const cells = present.map((d) => { colTot[d] += (row[d] || 0); return `<td class="${row[d] ? '' : 'zero'}">${row[d] ? nf(row[d]) : '·'}</td>`; }).join('');
        return `<tr><td>${esc(s)}</td>${cells}<td><b>${nf(rowTot)}</b></td></tr>`;
    }).join('');
    const grand = present.reduce((a, d) => a + colTot[d], 0);
    const foot = `<tr><td>totaal</td>${present.map((d) => `<td>${nf(colTot[d])}</td>`).join('')}<td>${nf(grand)}</td></tr>`;
    return `<table class="m-matrix"><thead><tr><th>stage \\ dispositie</th>${present.map((d) => `<th>${esc(d)}</th>`).join('')}<th>Σ</th></tr></thead>
        <tbody>${body}</tbody><tfoot>${foot}</tfoot></table>`;
}

function sourceDetailHTML(d) {
    const s = d.source;
    let h = '';
    if (s.paused) h += `<div class="m-alert red" style="margin-bottom:1rem">⛔ Gepauzeerd: ${esc(s.paused_reason || '')}</div>`;
    h += `<div class="m-kpi-sub">${esc(s.id)}${s.base_url ? ' · <a href="' + esc(s.base_url) + '" target="_blank" rel="noopener">' + esc(s.base_url) + '</a>' : ''}</div>`;

    h += `<h3>Items — stage × dispositie</h3>${matrixHTML(d.matrix)}`;

    if (d.missing.length) {
        h += `<h3>Coverage-gaten (missing[])</h3><div class="m-chips">` +
            d.missing.map((m) => `<span class="m-chip miss">${esc(m.reason)} <b>${nf(m.count)}</b></span>`).join('') + `</div>`;
    }
    if (d.queue.length) {
        h += `<h3>Wachtrij</h3><div class="m-chips">` +
            d.queue.map((q) => `<span class="m-chip queue">${esc(q.reason)}: <b>${nf(q.open)}</b> open / ${nf(q.resolved)} klaar</span>`).join('') + `</div>`;
    }

    h += `<h3>Runs (${d.runs.length})</h3>`;
    if (d.runs.length) {
        h += `<table class="m-table"><thead><tr><th>Run</th><th>Modus</th><th>Wanneer</th><th>Duur</th>
            <th class="num">geoogst / geclaimd</th><th>Verdict</th></tr></thead><tbody>` +
            d.runs.map((r) => `<tr class="clickable" data-run="${r.id}">
                <td class="m-mono">#${r.id}</td><td>${esc(r.mode)}</td>
                <td class="m-muted">${ago(r.finished_at || r.started_at)}</td><td>${dur(r.duration_s)}</td>
                <td class="num">${r.harvested != null ? nf(r.harvested) : '—'}${r.claimed_last != null ? ' / ' + nf(r.claimed_last) : ''}</td>
                <td>${verdictBadge(r.verdict, r.finished_at == null)}</td></tr>`).join('') + `</tbody></table>`;
    } else h += '<div class="m-empty">Geen runs.</div>';

    if (d.cost.length) {
        h += `<h3>Kosten</h3><table class="m-table"><thead><tr><th>Categorie</th><th class="num">Aantal</th><th class="num">€</th></tr></thead><tbody>` +
            d.cost.map((c) => `<tr><td>${esc(c.category)}</td><td class="num">${nf(c.amount)}</td><td class="num">${(c.eur || 0).toFixed(4)}</td></tr>`).join('') + `</tbody></table>`;
    }
    if (d.blobs.length) {
        h += `<h3>Blobs</h3><table class="m-table"><thead><tr><th>Bucket</th><th class="num">Aantal</th><th class="num">Bytes</th><th class="num">Op schijf</th></tr></thead><tbody>` +
            d.blobs.map((b) => `<tr><td>${esc(b.bucket)}</td><td class="num">${nf(b.count)}</td><td class="num">${bytes(b.bytes)}</td><td class="num">${nf(b.on_disk_count)}</td></tr>`).join('') + `</tbody></table>`;
    }
    if (d.failed_items.length) {
        h += `<h3>Mislukte items (${d.failed_items.length})</h3><table class="m-table"><thead><tr><th>URN</th><th>Stage</th><th>Reden</th><th>Retry</th></tr></thead><tbody>` +
            d.failed_items.map((f) => `<tr><td class="m-mono">${esc(f.urn)}</td><td>${esc(f.stage)}</td><td>${esc(f.reason || '—')}</td><td>${f.retryable ? '✓' : '✕'}</td></tr>`).join('') + `</tbody></table>`;
    }
    return h;
}

// ------------------------------------------------------------------ modal: run report
async function openRun(id) {
    showModal('Run #' + id, '<div class="m-loading">laden…</div>');
    try {
        const d = await getJSON('/api/monitor/run/' + encodeURIComponent(id));
        $('detailTitle').textContent = `Run #${d.id} — ${d.source_name}`;
        $('detailBody').innerHTML = runReportHTML(d);
    } catch (e) {
        $('detailBody').innerHTML = `<div class="m-empty">Fout: ${esc(e.message)}</div>`;
    }
}

function runReportHTML(d) {
    const rec = (d.report && d.report.reconciliation) || {};
    let h = `<div class="m-chips">
        <span class="m-chip">${esc(d.mode)}</span>
        <span class="m-chip">fasen: ${esc((d.phases || []).join(',') || '—')}</span>
        <span class="m-chip">duur: ${dur(d.duration_s)}</span>
        ${verdictBadge(d.verdict, d.finished_at == null)}
    </div>`;
    h += `<h3>Reconciliatie</h3><table class="m-table"><tbody>
        <tr><td>claimed (eerste → laatste)</td><td class="num">${nf(d.claimed_total_first)} → ${nf(d.claimed_total_last)}</td></tr>
        <tr><td>geoogst</td><td class="num">${nf(d.harvested_total)}</td></tr>
        <tr><td>som van leaves</td><td class="num">${nf(d.sum_of_leaves)}</td></tr>
        ${rec.scope ? `<tr><td>scope</td><td class="num">${esc(JSON.stringify(rec.scope))}</td></tr>` : ''}
        </tbody></table>`;
    if (rec.warnings && rec.warnings.length) {
        h += `<h3>Waarschuwingen</h3>` + rec.warnings.map((w) => `<div class="m-alert amber" style="margin-bottom:0.375rem">⚠️ ${esc(w)}</div>`).join('');
    }
    if (d.partitions.length) {
        h += `<h3>Partities (${d.partitions.length})</h3><table class="m-table"><thead><tr><th>Leaf</th>
            <th class="num">claimed</th><th class="num">geoogst</th><th>cap</th><th>subdiv</th></tr></thead><tbody>` +
            d.partitions.map((p) => `<tr><td class="m-mono">${esc(p.leaf)}</td>
                <td class="num">${p.claimed != null ? nf(p.claimed) : '—'}</td><td class="num">${nf(p.harvested)}</td>
                <td>${p.cap_hit ? '⚠️' : ''}</td><td>${p.subdivided ? '✓' : ''}</td></tr>`).join('') + `</tbody></table>`;
    }
    const report = d.report || {};
    if (report.outcomes) h += `<h3>Outcomes</h3><pre class="m-json">${esc(JSON.stringify(report.outcomes, null, 2))}</pre>`;
    if (report.cost) h += `<h3>Kosten</h3><pre class="m-json">${esc(JSON.stringify(report.cost, null, 2))}</pre>`;
    if (report.selector_health) h += `<h3>Selector-health</h3><pre class="m-json">${esc(JSON.stringify(report.selector_health, null, 2))}</pre>`;
    return h;
}

// ------------------------------------------------------------------ modal plumbing
function showModal(title, body) {
    $('detailTitle').textContent = title;
    $('detailBody').innerHTML = body;
    $('detailModal').classList.add('show');
}
function closeModal() { $('detailModal').classList.remove('show'); }

// ------------------------------------------------------------------ load loop
let timer = null;

// The two fetches are independent on purpose: /overview is Postgres-only and
// /projection is the only OpenSearch call on the page, so a dead cluster must cost
// one panel rather than the dashboard.
async function load() {
    const [ov, proj] = await Promise.allSettled([
        getJSON('/api/monitor/overview'),
        getJSON('/api/monitor/projection'),
    ]);

    const projection = proj.status === 'fulfilled'
        ? proj.value
        : { available: false, error: proj.reason.message, indices: [], drifted: 0 };
    renderProjection(projection);

    // Drift belongs in the alert strip, not only in its panel: a partial shard
    // failure is an HTTP 200, so nothing else on this page would ever show it.
    // "Geen problemen" must not be printed while an index disagrees with its schema.
    const driftAlerts = [];
    if (!projection.available) {
        driftAlerts.push({ level: 'amber', source: null,
            msg: `index-schema niet te controleren: ${projection.error}` });
    } else if (projection.drifted) {
        const names = projection.indices.filter((i) => !i.clean).map((i) => i.index);
        driftAlerts.push({ level: 'red', source: null,
            msg: `${projection.drifted} index/indices wijken af van het gedeclareerde `
               + `schema (${names.join(', ')}) — aggregaties en filters falen daar stil` });
    }

    if (ov.status !== 'fulfilled') {
        $('alerts').replaceChildren(el(`<div class="m-alert red">⛔ Kan monitor niet laden: ${esc(ov.reason.message)}</div>`),
            ...driftAlerts.map((a) => el(`<div class="m-alert ${a.level}"><span>⚠️</span><span>${esc(a.msg)}</span></div>`)));
        return;
    }
    const d = ov.value;
    renderAlerts([...driftAlerts, ...d.alerts]);
    renderKPIs(d.totals);
    renderSources(d.sources);
    renderRuns(d.recent_runs);
    renderQueues(d.queues);
    renderBlobs(d.blobs);
    renderCosts(d.costs);
    $('generatedAt').textContent = 'bijgewerkt ' + new Date(d.generated_at).toLocaleTimeString('nl-NL');
}

function setAutoRefresh(on) {
    if (timer) { clearInterval(timer); timer = null; }
    if (on) timer = setInterval(load, 30000);
}

document.addEventListener('DOMContentLoaded', () => {
    $('refreshBtn').addEventListener('click', load);
    $('detailClose').addEventListener('click', closeModal);
    $('detailModal').addEventListener('click', (e) => { if (e.target.id === 'detailModal') closeModal(); });
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeModal(); });
    $('autoRefresh').addEventListener('change', (e) => setAutoRefresh(e.target.checked));
    load();
});
