'use strict';

// Onboarding GUI (roadmap W6). The agent runs server-side as one asyncio task per source
// (design §D); this page polls it. Polling, not streaming, is deliberate: a reload or a
// closed laptop must never be able to lose a run that costs money.

// ------------------------------------------------------------------ helpers
const $ = (id) => document.getElementById(id);
const el = (html) => { const t = document.createElement('template'); t.innerHTML = html.trim(); return t.content.firstChild; };
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

const PHASES = [
    { n: 0, label: 'Intake' },
    { n: 1, label: 'Verkenning' },
    { n: 2, label: 'Scoping' },
    { n: 3, label: 'Batterij' },
    { n: 4, label: 'Plan', gate: true },
    { n: 5, label: 'Testrun' },
    { n: 6, label: 'Resultaten', gate: true },
    { n: 7, label: 'Productie', gate: true },
    { n: 8, label: 'Backfill' },
];
const STATUS_LABEL = {
    running: 'bezig', awaiting_gate: 'wacht op besluit', finished: 'klaar',
    refused: 'geweigerd', stopped: 'gestopt', failed: 'mislukt', stored: 'opgeslagen',
};
const VERDICT_LABEL = {
    approved: 'goedgekeurd', changes_requested: 'wijzigingen gevraagd', refused: 'geweigerd',
};

const state = {
    source: null, lastSeq: 0, events: [], timer: null, phase: null, status: null,
    gateKey: null,   // which gate the card currently shows; see renderGate
};

async function getJSON(url) {
    const r = await fetch(url);
    const body = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(body.error || (url + ' → ' + r.status));
    return body;
}

async function postJSON(url, payload) {
    const r = await fetch(url, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload || {}),
    });
    const body = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(body.error || (url + ' → ' + r.status));
    return body;
}

// ------------------------------------------------------------------ session list
async function loadSessions() {
    const data = await getJSON('/api/onboard/sessions');
    $('driverHint').textContent = data.sdk_available
        ? 'De sdk-driver draait de echte agent: live verzoeken en API-kosten (~$3,40 per run).'
        : 'De sdk-driver is hier niet beschikbaar (claude-agent-sdk / API-sleutel ontbreekt); replay werkt altijd.';
    const list = $('sessionList');
    if (!data.sessions.length) {
        list.replaceChildren(el('<p class="o-hint">Nog geen sessies.</p>'));
        return;
    }
    list.replaceChildren(...data.sessions.map((s) => {
        const node = el(`<button class="o-session ${s.source_id === state.source ? 'active' : ''}">
            <span class="o-session-id">${esc(s.source_id)}</span>
            <span class="o-session-meta">
                <span class="o-badge ${esc(s.status)}">${esc(STATUS_LABEL[s.status] || s.status)}</span>
                <span>fase ${s.phase}</span>
                <span>${s.interventions} interventie(s)</span>
                ${s.pending_gate ? '<span>⚖ poort open</span>' : ''}
            </span>
        </button>`);
        node.addEventListener('click', () => select(s.source_id));
        return node;
    }));
}

// ------------------------------------------------------------------ run view
// The selected session lives in the URL (`/onboard?source=…`), as in the search UI: a run
// is a thing you link a colleague to, and a reload during a paused gate must land back on
// that gate rather than on an empty page.
function select(sourceId, { push = true } = {}) {
    if (push && new URLSearchParams(location.search).get('source') !== sourceId) {
        history.pushState({ source: sourceId }, '', `/onboard?source=${encodeURIComponent(sourceId)}`);
    }
    state.source = sourceId;
    state.lastSeq = 0;
    state.events = [];
    state.phase = null;
    state.status = null;
    state.gateKey = null;
    $('emptyState').hidden = true;
    $('runView').hidden = false;
    $('events').replaceChildren();
    loadSessions();
    loadArtifacts();
    poll();
}

async function poll() {
    if (!state.source) return;
    clearTimeout(state.timer);
    let data;
    try {
        data = await getJSON(`/api/onboard/session/${encodeURIComponent(state.source)}?since=${state.lastSeq}`);
    } catch (e) {
        $('pollState').textContent = 'fout: ' + e.message;
        state.timer = setTimeout(poll, 5000);
        return;
    }
    render(data);
    // Artifacts change at phase boundaries, not per event — refetch only when they can
    // have changed, so polling a long run stays cheap.
    if (data.session.phase !== state.phase || data.status !== state.status) {
        state.phase = data.session.phase;
        state.status = data.status;
        loadSessions();
        loadArtifacts();
    }
    $('pollState').textContent = data.live ? 'live — ververst elke 2s' : 'niet live';
    state.timer = data.live ? setTimeout(poll, 2000) : null;
}

function render(data) {
    const s = data.session;
    $('runSource').textContent = data.source_id;
    $('runStatus').textContent = STATUS_LABEL[data.status] || data.status;
    $('runStatus').className = 'o-badge ' + data.status;
    $('runDriver').textContent = data.driver ? `driver: ${data.driver}` : 'opgeslagen sessie';
    $('runDriver').className = 'o-badge driver ' + (data.driver || '');
    $('runIntent').textContent = data.intent || '';
    $('stopBtn').hidden = !data.live;

    const overBudget = s.intervention_count > 5;
    $('runStats').replaceChildren(...[
        { v: s.phase, l: 'fase' },
        { v: `${s.intervention_count} / 5`, l: 'interventies', alert: overBudget },
        { v: '$' + (s.agent_cost_usd || 0).toFixed(2), l: 'agent-kosten' },
        { v: '€' + (s.cost_eur || 0).toFixed(2), l: 'crawl-kosten' },
        { v: s.acceptable ? 'ja' : 'nee', l: 'acceptabel', alert: !s.acceptable },
    ].map((k) => el(`<div class="o-stat ${k.alert ? 'alert' : ''}">
        <div class="o-stat-value">${esc(k.v)}</div><div class="o-stat-label">${esc(k.l)}</div></div>`)));

    $('phaseRail').replaceChildren(...PHASES.map((p) => el(
        `<div class="o-phase ${p.gate ? 'gate' : ''} ${s.phase > p.n ? 'done' : ''} ${s.phase === p.n ? 'current' : ''}">
            <span class="o-phase-num">${p.n}</span>${esc(p.label)}</div>`)));

    renderGate(s.pending_gate, data.status);
    renderAcceptance(data.acceptance_problems, s);
    appendEvents(data.events);
    // A stored (process-restarted) run reports last_seq 0 while its events carry real
    // sequence numbers — take the max of both, or a refresh re-appends the whole log.
    const highest = (data.events || []).reduce((m, e) => Math.max(m, e.seq || 0), 0);
    state.lastSeq = Math.max(state.lastSeq, data.last_seq || 0, highest);

    renderInterventions(s.interventions || []);
    renderSurfaceGate(s.gate);
    renderGateHistory(s.verification_gates || []);
}

// ------------------------------------------------------------------ the gate card
function renderGate(gate, status) {
    const card = $('gateCard');
    if (!gate) {
        card.hidden = true; card.replaceChildren(); state.gateKey = null; return;
    }
    // Re-rendering an unchanged gate would wipe the note being typed into it — and the
    // page polls every 2s while it waits. Render a gate once; leave it alone after that.
    const key = `${gate.phase}|${gate.requested_at}`;
    if (key === state.gateKey) return;
    state.gateKey = key;
    card.hidden = false;
    card.replaceChildren(el(`<div>
        <h2>⚖ Verificatiepoort — fase ${gate.phase}</h2>
        <p class="o-gate-summary">${esc(gate.summary)}</p>
        <p class="o-gate-detail">${esc(gate.detail || '')}</p>
        <label class="o-hint" for="gateNote">Toelichting (verplicht — bij elk besluit, ook bij goedkeuren)</label>
        <textarea id="gateNote" rows="3" placeholder="waarom besluit u dit?"></textarea>
        <div class="o-verdicts">
            <button class="approve" data-v="approved">Goedkeuren</button>
            <button class="changes" data-v="changes_requested">Wijzigingen vragen</button>
            <button class="refuse" data-v="refused">Weigeren</button>
        </div>
        <p class="o-hint">Weigeren stopt de run en blijft in het sessie-artefact staan: de bron
           kan niet in productie zolang dat besluit het laatste woord is.</p>
        <p class="o-error" id="gateError"></p>
    </div>`));
    card.querySelectorAll('.o-verdicts button').forEach((b) => {
        b.addEventListener('click', () => decide(b.dataset.v));
    });
    if (status !== 'awaiting_gate') {
        card.querySelectorAll('.o-verdicts button').forEach((b) => { b.disabled = true; });
        $('gateError').textContent =
            'Deze poort staat open, maar er draait geen agent meer — start de run opnieuw om te kunnen besluiten.';
    }
}

async function decide(verdict) {
    const note = $('gateNote').value.trim();
    if (!note) {
        $('gateError').textContent = 'Een toelichting is verplicht: het artefact moet het waarom vasthouden.';
        return;
    }
    $('gateError').textContent = '';
    try {
        await postJSON(`/api/onboard/session/${encodeURIComponent(state.source)}/gate`,
                       { verdict, note });
    } catch (e) {
        $('gateError').textContent = e.message;
        return;
    }
    poll();
}

function renderAcceptance(problems, session) {
    const box = $('acceptance');
    if (!problems || !problems.length) {
        box.replaceChildren(el(`<div class="o-ok">Acceptatiecontrole (sessiehelft) is schoon —
            interventiebudget, oppervlak-poort, kosten en poortbesluiten. De engine bewaakt de
            andere helft: een <code>reconciled</code> run-verdict.</div>`));
        return;
    }
    box.replaceChildren(...problems.map((p) => el(`<div class="o-problem">${esc(p)}</div>`)));
}

// ------------------------------------------------------------------ events
function appendEvents(events) {
    if (!events || !events.length) return;
    const box = $('events');
    const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 60;
    events.forEach((e) => {
        state.events.push(e);
        box.appendChild(el(`<div class="o-event ${esc(e.kind)}">
            <div class="o-event-kind">${esc(e.kind)}</div>
            <div class="o-event-body">${esc(eventText(e))}</div>
        </div>`));
    });
    if (atBottom) box.scrollTop = box.scrollHeight;
}

function eventText(e) {
    if (e.kind === 'tool_use') {
        const args = e.meta && e.meta.args ? JSON.stringify(e.meta.args) : '';
        return `${e.text}(${args})`;
    }
    return e.text;
}

// ------------------------------------------------------------------ side panels
function renderInterventions(entries) {
    const box = $('interventions');
    if (!entries.length) {
        box.replaceChildren(el('<p class="o-hint">Nog geen interventies — dat is het doel.</p>'));
        return;
    }
    box.replaceChildren(el(`<div class="o-list">${entries.map((i) => `
        <div class="o-item ${esc(i.category)}">
            <div class="o-item-head">${esc(i.category)}${i.phase != null ? ` · fase ${i.phase}` : ''}
                ${i.category === 'GATE' ? ' · telt niet mee' : ''}</div>
            <div class="o-item-note">${esc(i.note)}</div>
        </div>`).join('')}</div>`));
}

function renderSurfaceGate(gate) {
    const box = $('surfaceGate');
    if (!gate) {
        box.replaceChildren(el(`<p class="o-hint">Nog niet gedraaid. De agent moet probe 0
            uitvoeren vóór hij een scrape-oppervlak mag kiezen.</p>`));
        return;
    }
    const obligations = (gate.obligations || []).map((o) => `
        <div class="o-item ${o.met ? 'GATE' : 'DIAGNOSIS'}">
            <div class="o-item-head">${o.met ? '✓' : '○'} ${esc(o.key)}</div>
            <div class="o-item-note">${esc(o.detail)}</div>
        </div>`).join('');
    box.replaceChildren(el(`<div>
        <dl class="o-kv">
            <dt>gekozen</dt><dd>${esc(gate.chosen_surface)}</dd>
            <dt>status</dt><dd>${esc(gate.status)}</dd>
            <dt>aanbevolen</dt><dd>${gate.recommended
                ? esc(`${gate.recommended.surface} @ ${gate.recommended.host}`) : '—'}</dd>
        </dl>
        <p class="o-hint" style="margin-top:.5rem">${esc(gate.reason)}</p>
        ${obligations ? `<div class="o-list" style="margin-top:.75rem">${obligations}</div>` : ''}
    </div>`));
}

function renderGateHistory(gates) {
    const box = $('gateHistory');
    if (!gates.length) {
        box.replaceChildren(el('<p class="o-hint">Nog geen poortbesluiten.</p>'));
        return;
    }
    box.replaceChildren(el(`<div class="o-list">${gates.map((g) => `
        <div class="o-item ${g.verdict === 'refused' ? 'DIAGNOSIS' : 'GATE'}">
            <div class="o-item-head">fase ${g.phase} · ${g.pending ? 'OPEN'
                : esc((VERDICT_LABEL[g.verdict] || g.verdict).toUpperCase())}
                ${g.admin ? ` · ${esc(g.admin)}` : ''}${g.decided_at ? ` · ${esc(g.decided_at.slice(0, 16).replace('T', ' '))}` : ''}</div>
            <div class="o-item-note">${esc(g.summary)}</div>
            ${g.note ? `<div class="o-item-note"><em>${esc(g.note)}</em></div>` : ''}
        </div>`).join('')}</div>`));
}

// ------------------------------------------------------------------ artifacts
async function loadArtifacts() {
    if (!state.source) return;
    const id = encodeURIComponent(state.source);
    const [probes, draft] = await Promise.all([
        getJSON(`/api/onboard/session/${id}/probes`).catch((e) => ({ error: e.message, artifacts: [] })),
        getJSON(`/api/onboard/session/${id}/manifest`).catch((e) => ({ error: e.message })),
    ]);
    renderProbes(probes);
    renderManifest(draft);
}

function renderProbes(data) {
    const box = $('probes');
    if (!data.artifacts || !data.artifacts.length) {
        box.replaceChildren(el(`<p class="o-hint">Nog geen probe-artefacten in
            <code>${esc(data.dir || '')}</code>.</p>`));
        return;
    }
    box.replaceChildren(...data.artifacts.map((a) => {
        const p = a.payload || {};
        const rows = (p.surfaces || p.probes || []).map((s) => `
            <tr>
                <td>${esc(s.surface || '')}</td>
                <td>${esc(s.host || '')}</td>
                <td class="${esc(s.status || '')}">${esc(s.status || '')}</td>
                <td>${esc(s.rank_class || '')}</td>
                <td>${esc(s.url || '')}</td>
            </tr>`).join('');
        return el(`<div style="margin-bottom:1rem">
            <div class="o-item-head">${esc(a.name)} — verdict: ${esc(p.verdict || '—')}
                ${p.enforceable === false ? ' · NIET afdwingbaar (fail-closed)' : ''}</div>
            ${policyHtml(p.policy_flags)}
            ${rows ? `<table class="o-surfaces">
                <thead><tr><th>oppervlak</th><th>host</th><th>status</th><th>klasse</th><th>url</th></tr></thead>
                <tbody>${rows}</tbody></table>`
                : '<p class="o-hint">geen oppervlakken in dit artefact</p>'}
            <details class="o-details"><summary>ruwe JSON</summary>
                <pre class="o-code">${esc(JSON.stringify(p, null, 2))}</pre></details>
        </div>`);
    }));
}

// The robots posture is the one probe-0 output that must never be a JSON row the admin has
// to go looking for: four sources shipped against `Disallow: /` hosts before anything
// recorded it (POL-1). It is recorded, not gated — the decision stays the admin's.
function policyHtml(flags) {
    if (!flags) return '';
    const disallow = flags.blanket_disallow || [];
    const avoidable = flags.disallow_avoidable_via || [];
    if (!disallow.length && !(flags.undetermined || []).length) {
        return `<p class="o-hint">robots.txt gecontroleerd op
                ${esc((flags.robots_checked || []).join(', ') || '—')} — geen blanket Disallow.</p>`;
    }
    return `<div class="o-problem" style="margin:.5rem 0">
        ${disallow.length ? `<strong>robots.txt verbiedt ons op:</strong> ${esc(disallow.join(', '))}.
            Dit is een admin-besluit (<code>policy.admin_decision</code>), geen agent-besluit.` : ''}
        ${(flags.undetermined || []).length
            ? `<div>robots.txt onbepaald voor ${esc(flags.undetermined.join(', '))} — fail-closed lezen.</div>` : ''}
        ${avoidable.length
            ? `<div>Een aggregator dekt een deel hiervan: ${esc(avoidable.join(', '))} — de vraag
               krimpt, hij verdwijnt niet.</div>` : ''}
    </div>`;
}

function renderManifest(data) {
    const box = $('manifest');
    if (data.error) { box.replaceChildren(el(`<p class="o-error">${esc(data.error)}</p>`)); return; }
    if (!data.exists) { box.replaceChildren(el(`<p class="o-hint">${esc(data.note)}</p>`)); return; }
    const problems = (data.problems || []).map((p) => `<div class="o-problem">${esc(p)}</div>`).join('');
    const markers = (data.open_probe_markers || []).map((p) =>
        `<div class="o-item DIAGNOSIS"><div class="o-item-note">${esc(p)}</div></div>`).join('');
    box.replaceChildren(el(`<div>
        <p class="o-hint">${esc(data.path)}${data.stored_version
            ? ` · opgeslagen versie ${data.stored_version}` : ' · nog niet opgeslagen'}</p>
        ${problems ? `<div class="o-acceptance">${problems}</div>` : ''}
        ${markers ? `<h3 class="o-section-title" style="margin-top:1rem">Open TODO(probe)-markers</h3>
            <div class="o-list">${markers}</div>` : ''}
        ${data.diff ? `<h3 class="o-section-title" style="margin-top:1rem">Diff t.o.v. opgeslagen versie
            (+${data.diff.added} / −${data.diff.removed})</h3>
            <pre class="o-code o-diff">${diffHtml(data.diff.unified)}</pre>` : ''}
        <details class="o-details"><summary>volledig manifest (YAML)</summary>
            <pre class="o-code">${esc(data.yaml || '')}</pre></details>
        <p class="o-hint" style="margin-top:.5rem">Het manifest wordt hier niet bewerkt: vraag
           wijzigingen via de poort, dan blijft elke wijziging van de agent én met reden.</p>
    </div>`));
}

function diffHtml(unified) {
    return (unified || '').split('\n').map((line) => {
        const cls = line.startsWith('+') ? 'add' : line.startsWith('-') ? 'del'
            : line.startsWith('@@') ? 'hunk' : '';
        return cls ? `<span class="${cls}">${esc(line)}</span>` : esc(line);
    }).join('\n');
}

// ------------------------------------------------------------------ intake
$('intakeForm').addEventListener('submit', async (ev) => {
    ev.preventDefault();
    $('intakeError').textContent = '';
    $('startBtn').disabled = true;
    try {
        const data = await postJSON('/api/onboard/sessions', {
            url: $('url').value.trim(),
            intent: $('intent').value.trim(),
            source_id: $('sourceId').value.trim() || undefined,
            driver: $('driver').value,
        });
        select(data.source_id);
    } catch (e) {
        $('intakeError').textContent = e.message;
    } finally {
        $('startBtn').disabled = false;
    }
});

$('stopBtn').addEventListener('click', async () => {
    if (!state.source) return;
    await postJSON(`/api/onboard/session/${encodeURIComponent(state.source)}/stop`).catch(() => {});
    poll();
});

$('refreshBtn').addEventListener('click', () => { loadSessions(); poll(); loadArtifacts(); });

window.addEventListener('popstate', () => {
    const source = new URLSearchParams(location.search).get('source');
    if (source) select(source, { push: false });
});

loadSessions();
const initial = new URLSearchParams(location.search).get('source');
if (initial) select(initial, { push: false });
