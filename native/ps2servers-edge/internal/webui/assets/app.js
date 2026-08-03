// Router
function handleHashChange() {
    let hash = window.location.hash.substring(1) || 'dashboard';
    
    // Update nav links
    document.querySelectorAll('.nav-link').forEach(link => {
        link.classList.remove('active');
        if (link.dataset.tab === hash) link.classList.add('active');
    });

    // Update tab content
    document.querySelectorAll('.tab-content').forEach(tab => {
        tab.classList.remove('active');
    });
    const targetTab = document.getElementById('tab-' + hash);
    if (targetTab) targetTab.classList.add('active');

    // Trigger tab-specific logic
    if (['udpfs', 'smb', 'udpbd'].includes(hash)) {
        if (!configCache[hash]) formActions.load(hash);
    } else if (hash === 'about') {
        loadInfo();
    }
}
window.addEventListener('hashchange', handleHashChange);

// API Helpers
async function api(path, options = {}) {
    try {
        const res = await fetch(path, options);
        if (!res.ok) {
            let msg = `HTTP ${res.status}`;
            try { const err = await res.json(); if(err.error) msg = err.error; } catch(e){}
            throw new Error(msg);
        }
        return await res.json();
    } catch (e) {
        showToast(e.message, 'error');
        throw e;
    }
}

// Toasts
function showToast(message, type = 'info') {
    const container = document.getElementById('toast-container');
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.textContent = message;
    container.appendChild(toast);
    
    setTimeout(() => {
        toast.classList.add('hiding');
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}

// Dashboard
function formatUptime(seconds) {
    if (seconds == null || seconds < 0) return '-';
    if (seconds === 0) return '0s';
    const d = Math.floor(seconds / (3600*24));
    const h = Math.floor(seconds % (3600*24) / 3600);
    const m = Math.floor(seconds % 3600 / 60);
    const s = Math.floor(seconds % 60);
    let out = [];
    if (d > 0) out.push(d + 'd');
    if (h > 0) out.push(h + 'h');
    if (m > 0) out.push(m + 'm');
    if (s > 0 || out.length === 0) out.push(s + 's');
    return out.join(' ');
}

function updateStatusBadge(id, status) {
    const el = document.getElementById(id);
    if (!el) return;
    el.textContent = status.toUpperCase();
    el.className = 'badge';
    if (status === 'ready') el.classList.add('badge-ok');
    else if (status === 'busy' || status === 'degraded') el.classList.add('badge-warn');
    else if (status === 'stopped') el.classList.add('badge-error');
    else el.classList.add('badge-grey');
}

async function pollDashboard() {
    if (window.location.hash !== '' && window.location.hash !== '#dashboard') return;
    try {
        const res = await fetch('/api/status');
        if (!res.ok) return;
        const data = await res.json();
        
        ['udpfs', 'smb', 'udpbd'].forEach(svc => {
            const s = data[svc];
            if (s) {
                updateStatusBadge(`dash-${svc}-status`, s.status || 'unknown');
                document.getElementById(`dash-${svc}-sessions`).textContent = s.sessions || 0;
                document.getElementById(`dash-${svc}-uptime`).textContent = formatUptime(s.uptime);
            }
        });
        
        if (data.system) {
            document.getElementById('dash-sys-version').textContent = data.system.version || '-';
            document.getElementById('dash-sys-platform').textContent = data.system.platform || '-';
            document.getElementById('dash-sys-arch').textContent = data.system.arch || '-';
        }
    } catch(e) {}
}
setInterval(pollDashboard, 2000);

async function restartService(service) {
    try {
        await api('/api/restart', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ service })
        });
        showToast(`${service} restart initiated`, 'success');
        pollDashboard();
    } catch(e) {}
}

// Config Forms
const configCache = { udpfs: null, smb: null, udpbd: null };

const formActions = {
    async load(service) {
        try {
            const data = await api('/api/config');
            configCache[service] = data[service] || {};
            this.populate(service, configCache[service]);
            this.checkDirty(service);
        } catch(e) {}
    },
    async reset(service) {
        await this.load(service);
        showToast('Reset to saved settings', 'info');
    },
    async defaults(service) {
        try {
            const data = await api('/api/config/defaults');
            this.populate(service, data[service] || {});
            this.checkDirty(service);
            showToast('Loaded defaults (not saved)', 'info');
        } catch(e) {}
    },
    populate(service, data) {
        const form = document.getElementById(`form-${service}`);
        if (!form || !data) return;
        
        Array.from(form.elements).forEach(input => {
            if (!input.name || input.name.startsWith('_')) return;
            let val = data[input.name];
            if (val === undefined) return;
            
            if (input.type === 'checkbox') input.checked = !!val;
            else input.value = val;
        });

        // Special handlers
        if (service === 'udpfs') {
            const decomp = form.querySelector('[name="_decompress"]');
            if (decomp) decomp.checked = !data.no_compression;
        }
    },
    getValues(service) {
        const form = document.getElementById(`form-${service}`);
        const data = {};
        Array.from(form.elements).forEach(input => {
            if (!input.name || input.name.startsWith('_')) return;
            
            let val = input.type === 'checkbox' ? input.checked : input.value;
            
            // Type conversions
            if (input.type === 'number') {
                val = val === '' ? 0 : parseInt(val, 10);
            }
            if (input.name === 'sector_size') val = parseInt(val, 10);
            
            data[input.name] = val;
        });

        // Special handlers
        if (service === 'udpfs') {
            const decomp = form.querySelector('[name="_decompress"]');
            if (decomp) data.no_compression = !decomp.checked;
        }
        
        return data;
    },
    async save(service, quiet = false) {
        const data = this.getValues(service);
        
        // Basic Validation
        if (data.port < 0 || data.port > 65535) { showToast('Invalid port', 'error'); return false; }
        if (service === 'smb' && data.share && !data.share.includes('=')) { showToast('SMB share must be NAME=PATH format', 'error'); return false; }
        
        try {
            const fullConfig = await api('/api/config');
            fullConfig[service] = data;
            
            await api('/api/config', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(fullConfig)
            });
            
            configCache[service] = data;
            this.checkDirty(service);
            if (!quiet) showToast(`${service.toUpperCase()} config saved`, 'success');
            return true;
        } catch(e) {
            return false;
        }
    },
    async apply(service) {
        const saved = await this.save(service, true);
        if (saved) {
            await restartService(service);
            showToast(`${service.toUpperCase()} saved and applied`, 'success');
        }
    },
    checkDirty(service) {
        if (!configCache[service]) return;
        const current = this.getValues(service);
        const saved = configCache[service];
        
        let isDirty = false;
        for (const key in current) {
            if (current[key] !== saved[key]) {
                isDirty = true; break;
            }
        }
        
        const navLink = document.querySelector(`.nav-link[data-tab="${service}"]`);
        if (navLink) {
            if (isDirty) navLink.classList.add('dirty');
            else navLink.classList.remove('dirty');
        }
    }
};

// Listen for form changes to update dirty state
document.querySelectorAll('.config-form').forEach(form => {
    form.addEventListener('input', (e) => {
        formActions.checkDirty(form.dataset.service);
    });
    form.addEventListener('change', (e) => {
        formActions.checkDirty(form.dataset.service);
    });
});

// UI Interaction
function toggleCollapse(id) {
    const el = document.getElementById(id);
    const header = el.previousElementSibling;
    if (el.classList.contains('collapsed')) {
        el.classList.remove('collapsed');
        header.classList.add('open');
    } else {
        el.classList.add('collapsed');
        header.classList.remove('open');
    }
}

// Info Tab
async function loadInfo() {
    try {
        const data = await api('/api/info');
        document.getElementById('about-sys-version').textContent = data.version || '-';
        document.getElementById('about-sys-platform').textContent = data.platform || '-';
        document.getElementById('about-sys-arch').textContent = data.arch || '-';
    } catch(e) {}
}

// File Browser
let currentBrowserForm = null;
let currentBrowserInput = null;
let currentBrowserPath = '';
let currentBrowserIsDirOnly = true;

async function openBrowser(formId, inputName, isDirOnly) {
    currentBrowserForm = formId;
    currentBrowserInput = inputName;
    currentBrowserIsDirOnly = isDirOnly;
    
    const input = document.querySelector(`#${formId} [name="${inputName}"]`);
    currentBrowserPath = input.value || '/';
    // If it's a file path, we want to browse its parent dir
    if (!isDirOnly && currentBrowserPath !== '/') {
        const lastSlash = currentBrowserPath.lastIndexOf('/');
        const lastBackslash = currentBrowserPath.lastIndexOf('\\');
        const sep = lastSlash > lastBackslash ? lastSlash : lastBackslash;
        if (sep > 0) currentBrowserPath = currentBrowserPath.substring(0, sep);
    }
    
    document.getElementById('browser-modal').classList.add('show');
    await loadBrowserPath(currentBrowserPath);
}

function closeBrowser() {
    document.getElementById('browser-modal').classList.remove('show');
}

function selectBrowserPath() {
    if (currentBrowserInput && currentBrowserForm) {
        const input = document.querySelector(`#${currentBrowserForm} [name="${currentBrowserInput}"]`);
        input.value = currentBrowserPath;
        // Trigger input event to update dirty state
        input.dispatchEvent(new Event('input', { bubbles: true }));
    }
    closeBrowser();
}

function selectFile(fullPath) {
    if (currentBrowserInput && currentBrowserForm) {
        const input = document.querySelector(`#${currentBrowserForm} [name="${currentBrowserInput}"]`);
        input.value = fullPath;
        input.dispatchEvent(new Event('input', { bubbles: true }));
    }
    closeBrowser();
}

async function loadBrowserPath(path) {
    const list = document.getElementById('browser-list');
    list.innerHTML = '<div class="muted" style="padding:1rem;">Loading...</div>';
    
    try {
        const res = await api(`/api/browse?path=${encodeURIComponent(path)}`);
        currentBrowserPath = res.path; // server returns resolved canonical path
        
        // Render Breadcrumbs
        const breadcrumbs = document.getElementById('browser-breadcrumbs');
        breadcrumbs.innerHTML = '';
        
        // Very basic split for UI (handles both slashes)
        let parts = currentBrowserPath.split(/[/\\]/).filter(Boolean);
        if (currentBrowserPath.startsWith('/')) {
            addBreadcrumb('Root', '/', 0, parts);
        } else if (parts.length > 0 && parts[0].includes(':')) {
            // Windows drive root
            addBreadcrumb(parts[0] + '\\', parts[0] + '\\', 0, parts);
            parts.shift();
        } else {
            addBreadcrumb('Root', '/', 0, parts);
        }

        let accum = currentBrowserPath.startsWith('/') ? '/' : (currentBrowserPath.split(/[/\\]/)[0] + '\\');
        parts.forEach((part, i) => {
            breadcrumbs.insertAdjacentHTML('beforeend', '<span class="breadcrumb-sep">/</span>');
            accum += (accum.endsWith('/') || accum.endsWith('\\') ? '' : '/') + part;
            addBreadcrumb(part, accum, i+1, parts);
        });
        
        // Render List
        list.innerHTML = '';
        
        // Parent dir item
        if (res.parent) {
            const up = document.createElement('div');
            up.className = 'browser-item';
            up.innerHTML = `<span class="browser-item-icon">📁</span><span>.. (Up)</span>`;
            up.onclick = () => loadBrowserPath(res.parent);
            list.appendChild(up);
        }

        if (!res.items || res.items.length === 0) {
            list.insertAdjacentHTML('beforeend', '<div class="muted" style="padding:1rem;">Empty directory</div>');
            return;
        }
        
        res.items.forEach(item => {
            if (currentBrowserIsDirOnly && !item.is_dir) return; // filter files if dir only
            
            const div = document.createElement('div');
            div.className = 'browser-item';
            
            const icon = item.is_dir ? '📁' : '📄';
            let sizeStr = '';
            if (!item.is_dir && item.size !== undefined) {
                const mb = (item.size / (1024*1024)).toFixed(2);
                sizeStr = `<span class="muted" style="margin-left:auto; font-size:0.8rem;">${mb} MB</span>`;
            }
            
            div.innerHTML = `<span class="browser-item-icon">${icon}</span><span>${item.name}</span>${sizeStr}`;
            
            let fullPath = currentBrowserPath;
            if (!fullPath.endsWith('/') && !fullPath.endsWith('\\')) {
                fullPath += currentBrowserPath.includes('\\') ? '\\' : '/';
            }
            fullPath += item.name;
            
            if (item.is_dir) {
                div.onclick = () => loadBrowserPath(fullPath);
            } else {
                div.onclick = () => selectFile(fullPath);
            }
            list.appendChild(div);
        });
        
    } catch(e) {
        list.innerHTML = `<div class="toast error" style="position:static; margin:1rem;">${e.message}</div>`;
    }
}

function addBreadcrumb(label, path, index, parts) {
    const el = document.createElement('span');
    el.className = 'breadcrumb-item';
    el.textContent = label;
    el.onclick = () => loadBrowserPath(path);
    document.getElementById('browser-breadcrumbs').appendChild(el);
}

// Logs Viewer (SSE)
let evtSource = null;
const logViewer = document.getElementById('log-viewer');
const logAutoScroll = document.getElementById('log-autoscroll');
const logStatus = document.getElementById('log-status');
let reconnectTimeout = null;

function clearLogs() {
    logViewer.textContent = '';
}

function connectLogs() {
    if (evtSource) return;
    
    evtSource = new EventSource('/api/logs');
    
    evtSource.onopen = () => {
        logStatus.textContent = 'Connected';
        logStatus.className = 'badge badge-ok';
        if (reconnectTimeout) clearTimeout(reconnectTimeout);
    };
    
    evtSource.onmessage = (e) => {
        // Append
        logViewer.appendChild(document.createTextNode(e.data + '\n'));
        
        // Cap lines
        if (logViewer.childNodes.length > 1000) {
            logViewer.removeChild(logViewer.firstChild);
        }
        
        if (logAutoScroll.checked) {
            logViewer.scrollTop = logViewer.scrollHeight;
        }
    };
    
    evtSource.onerror = () => {
        logStatus.textContent = 'Reconnecting...';
        logStatus.className = 'badge badge-warn';
        evtSource.close();
        evtSource = null;
        reconnectTimeout = setTimeout(connectLogs, 2000); // 2s backoff
    };
}

// Init
document.addEventListener('DOMContentLoaded', () => {
    handleHashChange();
    pollDashboard();
    connectLogs();
});
