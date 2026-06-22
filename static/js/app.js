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
