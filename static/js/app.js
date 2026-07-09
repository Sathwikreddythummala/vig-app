function showToast(message, type = 'success') {
    const container = document.getElementById('toastContainer');
    const id = 'toast-' + Date.now();
    const icons = { success: 'bi-check-circle-fill', danger: 'bi-x-circle-fill', warning: 'bi-exclamation-triangle-fill', info: 'bi-info-circle-fill' };
    const colors = { success: '#43A047', danger: '#E53935', warning: '#FFA000', info: '#1E88E5' };
    const toast = document.createElement('div');
    toast.id = id;
    toast.className = 'toast show';
    toast.setAttribute('role', 'alert');
    toast.innerHTML = `
        <div class="toast-body d-flex align-items-center gap-2" style="color:${colors[type]||colors.info}">
            <i class="bi ${icons[type]||icons.info} fs-5"></i>
            <span style="color:var(--text-dark);flex:1">${message}</span>
            <button type="button" class="btn-close btn-close-sm" onclick="this.closest('.toast').remove()"></button>
        </div>`;
    container.appendChild(toast);
    setTimeout(() => {
        toast.style.transition = 'opacity 0.3s';
        toast.style.opacity = '0';
        setTimeout(() => toast.remove(), 300);
    }, 4000);
}

// Upload a file chosen in a form's file input against an entity via the documents API.
// Replaces any previous document of the same type for that entity.
// Returns true if no file was selected or the upload succeeded.
async function uploadEntityDoc(inputId, entityType, entityId, docType) {
    const input = document.getElementById(inputId);
    if (!input || !input.files || !input.files.length) return true;
    if (!entityId) { input.value = ''; return true; }
    docType = docType || 'Invoice';
    const fd = new FormData();
    fd.append('file', input.files[0]);
    fd.append('entity_type', entityType);
    fd.append('entity_id', entityId);
    fd.append('doc_type', docType);
    try {
        let oldIds = [];
        try {
            const existing = await (await fetch('/documents/api/list?entity_type=' + encodeURIComponent(entityType) + '&entity_id=' + encodeURIComponent(entityId))).json();
            oldIds = (existing || []).filter(d => (d.doc_type || '') === docType).map(d => d.doc_id);
        } catch (e) { /* replacement is best-effort; still upload the new file */ }
        const res = await fetch('/documents/api/upload', { method: 'POST', body: fd });
        const data = await res.json();
        input.value = '';
        if (!data.success) { showToast(data.error || 'Document upload failed', 'danger'); return false; }
        for (const oid of oldIds) {
            try { await fetch('/documents/api/' + oid, { method: 'DELETE' }); } catch (e) {}
        }
        return true;
    } catch (e) {
        input.value = '';
        showToast('Document upload failed', 'danger');
        return false;
    }
}

// ---- Multi-select filter dropdowns ----
// Converts a filter <select> into a checkbox dropdown so several values can be
// picked at once. The original select stays hidden as the source of options and
// its .value returns the comma-joined selection, so existing code that reads
// .value (query params, filters) keeps working unchanged.

function getFilterValues(selectId) {
    var sel = document.getElementById(selectId);
    if (!sel) return [];
    if (sel._msSel) return Array.from(sel._msSel);
    return sel.value ? [String(sel.value)] : [];
}

function makeMultiSelect(sel) {
    if (!sel || sel._msSel || sel.multiple) return;
    var selected = new Set();
    sel._msSel = selected;
    var allLabel = (sel.options[0] && !sel.options[0].value) ? sel.options[0].text : 'All';

    var wrap = document.createElement('div');
    wrap.className = 'ms-filter';
    wrap.style.width = sel.style.width || '160px';
    var btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'form-select glass-input ms-filter-btn';
    btn.textContent = allLabel;
    var menu = document.createElement('div');
    menu.className = 'ms-filter-menu';
    sel.parentNode.insertBefore(wrap, sel);
    wrap.appendChild(btn);
    wrap.appendChild(menu);
    wrap.appendChild(sel);
    sel.style.display = 'none';

    function labelText() {
        if (!selected.size) return allLabel;
        if (selected.size === 1) return Array.from(selected)[0];
        return selected.size + ' selected';
    }
    function fire() {
        btn.textContent = labelText();
        sel.dispatchEvent(new Event('change'));
    }
    Object.defineProperty(sel, 'value', {
        configurable: true,
        get: function () { return Array.from(selected).join(','); },
        set: function (v) {
            selected.clear();
            String(v || '').split(',').forEach(function (x) { x = x.trim(); if (x) selected.add(x); });
            btn.textContent = labelText();
        },
    });
    function buildMenu() {
        menu.innerHTML = '';
        var clear = document.createElement('div');
        clear.className = 'ms-filter-item ms-filter-clear';
        clear.textContent = allLabel;
        clear.onclick = function (e) { e.stopPropagation(); selected.clear(); buildMenu(); fire(); };
        menu.appendChild(clear);
        Array.from(sel.options).forEach(function (opt) {
            var val = opt.value; // empty-value options are "All …" placeholders, handled by the clear row
            if (!val.trim()) return;
            var item = document.createElement('label');
            item.className = 'ms-filter-item';
            var cb = document.createElement('input');
            cb.type = 'checkbox';
            cb.checked = selected.has(val);
            cb.onchange = function () {
                if (cb.checked) selected.add(val); else selected.delete(val);
                fire();
            };
            item.appendChild(cb);
            item.appendChild(document.createTextNode(' ' + opt.text));
            menu.appendChild(item);
        });
    }
    btn.onclick = function (e) {
        e.stopPropagation();
        var willOpen = !menu.classList.contains('open');
        msCloseAllFilters();
        if (willOpen) {
            buildMenu();
            menu.classList.add('open');
            // glass cards create stacking contexts (backdrop-filter), so lift this
            // card above its siblings or the menu paints underneath the next card
            var card = wrap.closest('.glass-card');
            if (card) card.classList.add('ms-elevated');
        }
    };
}

function msCloseAllFilters() {
    document.querySelectorAll('.ms-filter-menu.open').forEach(function (m) { m.classList.remove('open'); });
    document.querySelectorAll('.ms-elevated').forEach(function (c) { c.classList.remove('ms-elevated'); });
}

document.addEventListener('click', function (e) {
    if (!e.target.closest('.ms-filter')) msCloseAllFilters();
});

document.addEventListener('DOMContentLoaded', function () {
    var css = document.createElement('style');
    css.textContent =
        '.ms-filter{position:relative;display:inline-block;vertical-align:middle}' +
        '.ms-filter-btn{text-align:left;cursor:pointer;width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}' +
        '.ms-filter-menu{display:none;position:absolute;top:calc(100% + 4px);left:0;z-index:1060;min-width:100%;max-height:280px;overflow:auto;background:#FFFDE7;border:1px solid rgba(255,213,79,.6);border-radius:10px;padding:6px;box-shadow:0 8px 22px rgba(0,0,0,.18)}' +
        '.ms-filter-menu.open{display:block}' +
        '.ms-filter-item{display:flex;align-items:center;gap:7px;padding:5px 9px;border-radius:7px;cursor:pointer;white-space:nowrap;margin:0;font-size:.85rem;color:var(--text-dark,#3E2723)}' +
        '.ms-filter-item:hover{background:rgba(255,213,79,.3)}' +
        '.ms-filter-clear{font-weight:600;border-bottom:1px solid rgba(255,213,79,.4);border-radius:7px 7px 0 0}' +
        '.ms-elevated{position:relative;z-index:500}';
    document.head.appendChild(css);
    document.querySelectorAll('select[id^="filter"]').forEach(makeMultiSelect);
});

let alertsVisible = false;

function toggleAlerts() {
    const dropdown = document.getElementById('alertsDropdown');
    alertsVisible = !alertsVisible;
    dropdown.style.display = alertsVisible ? 'block' : 'none';
}

document.addEventListener('click', (e) => {
    if (alertsVisible && !e.target.closest('#alertBell') && !e.target.closest('#alertsDropdown')) {
        document.getElementById('alertsDropdown').style.display = 'none';
        alertsVisible = false;
    }
});

function isViewer() { return typeof USER_ROLE !== 'undefined' && USER_ROLE === 'viewer'; }

function hideViewerActions() {
    if (!isViewer()) return;
    document.querySelectorAll('.btn-outline-warning, .btn-outline-danger').forEach(function(btn) {
        var text = btn.textContent.trim().toLowerCase();
        var icon = btn.querySelector('i');
        if (icon && (icon.classList.contains('bi-pencil') || icon.classList.contains('bi-trash') || icon.classList.contains('bi-plus-lg'))) {
            btn.style.display = 'none';
        }
    });
}

var _origObserver = new MutationObserver(function() { if (isViewer()) hideViewerActions(); });
document.addEventListener('DOMContentLoaded', function() {
    if (isViewer()) {
        hideViewerActions();
        _origObserver.observe(document.body, { childList: true, subtree: true });
    }
});

async function loadAlerts() {
    try {
        const res = await fetch('/api/dashboard/alerts');
        const data = await res.json();
        const alerts = data.alerts || [];
        const countEl = document.getElementById('alertCount');
        const listEl = document.getElementById('alertsList');
        if (alerts.length > 0) {
            countEl.textContent = alerts.length;
            countEl.style.display = '';
            listEl.innerHTML = alerts.map(a => `
                <div class="alert-item">
                    <span class="badge bg-${a.type}">${a.type === 'danger' ? 'Expired' : a.type === 'warning' ? 'Expiring' : 'Info'}</span>
                    <span>${a.message}</span>
                </div>`).join('');
        } else {
            countEl.style.display = 'none';
            listEl.innerHTML = '<div class="p-3 text-center text-muted">No alerts</div>';
        }
    } catch (err) {
        console.error('Failed to load alerts:', err);
    }
}
