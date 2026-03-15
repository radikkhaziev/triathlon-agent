/* Chart.js helpers for the Triathlon Dashboard */

const CHART_COLORS = {
    ctl: 'rgb(59, 130, 246)',
    atl: 'rgb(239, 68, 68)',
    tsb: 'rgb(34, 197, 94)',
    swim: 'rgb(59, 130, 246)',
    bike: 'rgb(34, 197, 94)',
    run: 'rgb(245, 158, 11)',
    grid: 'rgba(128, 128, 128, 0.15)',
    text: getComputedStyle(document.documentElement).getPropertyValue('--hint').trim() || '#999',
};

const CHART_DEFAULTS = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
        legend: {
            position: 'top',
            labels: { boxWidth: 12, padding: 8, font: { size: 11 } },
        },
    },
    scales: {
        x: {
            grid: { color: CHART_COLORS.grid },
            ticks: { font: { size: 10 }, color: CHART_COLORS.text, maxRotation: 45 },
        },
        y: {
            grid: { color: CHART_COLORS.grid },
            ticks: { font: { size: 10 }, color: CHART_COLORS.text },
        },
    },
};

function createLoadChart(canvasId, data) {
    const ctx = document.getElementById(canvasId);
    if (!ctx) return null;

    const labels = data.dates.map(d => {
        const parts = d.split('-');
        return `${parts[1]}/${parts[2]}`;
    });

    return new Chart(ctx, {
        type: 'line',
        data: {
            labels,
            datasets: [
                {
                    label: 'CTL (Fitness)',
                    data: data.ctl,
                    borderColor: CHART_COLORS.ctl,
                    backgroundColor: CHART_COLORS.ctl + '20',
                    fill: false,
                    tension: 0.3,
                    pointRadius: 0,
                    borderWidth: 2,
                },
                {
                    label: 'ATL (Fatigue)',
                    data: data.atl,
                    borderColor: CHART_COLORS.atl,
                    fill: false,
                    tension: 0.3,
                    pointRadius: 0,
                    borderWidth: 2,
                },
                {
                    label: 'TSB (Form)',
                    data: data.tsb,
                    borderColor: CHART_COLORS.tsb,
                    backgroundColor: CHART_COLORS.tsb + '15',
                    fill: true,
                    tension: 0.3,
                    pointRadius: 0,
                    borderWidth: 2,
                },
            ],
        },
        options: {
            ...CHART_DEFAULTS,
            plugins: {
                ...CHART_DEFAULTS.plugins,
                title: { display: true, text: 'Training Load (12 weeks)', font: { size: 13 } },
            },
        },
    });
}

function createTssBarChart(canvasId, activities) {
    const ctx = document.getElementById(canvasId);
    if (!ctx) return null;

    const byDate = {};
    for (const act of activities) {
        if (!byDate[act.date]) byDate[act.date] = { swim: 0, bike: 0, run: 0 };
        const sport = act.sport === 'swimming' ? 'swim' : act.sport === 'cycling' ? 'bike' : act.sport === 'running' ? 'run' : null;
        if (sport && act.tss) byDate[act.date][sport] += act.tss;
    }

    const dates = Object.keys(byDate).sort();
    const labels = dates.map(d => { const p = d.split('-'); return `${p[1]}/${p[2]}`; });

    return new Chart(ctx, {
        type: 'bar',
        data: {
            labels,
            datasets: [
                { label: 'Swim', data: dates.map(d => byDate[d].swim), backgroundColor: CHART_COLORS.swim + 'cc', borderRadius: 2 },
                { label: 'Bike', data: dates.map(d => byDate[d].bike), backgroundColor: CHART_COLORS.bike + 'cc', borderRadius: 2 },
                { label: 'Run', data: dates.map(d => byDate[d].run), backgroundColor: CHART_COLORS.run + 'cc', borderRadius: 2 },
            ],
        },
        options: {
            ...CHART_DEFAULTS,
            plugins: {
                ...CHART_DEFAULTS.plugins,
                title: { display: true, text: 'Daily TSS by Sport', font: { size: 13 } },
            },
            scales: {
                ...CHART_DEFAULTS.scales,
                x: { ...CHART_DEFAULTS.scales.x, stacked: true },
                y: { ...CHART_DEFAULTS.scales.y, stacked: true },
            },
        },
    });
}

function createGoalTrendChart(canvasId, data) {
    const ctx = document.getElementById(canvasId);
    if (!ctx) return null;

    const labels = data.dates.map(d => { const p = d.split('-'); return `${p[1]}/${p[2]}`; });

    return new Chart(ctx, {
        type: 'line',
        data: {
            labels,
            datasets: [
                { label: 'Swim CTL', data: data.ctl_swim || [], borderColor: CHART_COLORS.swim, tension: 0.3, pointRadius: 0, borderWidth: 2 },
                { label: 'Bike CTL', data: data.ctl_bike || [], borderColor: CHART_COLORS.bike, tension: 0.3, pointRadius: 0, borderWidth: 2 },
                { label: 'Run CTL', data: data.ctl_run || [], borderColor: CHART_COLORS.run, tension: 0.3, pointRadius: 0, borderWidth: 2 },
            ],
        },
        options: {
            ...CHART_DEFAULTS,
            plugins: {
                ...CHART_DEFAULTS.plugins,
                title: { display: true, text: 'CTL by Sport', font: { size: 13 } },
            },
        },
    });
}

function drawReadinessGauge(canvasId, score, level) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;

    const ctx = canvas.getContext('2d');
    const size = canvas.width;
    const center = size / 2;
    const radius = size / 2 - 15;
    const startAngle = 0.75 * Math.PI;
    const endAngle = 2.25 * Math.PI;

    ctx.clearRect(0, 0, size, size);

    // Background arc
    ctx.beginPath();
    ctx.arc(center, center, radius, startAngle, endAngle);
    ctx.strokeStyle = CHART_COLORS.grid;
    ctx.lineWidth = 12;
    ctx.lineCap = 'round';
    ctx.stroke();

    // Score arc
    const pct = Math.max(0, Math.min(1, score / 100));
    const scoreEnd = startAngle + (endAngle - startAngle) * pct;
    const colors = { green: '#22c55e', yellow: '#f59e0b', red: '#ef4444' };

    ctx.beginPath();
    ctx.arc(center, center, radius, startAngle, scoreEnd);
    ctx.strokeStyle = colors[level] || colors.yellow;
    ctx.lineWidth = 12;
    ctx.lineCap = 'round';
    ctx.stroke();
}
