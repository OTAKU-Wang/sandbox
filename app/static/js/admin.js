/**
 * CDS Admin — shared API client and utilities.
 */
const API = {
    async get(url) {
        const res = await fetch(url, { headers: this._headers() });
        if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`);
        return res.json();
    },
    async post(url, body) {
        const res = await fetch(url, {
            method: 'POST', headers: this._headers(), body: JSON.stringify(body),
        });
        if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`);
        return res.json();
    },
    async del(url) {
        const res = await fetch(url, { method: 'DELETE', headers: this._headers() });
        if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`);
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

function badge(text, type) {
    return `<span class="badge badge-${type}">${text}</span>`;
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

function truncate(str, len) {
    if (!str) return '-';
    return str.length > len ? str.slice(0, len) + '...' : str;
}

async function loadPage(apiUrl, renderFn) {
    try {
        const data = await API.get(apiUrl);
        renderFn(data);
    } catch (e) {
        $('#app').innerHTML = `<div class="empty">加载失败: ${e.message}</div>`;
    }
}
