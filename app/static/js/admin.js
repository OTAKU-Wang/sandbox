/**
 * CDS Admin — shared API client and utilities.
 *
 * A114: admin pages render server data through innerHTML. `esc()` is the
 * single HTML-escaping utility for every interpolation — templates must not
 * hand-roll replaces. `escAttr()` additionally makes a value safe to embed
 * inside an inline `onclick="fn('...')"` JS string literal.
 */
const API = {
    async get(url) {
        const res = await fetch(url, { headers: this._headers() });
        if (!res.ok) throw new Error(`${res.status}: ${(await res.text()).slice(0, 200)}`);
        return res.json();
    },
    async post(url, body) {
        const res = await fetch(url, {
            method: 'POST', headers: this._headers(), body: JSON.stringify(body),
        });
        if (!res.ok) throw new Error(`${res.status}: ${(await res.text()).slice(0, 200)}`);
        return res.json();
    },
    async del(url) {
        const res = await fetch(url, { method: 'DELETE', headers: this._headers() });
        if (!res.ok) throw new Error(`${res.status}: ${(await res.text()).slice(0, 200)}`);
        return res.json();
    },
    _headers() {
        const h = { 'Content-Type': 'application/json' };
        const token = localStorage.getItem('cds_token');
        if (token) h['Authorization'] = `Bearer ${token}`;
        return h;
    },
};

function $(sel) { return document.querySelector(sel); }
function $$(sel) { return document.querySelectorAll(sel); }

// A114: the one and only HTML escaper — every innerHTML interpolation of
// server data goes through it (or through badge()/statusBadge() which call it).
function esc(value) {
    if (value === null || value === undefined) return '';
    return String(value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

// For embedding a value in `onclick="fn('...')"`: JSON-escape as a JS string
// literal body, then HTML-escape for the double-quoted attribute context.
function escAttr(value) {
    const body = JSON.stringify(String(value === null || value === undefined ? '' : value)).slice(1, -1);
    return esc(body.replace(/'/g, "\\'"));
}

function badge(text, type) {
    return `<span class="badge badge-${esc(type)}">${esc(text)}</span>`;
}

function statusBadge(status) {
    const map = {
        active: 'success', valid: 'success', completed: 'success', verified: 'success',
        pending: 'warning', suspended: 'warning', expired: 'warning',
        revoked: 'danger', failed: 'danger', destroyed: 'danger',
    };
    return badge(status, map[status] || 'info');
}

function formatDate(iso) {
    if (!iso) return '-';
    return new Date(iso).toLocaleString('zh-CN');
}

// Pure text helper — the result is still raw text; HTML sinks must wrap it
// in esc() before interpolation.
function truncate(str, len) {
    if (!str) return '-';
    return str.length > len ? str.slice(0, len) + '...' : str;
}

async function loadPage(apiUrl, renderFn) {
    try {
        const data = await API.get(apiUrl);
        renderFn(data);
    } catch (e) {
        $('#app').innerHTML = `<div class="empty">加载失败: ${esc(e.message)}</div>`;
    }
}
