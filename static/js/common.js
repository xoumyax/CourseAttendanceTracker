// Shared helpers. Course settings come from GET /api/config (the single source of truth).

let publicConfig = null;
const configPromise = fetch('/api/config')
    .then(r => r.json())
    .then(c => { publicConfig = c; return c; })
    .catch(() => null);

function loadConfig() {
    return configPromise;
}

function escapeHtml(value) {
    return String(value).replace(/[&<>"']/g, ch => (
        { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]
    ));
}

// Session dates are plain calendar dates (YYYY-MM-DD); show them as they are.
function fmtDate(value) {
    return escapeHtml(value);
}

// Timestamps are UTC with an offset; show them in the course timezone.
function fmtDateTime(value) {
    const options = publicConfig ? { timeZone: publicConfig.timezone } : {};
    return new Date(value).toLocaleString([], options);
}

// FastAPI returns `detail` as a string, or as a list of validation errors.
function errorText(data, fallback) {
    if (!data || !data.detail) return fallback;
    if (Array.isArray(data.detail)) return data.detail.map(d => d.msg).join('; ');
    return data.detail;
}

function describeGradeScale(scale) {
    return scale.map((tier, i) => {
        const range = (tier.min_percent === 0 && i > 0)
            ? `below ${scale[i - 1].min_percent}%`
            : `${tier.min_percent}% or more`;
        return `${range}: ${tier.points} point${tier.points === 1 ? '' : 's'}`;
    });
}

// Fill [data-cfg="key"] elements, the grade-scale lists, and "{course}" in the title.
async function applyConfig() {
    const cfg = await loadConfig();
    if (!cfg) return;
    document.title = document.title.replace('{course}', cfg.course_name);
    document.querySelectorAll('[data-cfg]').forEach(el => {
        const value = cfg[el.dataset.cfg];
        if (value !== undefined) el.textContent = value;
    });
    document.querySelectorAll('[data-cfg-grade-scale]').forEach(list => {
        list.innerHTML = describeGradeScale(cfg.grade_scale)
            .map(line => `<li>${escapeHtml(line)}</li>`).join('');
    });
}

document.addEventListener('DOMContentLoaded', applyConfig);
