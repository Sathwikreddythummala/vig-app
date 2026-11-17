// Local (browser-timezone) date helpers. Never use toISOString() for date defaults —
// that returns UTC, which shows YESTERDAY between midnight and ~5:30am IST.
function todayLocal() {
    var d = new Date();
    return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
}
function thisMonthLocal() {
    var d = new Date();
    return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0');
}

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
        var realOpts = Array.from(sel.options).filter(function (o) { return o.value.trim(); });
        // Type-to-filter box for long lists (vehicles, vendors, drivers…)
        var search = null;
        if (realOpts.length > 7) {
            var sWrap = document.createElement('div');
            sWrap.className = 'ms-filter-search';
            search = document.createElement('input');
            search.type = 'text';
            search.placeholder = 'Search…';
            search.className = 'form-control form-control-sm';
            search.onclick = function (e) { e.stopPropagation(); };
            search.oninput = function () { applySearch(); };
            sWrap.appendChild(search);
            menu.appendChild(sWrap);
        }
        var clear = document.createElement('div');
        clear.className = 'ms-filter-item ms-filter-clear';
        clear.textContent = allLabel;
        clear.onclick = function (e) { e.stopPropagation(); selected.clear(); buildMenu(); fire(); };
        menu.appendChild(clear);
        realOpts.forEach(function (opt) {
            var item = document.createElement('label');
            item.className = 'ms-filter-item';
            item.dataset.text = opt.text.toLowerCase();
            var cb = document.createElement('input');
            cb.type = 'checkbox';
            cb.checked = selected.has(opt.value);
            cb.onchange = function () {
                if (cb.checked) selected.add(opt.value); else selected.delete(opt.value);
                fire();
            };
            item.appendChild(cb);
            item.appendChild(document.createTextNode(' ' + opt.text));
            menu.appendChild(item);
        });
        function applySearch() {
            var q = (search.value || '').toLowerCase().trim();
            menu.querySelectorAll('.ms-filter-item:not(.ms-filter-clear)').forEach(function (it) {
                it.style.display = (!q || it.dataset.text.indexOf(q) >= 0) ? '' : 'none';
            });
        }
        if (search) setTimeout(function () { search.focus(); }, 30);
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
        '.ms-filter-search{position:sticky;top:0;background:#FFFDE7;padding:2px 2px 6px;margin-bottom:4px;border-bottom:1px solid rgba(255,213,79,.4)}' +
        '.ms-elevated{position:relative;z-index:500}' +
        // searchable single-select combobox
        '.ss-wrap{position:relative;display:inline-block;vertical-align:middle}' +
        '.ss-input{cursor:text}' +
        '.ss-menu{display:none;position:absolute;top:calc(100% + 4px);left:0;z-index:1065;min-width:100%;max-height:260px;overflow:auto;background:#FFFDE7;border:1px solid rgba(255,213,79,.6);border-radius:10px;padding:5px;box-shadow:0 8px 22px rgba(0,0,0,.18)}' +
        '.ss-menu.open{display:block}' +
        '.ss-opt{padding:6px 9px;border-radius:7px;cursor:pointer;white-space:nowrap;font-size:.88rem;color:var(--text-dark,#3E2723)}' +
        '.ss-opt:hover,.ss-opt.active{background:rgba(255,213,79,.35)}' +
        '.ss-opt.ss-none{color:#999;cursor:default}';
    document.head.appendChild(css);
    document.querySelectorAll('select[id^="filter"]').forEach(makeMultiSelect);
});

// ---- Mobile sidebar ----
// On phones/tablets the sidebar slides in over the content with a dimmed
// backdrop; on desktop the same button collapses it to an icon rail.
function _isMobileView() { return window.matchMedia('(max-width: 991.98px)').matches; }

function toggleSidebar() {
    var sidebar = document.getElementById('sidebar');
    var backdrop = document.getElementById('sidebarBackdrop');
    if (_isMobileView()) {
        var open = sidebar.classList.toggle('mobile-open');
        if (backdrop) backdrop.classList.toggle('show', open);
        document.body.style.overflow = open ? 'hidden' : '';
    } else {
        sidebar.classList.toggle('collapsed');
    }
}

function closeSidebar() {
    var sidebar = document.getElementById('sidebar');
    var backdrop = document.getElementById('sidebarBackdrop');
    if (sidebar) sidebar.classList.remove('mobile-open');
    if (backdrop) backdrop.classList.remove('show');
    document.body.style.overflow = '';
}

// Tapping a nav link on mobile closes the drawer before navigating.
document.addEventListener('DOMContentLoaded', function () {
    if (_isMobileView()) {
        document.querySelectorAll('.sidebar .nav-link').forEach(function (a) {
            a.addEventListener('click', function () { closeSidebar(); });
        });
    }
});
// Reset drawer state when rotating/resizing back to desktop.
window.addEventListener('resize', function () { if (!_isMobileView()) closeSidebar(); });

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
        var noDrv = data.vehicles_without_driver || [];
        var drvIssues = data.driver_issues || [];
        if (noDrv.length) { setTimeout(function(){ showNoDriverPopup(noDrv); }, 600); }
        else if (drvIssues.length) { setTimeout(function(){ showDriverDatesPopup(drvIssues); }, 700); }
    } catch (err) {
        console.error('Failed to load alerts:', err);
    }
}

// Strictly no vehicle without a driver — pop up until every one is assigned.
function showNoDriverPopup(list) {
    if (document.querySelector('.modal.show')) return;  // don't stack over another dialog
    var id = 'noDriverModal';
    var el = document.getElementById(id);
    if (!el) {
        el = document.createElement('div');
        el.className = 'modal fade'; el.id = id; el.tabIndex = -1;
        el.innerHTML =
            '<div class="modal-dialog"><div class="modal-content">' +
            '<div class="modal-header" style="background:#dc3545;color:#fff"><h5 class="modal-title"><i class="bi bi-exclamation-triangle-fill"></i> Vehicle without a driver</h5><button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal"></button></div>' +
            '<div class="modal-body"><p class="mb-2 fw-bold text-danger">Every vehicle must have a driver. These have none — assign one:</p><div id="noDriverList"></div></div>' +
            '<div class="modal-footer"><button class="btn btn-secondary" data-bs-dismiss="modal">Later</button></div>' +
            '</div></div>';
        document.body.appendChild(el);
    }
    document.getElementById('noDriverList').innerHTML = list.map(function (v) {
        return '<div class="d-flex justify-content-between align-items-center border-bottom py-2"><b>' + v.vehicle + '</b>' +
            '<a href="/vehicles/details/' + v.id + '" class="btn btn-sm btn-warning viewer-hide"><i class="bi bi-person-gear"></i> Assign driver</a></div>';
    }).join('');
    try { bootstrap.Modal.getOrCreateInstance(el).show(); } catch (e) {}
}

// Strictly every driver needs an entry date; inactive drivers also need an exit date.
function showDriverDatesPopup(list) {
    if (document.querySelector('.modal.show')) return;
    var id = 'driverDatesModal';
    var el = document.getElementById(id);
    if (!el) {
        el = document.createElement('div');
        el.className = 'modal fade'; el.id = id; el.tabIndex = -1;
        el.innerHTML =
            '<div class="modal-dialog"><div class="modal-content">' +
            '<div class="modal-header" style="background:#dc3545;color:#fff"><h5 class="modal-title"><i class="bi bi-person-exclamation"></i> Driver issues to fix</h5><button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal"></button></div>' +
            '<div class="modal-body"><p class="mb-2 fw-bold text-danger">Every driver needs an entry (joining) date. A driver who left needs an exit date, and an active driver with no vehicle should be given one or marked inactive:</p><div id="driverDatesList" style="max-height:50vh;overflow:auto"></div></div>' +
            '<div class="modal-footer"><a href="/drivers" class="btn btn-warning viewer-hide"><i class="bi bi-people"></i> Open Employees</a><button class="btn btn-secondary" data-bs-dismiss="modal">Later</button></div>' +
            '</div></div>';
        document.body.appendChild(el);
    }
    document.getElementById('driverDatesList').innerHTML = list.map(function (d) {
        return '<div class="d-flex justify-content-between align-items-center border-bottom py-2 gap-2"><b>' + d.name + '</b>' +
            '<span class="badge bg-danger text-wrap" style="text-align:right">' + d.issue + '</span></div>';
    }).join('');
    try { bootstrap.Modal.getOrCreateInstance(el).show(); } catch (e) {}
}

// ---- Optional click-to-sort on tables ----
// The server's order (latest entry first) stays the DEFAULT. Clicking a column
// header sorts the currently shown rows: 1st click ascending, 2nd descending,
// 3rd restores the default order. Grouped tables (data-no-sort) are excluded,
// and any table with grouped/subtotal rows (colspan) is skipped safely.
function _sortCellValue(cell) {
    var t = (cell ? cell.textContent : '').trim();
    if (t === '' || t === '-' || t === '—') return { n: null, s: '' };
    var num = t.replace(/[₹,\s]/g, '');
    if (/^-?\d+(\.\d+)?$/.test(num)) return { n: parseFloat(num), s: t.toLowerCase() };
    if (/^\d{4}-\d{2}-\d{2}/.test(t)) { var d = Date.parse(t.slice(0, 10)); if (!isNaN(d)) return { n: d, s: t.toLowerCase() }; }
    return { n: null, s: t.toLowerCase() };
}

function sortTableByColumn(table, colIdx, th) {
    var tbody = table.tBodies[0];
    if (!tbody) return;
    var rows = Array.prototype.slice.call(tbody.rows);
    if (!rows.length) return;
    var isSpecial = function (r) { return Array.prototype.some.call(r.cells, function (c) { return c.colSpan > 1; }); };
    var firstSpecial = rows.findIndex(isSpecial);
    var dataRows = rows.filter(function (r) { return !isSpecial(r); });
    var trailing = rows.filter(isSpecial);
    // safe only when special rows (e.g. a totals footer) are all at the very bottom;
    // interleaved group/subtotal rows mean this is a grouped table — leave it alone
    if (firstSpecial !== -1 && firstSpecial !== dataRows.length) {
        if (typeof showToast === 'function') showToast('This grouped table isn’t column-sortable', 'info');
        return;
    }
    if (!tbody._origOrder) tbody._origOrder = rows.slice();
    var head = th.parentNode;
    var dir = th.dataset.sortDir === 'asc' ? 'desc' : th.dataset.sortDir === 'desc' ? '' : 'asc';
    Array.prototype.forEach.call(head.cells, function (c) {
        c.dataset.sortDir = '';
        var ar = c.querySelector('.sort-arrow');
        if (ar) { ar.textContent = ' ⇅'; ar.style.opacity = '0.35'; }
    });
    th.dataset.sortDir = dir;
    var arrow = th.querySelector('.sort-arrow');
    if (dir === '') {
        tbody._origOrder.forEach(function (r) { tbody.appendChild(r); });
        return;
    }
    if (arrow) { arrow.textContent = dir === 'asc' ? ' ↑' : ' ↓'; arrow.style.opacity = '1'; }
    dataRows.slice().sort(function (a, b) {
        var va = _sortCellValue(a.cells[colIdx]), vb = _sortCellValue(b.cells[colIdx]);
        var r;
        if (va.n !== null && vb.n !== null) r = va.n - vb.n;
        else r = va.s < vb.s ? -1 : va.s > vb.s ? 1 : 0;
        return dir === 'asc' ? r : -r;
    }).forEach(function (r) { tbody.appendChild(r); });
    trailing.forEach(function (r) { tbody.appendChild(r); });  // keep totals row pinned at the bottom
}

function makeTablesSortable(root) {
    (root || document).querySelectorAll('table.glass-table:not([data-no-sort])').forEach(function (table) {
        var thead = table.tHead;
        if (!thead || !thead.rows.length) return;
        var headerRow = thead.rows[thead.rows.length - 1];
        Array.prototype.forEach.call(headerRow.cells, function (th, idx) {
            if (th.dataset.sortBound) return;
            var label = th.textContent.trim().toLowerCase();
            if (!label || label === 'actions' || label === 'action' || 'noSort' in th.dataset) return;
            th.dataset.sortBound = '1';
            th.style.cursor = 'pointer';
            th.style.userSelect = 'none';
            if (!th.title) th.title = 'Click to sort';
            var arrow = document.createElement('span');
            arrow.className = 'sort-arrow';
            arrow.style.opacity = '0.35';
            arrow.style.fontSize = '0.85em';
            arrow.textContent = ' ⇅';
            th.appendChild(arrow);
            th.addEventListener('click', function () { sortTableByColumn(table, idx, th); });
        });
    });
}

document.addEventListener('DOMContentLoaded', function () { makeTablesSortable(); });

// ===================================================================
// UX enhancements — fewer clicks / less searching / less typing.
// All defensive (try/catch) so a failure here can never break a page.
// ===================================================================
(function () {
    'use strict';

    // ---------- Searchable single-select (form dropdowns) ----------
    // Enhances a <select> into a type-to-pick combobox while keeping the
    // native select in the DOM as the value source (graceful fallback).
    function makeSearchableSelect(sel) {
        if (!sel || sel.multiple || sel._ss || sel.dataset.noSearch === '1') return;
        if (sel.options.length < 6) return;            // short lists don't need it
        var wrap = document.createElement('div');
        wrap.className = 'ss-wrap';
        wrap.style.width = sel.style.width || '100%';
        var input = document.createElement('input');
        input.type = 'text';
        input.className = (sel.className || 'form-control') + ' ss-input';
        input.setAttribute('autocomplete', 'off');
        input.placeholder = (sel.options[0] && !sel.options[0].value) ? sel.options[0].text : 'Search…';
        var menu = document.createElement('div');
        menu.className = 'ss-menu';
        sel.parentNode.insertBefore(wrap, sel);
        wrap.appendChild(input); wrap.appendChild(menu); wrap.appendChild(sel);
        sel.style.display = 'none';
        sel._ss = { input: input };

        function curText() {
            var o = sel.options[sel.selectedIndex];
            return (o && o.value) ? o.text : '';
        }
        function syncInput() { input.value = curText(); }
        sel._ss.refresh = syncInput;
        var active = -1, items = [];
        function render() {
            var q = input.value.toLowerCase().trim();
            menu.innerHTML = ''; items = []; active = -1;
            Array.from(sel.options).forEach(function (o) {
                if (!o.value) return;
                if (q && o.text.toLowerCase().indexOf(q) < 0) return;
                var d = document.createElement('div');
                d.className = 'ss-opt'; d.textContent = o.text; d.dataset.val = o.value;
                d.onmousedown = function (e) { e.preventDefault(); choose(o.value); };
                menu.appendChild(d); items.push(d);
            });
            if (!items.length) {
                var n = document.createElement('div'); n.className = 'ss-opt ss-none'; n.textContent = 'No match';
                menu.appendChild(n);
            }
        }
        function open() { render(); menu.classList.add('open'); var c = wrap.closest('.glass-card'); if (c) c.classList.add('ms-elevated'); }
        function close() { menu.classList.remove('open'); syncInput(); }
        function choose(val) {
            sel.value = val;
            sel.dispatchEvent(new Event('change', { bubbles: true }));
            syncInput(); menu.classList.remove('open');
        }
        function setActive(i) {
            if (!items.length) return;
            active = (i + items.length) % items.length;
            items.forEach(function (it, idx) { it.classList.toggle('active', idx === active); });
            items[active].scrollIntoView({ block: 'nearest' });
        }
        input.addEventListener('focus', open);
        input.addEventListener('click', open);
        input.addEventListener('input', function () { menu.classList.add('open'); render(); });
        input.addEventListener('keydown', function (e) {
            if (e.key === 'ArrowDown') { e.preventDefault(); if (!menu.classList.contains('open')) open(); setActive(active + 1); }
            else if (e.key === 'ArrowUp') { e.preventDefault(); setActive(active - 1); }
            else if (e.key === 'Enter') { if (menu.classList.contains('open') && active >= 0) { e.preventDefault(); choose(items[active].dataset.val); } }
            else if (e.key === 'Escape') { close(); }
        });
        input.addEventListener('blur', function () { setTimeout(function () { if (!menu.contains(document.activeElement)) close(); }, 120); });
        // reflect programmatic value changes (edit flows dispatch change)
        sel.addEventListener('change', syncInput);
        syncInput();
    }
    window.makeSearchableSelect = makeSearchableSelect;

    function enhanceSelects(root) {
        try {
            (root || document).querySelectorAll('select.searchable, select[data-search="1"]').forEach(makeSearchableSelect);
        } catch (e) {}
    }
    // Fields that should remember the last value you used (per browser).
    var REMEMBER_FIELDS = ['expPaymentMode', 'expPaidBy', 'fPayMode', 'fFuelType', 'rPayMode'];
    function wireRememberFields() {
        REMEMBER_FIELDS.forEach(function (id) {
            var el = document.getElementById(id); if (!el) return;
            el.addEventListener('change', function () { UX.setLast('field:' + id, el.value); });
        });
    }
    function applyRemembered(modal) {
        REMEMBER_FIELDS.forEach(function (id) {
            var el = modal.querySelector('#' + id); if (!el) return;
            var v = UX.getLast('field:' + id, null);
            if (v) { el.value = v; if (el._ss) el._ss.refresh(); }
        });
    }
    // Re-sync comboboxes when a modal opens (edit flows set .value directly);
    // on an ADD modal, also pre-fill the remembered Payment Mode / Paid-By etc.
    document.addEventListener('shown.bs.modal', function (e) {
        try {
            enhanceSelects(e.target);
            e.target.querySelectorAll('select').forEach(function (s) { if (s._ss) s._ss.refresh(); });
            var title = e.target.querySelector('.modal-title');
            if (title && /^\s*add/i.test(title.textContent)) applyRemembered(e.target);
        } catch (err) {}
    });

    // ---------- Remember last-used form values (localStorage) ----------
    window.UX = window.UX || {};
    UX.getLast = function (key, dflt) { try { var v = localStorage.getItem('uxlast:' + key); return v === null ? (dflt || '') : v; } catch (e) { return dflt || ''; } };
    UX.setLast = function (key, val) { try { localStorage.setItem('uxlast:' + key, val == null ? '' : String(val)); } catch (e) {} };
    // Bind an input/select: restore its remembered value now, save on change.
    UX.remember = function (id, key) {
        try {
            var el = document.getElementById(id); if (!el) return;
            key = key || (location.pathname + ':' + id);
            var saved = UX.getLast(key, null);
            if (saved !== null && saved !== '' && !el.value) el.value = saved;
            el.addEventListener('change', function () { UX.setLast(key, el.value); });
        } catch (e) {}
    };

    // ---------- Remember page filters (localStorage) ----------
    function filterKey() { return 'uxfilters:' + location.pathname; }
    function filterEls() {
        return Array.from(document.querySelectorAll('[id^="filter"]'))
            .filter(function (el) { return el.id !== 'filterSearch'; });  // don't persist live text search
    }
    function saveFilters() {
        try {
            var data = {};
            filterEls().forEach(function (el) { data[el.id] = el.value; });
            localStorage.setItem(filterKey(), JSON.stringify(data));
        } catch (e) {}
    }
    function restoreFilters() {
        try {
            var raw = localStorage.getItem(filterKey()); if (!raw) return false;
            var data = JSON.parse(raw); var any = false, monthEl = null;
            filterEls().forEach(function (el) {
                if (data[el.id] !== undefined && data[el.id] !== '') {
                    el.value = data[el.id]; any = true;
                    if (/month/i.test(el.id)) monthEl = monthEl || el;
                }
            });
            // trigger one reload via a month filter's onchange (present on all list pages)
            if (any) { var t = monthEl || document.getElementById('filterMonth'); if (t) t.dispatchEvent(new Event('change', { bubbles: true })); }
            return any;
        } catch (e) { return false; }
    }
    UX.clearFilters = function () { try { localStorage.removeItem(filterKey()); } catch (e) {} };

    // ---------- Global search (top bar) ----------
    function initGlobalSearch() {
        var inp = document.getElementById('globalSearch');
        var box = document.getElementById('globalSearchResults');
        if (!inp || !box) return;
        var timer = null, rows = [], active = -1;
        function hide() { box.style.display = 'none'; active = -1; }
        function go(url) { if (url) window.location.href = url; }
        function render() {
            if (!rows.length) { box.innerHTML = '<div class="text-muted small p-2">No matches</div>'; box.style.display = 'block'; return; }
            var groups = {};
            rows.forEach(function (r) { (groups[r.type] = groups[r.type] || []).push(r); });
            var html = '', i = 0;
            Object.keys(groups).forEach(function (g) {
                html += '<div class="text-muted small fw-bold px-2 pt-1" style="text-transform:uppercase;font-size:.68rem;letter-spacing:.04em">' + g + '</div>';
                groups[g].forEach(function (r) {
                    html += '<a href="' + r.url + '" class="gs-row d-block text-decoration-none px-2 py-1" data-i="' + i + '" style="border-radius:7px;color:var(--text-dark,#3E2723)">' +
                        '<i class="bi ' + (r.icon || 'bi-dot') + ' me-2" style="color:#B8860B"></i>' +
                        '<span>' + r.label + '</span>' + (r.sub ? ' <span class="text-muted small">— ' + r.sub + '</span>' : '') + '</a>';
                    i++;
                });
            });
            box.innerHTML = html; box.style.display = 'block';
            box.querySelectorAll('.gs-row').forEach(function (a) {
                a.addEventListener('mouseenter', function () { setActive(parseInt(a.dataset.i)); });
            });
        }
        var flat = function () { return Array.from(box.querySelectorAll('.gs-row')); };
        function setActive(i) {
            var f = flat(); if (!f.length) return;
            active = (i + f.length) % f.length;
            f.forEach(function (a, idx) { a.style.background = idx === active ? 'rgba(255,213,79,.35)' : ''; });
        }
        function search() {
            var q = inp.value.trim();
            if (q.length < 2) { hide(); return; }
            fetch('/api/search?q=' + encodeURIComponent(q)).then(function (r) { return r.json(); }).then(function (d) {
                rows = (d && d.results) || []; render();
            }).catch(hide);
        }
        inp.addEventListener('input', function () { clearTimeout(timer); timer = setTimeout(search, 180); });
        inp.addEventListener('focus', function () { if (inp.value.trim().length >= 2) search(); });
        inp.addEventListener('keydown', function (e) {
            if (e.key === 'ArrowDown') { e.preventDefault(); setActive(active + 1); }
            else if (e.key === 'ArrowUp') { e.preventDefault(); setActive(active - 1); }
            else if (e.key === 'Enter') { var f = flat(); if (f.length) { e.preventDefault(); go(f[active < 0 ? 0 : active].getAttribute('href')); } }
            else if (e.key === 'Escape') { hide(); inp.blur(); }
        });
        document.addEventListener('click', function (e) { if (!e.target.closest('.global-search')) hide(); });
    }

    // ---------- Keyboard shortcuts ----------
    function onKey(e) {
        if (e.ctrlKey || e.metaKey || e.altKey) return;
        var t = e.target, tag = (t.tagName || '').toLowerCase();
        var typing = tag === 'input' || tag === 'textarea' || tag === 'select' || t.isContentEditable;
        if (e.key === '/' && !typing) {
            var gs = document.getElementById('globalSearch');
            if (gs) { e.preventDefault(); gs.focus(); }
        } else if ((e.key === 'n' || e.key === 'N') && !typing) {
            // click the page's primary "add" button
            var add = document.querySelector('[data-bs-target="#expenseModal"],[data-bs-target="#billModal"],[data-bs-target="#recvModal"],[data-quick-add]')
                || Array.from(document.querySelectorAll('button')).find(function (b) { return /add (expense|fuel|bill|vehicle|driver|payment)/i.test(b.textContent); });
            if (add) { e.preventDefault(); add.click(); }
        }
    }
    document.addEventListener('keydown', onKey);

    // ---------- Init ----------
    window.addEventListener('load', function () {
        try { enhanceSelects(); } catch (e) {}
        try { initGlobalSearch(); } catch (e) {}
        try { wireRememberFields(); } catch (e) {}
        // save filters on any change; restore saved filters after the page's own init
        document.addEventListener('change', function (e) {
            if (e.target && e.target.id && e.target.id.indexOf('filter') === 0) saveFilters();
        }, true);
        setTimeout(restoreFilters, 350);
        // Deep-link quick actions from the dashboard (?new=1 / ?pay=1)
        try {
            var qp = new URLSearchParams(location.search);
            if (qp.get('new') === '1') {
                var addBtn = document.querySelector('[data-bs-target="#expenseModal"],[data-bs-target="#fuelModal"],[data-bs-target="#billModal"]');
                if (addBtn) setTimeout(function () { addBtn.click(); }, 400);
            } else if (qp.get('pay') === '1') {
                var payBtn = document.querySelector('[data-bs-target="#recvModal"]');
                if (payBtn) setTimeout(function () { payBtn.click(); }, 400);
            }
        } catch (e) {}
        // Billing/Expenses "Clear" also forgets saved filters
        document.querySelectorAll('button').forEach(function (b) {
            if (/^\s*clear\s*$/i.test(b.textContent)) b.addEventListener('click', UX.clearFilters);
        });
    });
})();
