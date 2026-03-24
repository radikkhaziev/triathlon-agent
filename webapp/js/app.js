/* Triathlon Dashboard — Main App Logic */

const API_BASE = window.API_BASE_URL || '';
const tg = window.Telegram?.WebApp;

// Expand Telegram Mini App to full height
if (tg) {
    tg.expand();
    tg.ready();
}

function getInitData() {
    return tg?.initData || '';
}

async function apiFetch(endpoint) {
    const headers = {};
    const initData = getInitData();
    if (initData) headers['Authorization'] = initData;

    const res = await fetch(`${API_BASE}${endpoint}`, { headers });
    if (!res.ok) throw new Error(`API error: ${res.status}`);
    return res.json();
}

/* --- Tab switching --- */
document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
        document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
        document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
        btn.classList.add('active');
        document.getElementById(`tab-${btn.dataset.tab}`).classList.add('active');
        loadTab(btn.dataset.tab);
    });
});

const loadedTabs = new Set();

async function loadTab(name) {
    if (loadedTabs.has(name)) return;
    loadedTabs.add(name);

    try {
        switch (name) {
            case 'today': await loadToday(); break;
            case 'load': await loadLoadTab(); break;
            case 'goal': await loadGoalTab(); break;
            case 'week': await loadWeekTab(); break;
        }
    } catch (err) {
        console.error(`Failed to load tab "${name}":`, err);
    }
}

/* --- Today Tab --- */
async function loadToday() {
    const data = await apiFetch('/api/dashboard');
    const container = document.getElementById('tab-today');

    if (!data.has_data) {
        container.innerHTML = '<div class="no-data">No data for today. Use /sync in the bot first.</div>';
        return;
    }

    // Readiness gauge
    const level = data.readiness_level || 'yellow';
    const score = data.readiness_score || 0;
    drawReadinessGauge('readiness-gauge', score, level);
    document.getElementById('gauge-score').textContent = score;

    // Metric cards
    const hrvDelta = data.hrv_baseline ? ((data.hrv_last - data.hrv_baseline) / data.hrv_baseline * 100).toFixed(0) : '—';
    document.getElementById('metric-hrv').textContent = `${hrvDelta > 0 ? '+' : ''}${hrvDelta}%`;
    document.getElementById('metric-sleep').textContent = data.sleep_score ?? '—';
    document.getElementById('metric-rhr').textContent = data.resting_hr ? `${data.resting_hr.toFixed(0)}` : '—';

    // Training load
    document.getElementById('metric-ctl').textContent = data.ctl?.toFixed(0) ?? '—';
    document.getElementById('metric-atl').textContent = data.atl?.toFixed(0) ?? '—';
    document.getElementById('metric-tsb').textContent = data.tsb != null ? (data.tsb >= 0 ? '+' : '') + data.tsb.toFixed(0) : '—';

    // AI recommendation
    const aiEl = document.getElementById('ai-recommendation');
    aiEl.textContent = data.ai_recommendation || 'No recommendation available.';
}

/* --- Load Tab --- */
async function loadLoadTab() {
    const [loadData, actData] = await Promise.all([
        apiFetch('/api/training-load?days=84'),
        apiFetch('/api/activities?days=28'),
    ]);

    if (loadData.dates?.length) {
        createLoadChart('load-chart', loadData);
    }

    if (actData.activities?.length) {
        createTssBarChart('tss-chart', actData.activities);
    }
}

/* --- Goal Tab --- */
async function loadGoalTab() {
    const [goalData, loadData] = await Promise.all([
        apiFetch('/api/goal'),
        apiFetch('/api/training-load?days=84'),
    ]);

    document.getElementById('goal-event-name').textContent = goalData.event_name;
    document.getElementById('goal-weeks').textContent = goalData.weeks_remaining;

    setProgress('swim', goalData.swim_pct);
    setProgress('bike', goalData.bike_pct);
    setProgress('run', goalData.run_pct);

    if (loadData.dates?.length) {
        createGoalTrendChart('goal-trend-chart', loadData);
    }
}

function setProgress(sport, pct) {
    const fill = document.querySelector(`.progress-fill.${sport}`);
    const pctEl = document.getElementById(`pct-${sport}`);
    if (fill) fill.style.width = `${Math.min(100, pct)}%`;
    if (pctEl) pctEl.textContent = `${pct.toFixed(0)}%`;
}

/* --- Week Tab --- */
async function loadWeekTab() {
    const [weekData, schedData] = await Promise.all([
        apiFetch('/api/weekly-summary'),
        apiFetch('/api/scheduled?days=7'),
    ]);

    const tbody = document.getElementById('week-tbody');
    tbody.innerHTML = '';

    const sportEmoji = { swimming: '\u{1F3CA}', cycling: '\u{1F6B4}', running: '\u{1F3C3}' };

    if (schedData.workouts?.length) {
        for (const w of schedData.workouts) {
            const tr = document.createElement('tr');
            const emoji = sportEmoji[w.sport] || '\u{1F3CB}';
            tr.innerHTML = `
                <td>${w.date}</td>
                <td>${emoji} ${w.workout_name}</td>
                <td>${w.planned_tss ? w.planned_tss.toFixed(0) : '—'}</td>
            `;
            tbody.appendChild(tr);
        }
    } else {
        tbody.innerHTML = '<tr><td colspan="3" style="text-align:center;color:var(--hint)">No scheduled workouts</td></tr>';
    }

    // Weekly summary
    const summaryEl = document.getElementById('week-summary');
    const sports = weekData.by_sport || {};
    let totalTss = 0;
    let totalDuration = 0;
    const lines = [];

    for (const [sport, s] of Object.entries(sports)) {
        const emoji = sportEmoji[sport] || '\u{1F3CB}';
        const hours = (s.duration_sec / 3600).toFixed(1);
        const km = (s.distance_m / 1000).toFixed(1);
        lines.push(`${emoji} ${sport}: ${hours}h, ${km}km, TSS ${s.tss.toFixed(0)}`);
        totalTss += s.tss;
        totalDuration += s.duration_sec;
    }

    lines.push(`\nTotal: ${(totalDuration / 3600).toFixed(1)}h, TSS ${totalTss.toFixed(0)}`);
    summaryEl.textContent = lines.join('\n');
}

// Load the default tab on startup
loadTab('today');
