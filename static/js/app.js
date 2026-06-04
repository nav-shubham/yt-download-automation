// Global State Variables
let currentTab = 'dashboard';
let statsInterval = null;
let queueInterval = null;
let activeMetadata = null; // Cache fetched URL metadata

// Circumference of Quota gauge ring (r=50)
const QUOTA_CIRCUMFERENCE = 314.159;

// ==========================================================================
// APP INITIALIZATION
// ==========================================================================
document.addEventListener('DOMContentLoaded', () => {
    // Switch to initial tab
    switchTab('dashboard');

    // Load configurations and settings
    loadSystemSettings();

    // Trigger initial stats load
    refreshStats();

    // Listen to Enter key on URL input
    document.getElementById('url-input').addEventListener('keypress', (e) => {
        if (e.key === 'Enter') {
            analyzeURL();
        }
    });

    // Check URL input dynamically to show/hide clear button
    document.getElementById('url-input').addEventListener('input', (e) => {
        const btn = document.getElementById('btn-clear-url');
        if (e.target.value.trim() !== '') {
            btn.style.display = 'block';
        } else {
            btn.style.display = 'none';
        }
    });

    // Start background polling (Every 1.5 seconds)
    startPollingLoops();
});

function startPollingLoops() {
    // Clear any existing intervals
    if (queueInterval) clearInterval(queueInterval);
    if (statsInterval) clearInterval(statsInterval);

    // Poll active download progresses
    queueInterval = setInterval(() => {
        if (currentTab === 'queue' || currentTab === 'dashboard') {
            refreshQueue();
        }
    }, 1500);

    // Poll daily stats less frequently
    statsInterval = setInterval(refreshStats, 5000);
}

// ==========================================================================
// TAB ROUTING CONTROLLER
// ==========================================================================
function switchTab(tabId) {
    currentTab = tabId;

    // Toggle menu button active states
    document.querySelectorAll('.nav-item').forEach(btn => {
        btn.classList.remove('active');
    });
    
    const activeBtn = document.getElementById(`nav-btn-${tabId}`);
    if (activeBtn) activeBtn.classList.add('active');

    // Toggle tab panels
    document.querySelectorAll('.tab-panel').forEach(panel => {
        panel.classList.remove('active');
    });
    
    const activePanel = document.getElementById(`panel-${tabId}`);
    if (activePanel) activePanel.classList.add('active');

    // Load data specific to tabs
    if (tabId === 'history') {
        loadHistory();
    } else if (tabId === 'queue') {
        refreshQueue();
    } else if (tabId === 'settings') {
        loadSystemSettings();
    }
}

// ==========================================================================
// FLOATING TOAST NOTIFIER
// ==========================================================================
function showToast(message, type = 'info') {
    const toastArea = document.getElementById('toast-area');
    const toastId = 'toast-' + Date.now();
    
    // Select Icon based on type
    let icon = 'fa-circle-info';
    if (type === 'success') icon = 'fa-circle-check';
    if (type === 'error') icon = 'fa-circle-xmark';

    const toastHTML = `
        <div class="toast toast-${type}" id="${toastId}">
            <i class="fa-solid ${icon}"></i>
            <div class="toast-content">${message}</div>
            <button class="toast-close" onclick="closeToast('${toastId}')"><i class="fa-solid fa-xmark"></i></button>
        </div>
    `;

    toastArea.insertAdjacentHTML('beforeend', toastHTML);

    // Auto-remove after 4 seconds
    setTimeout(() => {
        closeToast(toastId);
    }, 4000);
}

function closeToast(toastId) {
    const toast = document.getElementById(toastId);
    if (toast) {
        toast.style.opacity = '0';
        toast.style.transform = 'translateX(50px)';
        toast.style.transition = 'all 0.3s ease';
        setTimeout(() => toast.remove(), 300);
    }
}

// ==========================================================================
// API CLIENT: URL ANALYZER
// ==========================================================================
async function analyzeURL() {
    const urlInput = document.getElementById('url-input');
    const url = urlInput.value.trim();

    if (!url) {
        showToast('Please paste a valid video or playlist URL first.', 'error');
        return;
    }

    // Toggle loader and hide previous metadata card
    const loader = document.getElementById('analysis-loader');
    const metaCard = document.getElementById('metadata-card');
    const btn = document.getElementById('btn-analyze');

    loader.style.display = 'flex';
    metaCard.style.display = 'none';
    btn.disabled = true;

    try {
        const response = await fetch('/api/fetch-info', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url })
        });

        const data = await response.json();

        if (data.status === 'success') {
            activeMetadata = data; // Cache
            activeMetadata.url = url; // Inject URL

            // Populate Card
            document.getElementById('meta-thumb').src = data.thumbnail || '/static/img/placeholder.jpg';
            document.getElementById('meta-title').innerText = data.title;
            document.getElementById('meta-uploader').innerText = data.uploader;
            document.getElementById('meta-duration').innerText = data.duration;

            // Populate Resolution Selector dropdown
            const resSelect = document.getElementById('select-resolution');
            resSelect.innerHTML = '';
            data.resolutions.forEach(res => {
                const opt = document.createElement('option');
                opt.value = res;
                opt.innerText = res + 'p';
                resSelect.appendChild(opt);
            });

            // Handle default selections if present
            const settings = await (await fetch('/api/settings')).json();
            if (settings.default_resolution && data.resolutions.includes(settings.default_resolution)) {
                resSelect.value = settings.default_resolution;
            }

            // Show metadata card and hide loader
            loader.style.display = 'none';
            metaCard.style.display = 'flex';
            showToast('Media parsed successfully. Configure output settings below.', 'success');
        } else {
            throw new Error(data.message || 'Stream extraction failed');
        }
    } catch (err) {
        loader.style.display = 'none';
        showToast(err.message || 'Error occurred querying URL.', 'error');
    } finally {
        btn.disabled = false;
    }
}

function onModeChange() {
    const mode = document.getElementById('select-mode').value;
    const resGroup = document.getElementById('config-res-item');
    if (mode === 'audio') {
        resGroup.style.display = 'none';
    } else {
        resGroup.style.display = 'flex';
    }
}

// ==========================================================================
// API CLIENT: QUEUE ADDITIONS & CONTROLS
// ==========================================================================
async function addToQueue(startImmediately) {
    if (!activeMetadata) return;

    const mode = document.getElementById('select-mode').value;
    const resolution = document.getElementById('select-resolution').value;

    const payload = {
        url: activeMetadata.url,
        title: activeMetadata.title,
        mode: mode,
        resolution: resolution,
        is_playlist: activeMetadata.is_playlist,
        thumbnail: activeMetadata.thumbnail
    };

    try {
        const response = await fetch('/api/queue/add', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        const data = await response.json();
        if (data.status === 'success') {
            showToast(`"${activeMetadata.title}" successfully registered in queue.`, 'success');
            
            // Clear Dashboard inputs
            document.getElementById('url-input').value = '';
            document.getElementById('btn-clear-url').style.display = 'none';
            document.getElementById('metadata-card').style.display = 'none';
            activeMetadata = null;

            // Shift tabs or refresh lists
            if (startImmediately) {
                switchTab('queue');
            } else {
                refreshQueue();
            }
            refreshStats();
        } else {
            throw new Error(data.message);
        }
    } catch (err) {
        showToast('Failed to add media to download worker.', 'error');
    }
}

async function refreshQueue() {
    try {
        const response = await fetch('/api/queue');
        const tasks = await response.json();

        // 1. Populate Queue table (Queue tab panel)
        const tbody = document.getElementById('queue-tbody');
        let activeCount = 0;

        if (tasks.length === 0) {
            tbody.innerHTML = `
                <tr>
                    <td colspan="7" class="loading-state">
                        <p class="empty-state">No active or pending items in download queue.</p>
                    </td>
                </tr>
            `;
        } else {
            let tbodyHTML = '';
            tasks.forEach(task => {
                const isDownloading = task.status === 'downloading' || task.status === 'merging';
                if (task.status !== 'completed' && task.status !== 'failed') {
                    activeCount++;
                }

                // Title Section
                const mediaThumb = task.thumbnail || '/static/img/placeholder.jpg';
                const qualityStr = task.mode === 'audio' ? 'Audio (M4A)' : `${task.resolution}p`;

                // Status Badge
                let badgeClass = `status-${task.status}`;
                let statusLabel = task.status;
                if (task.status === 'downloading') statusLabel = `<i class="fa-solid fa-spinner fa-spin"></i> downloading`;

                // Progress Info
                const percent = task.percent || 0;
                
                // Controls Column
                let controlButtons = '';
                if (task.status === 'downloading' || task.status === 'queued') {
                    controlButtons = `
                        <button class="control-icon-btn pause-hover" onclick="controlQueueItem('${task.id}', 'pause')" title="Pause Extraction">
                            <i class="fa-solid fa-pause"></i>
                        </button>
                    `;
                } else if (task.status === 'paused' || task.status === 'failed') {
                    controlButtons = `
                        <button class="control-icon-btn play-hover" onclick="controlQueueItem('${task.id}', 'resume')" title="Resume Download">
                            <i class="fa-solid fa-play"></i>
                        </button>
                    `;
                }
                
                // Delete button always there
                controlButtons += `
                    <button class="control-icon-btn delete-hover" onclick="controlQueueItem('${task.id}', 'delete')" title="Delete from Queue">
                        <i class="fa-solid fa-trash"></i>
                    </button>
                `;

                // Build Row HTML
                tbodyHTML += `
                    <tr id="row-${task.id}">
                        <td>
                            <div class="title-cell-media">
                                <img src="${mediaThumb}" alt="Thumbnail">
                                <span title="${task.title}">${task.title}</span>
                            </div>
                        </td>
                        <td><span class="tag">${qualityStr}</span></td>
                        <td>
                            <span class="status-badge ${badgeClass}">${statusLabel}</span>
                        </td>
                        <td>
                            <div class="progress-col-area">
                                <div class="progress-number">${percent}%</div>
                                <div class="progress-track">
                                    <div class="progress-fill" style="width: ${percent}%"></div>
                                </div>
                            </div>
                        </td>
                        <td><strong>${task.speed_str || '0 B/s'}</strong></td>
                        <td>${task.eta_str || '--:--'}</td>
                        <td class="actions-col">
                            <div class="row-actions-group">${controlButtons}</div>
                        </td>
                    </tr>
                `;
            });
            tbody.innerHTML = tbodyHTML;
        }

        // Update active badge in sidebar
        document.getElementById('badge-active-count').innerText = activeCount;

        // 2. Populate miniature active lists (Dashboard card panel)
        const miniList = document.getElementById('mini-downloads-list');
        const activeTasks = tasks.filter(t => t.status === 'downloading' || t.status === 'merging');
        
        if (activeTasks.length === 0) {
            miniList.innerHTML = '<p class="empty-state">No downloads running currently.</p>';
        } else {
            let miniHTML = '';
            activeTasks.slice(0, 4).forEach(task => {
                miniHTML += `
                    <div class="mini-item">
                        <img class="mini-thumb" src="${task.thumbnail || '/static/img/placeholder.jpg'}" alt="Thumb">
                        <div class="mini-details">
                            <h5 title="${task.title}">${task.title}</h5>
                            <div class="mini-progress-bar">
                                <div class="mini-progress-fill" style="width: ${task.percent}%"></div>
                            </div>
                            <div class="mini-speed">
                                <span>${task.status === 'merging' ? 'Merging...' : task.speed_str}</span>
                                <span>${task.percent}%</span>
                            </div>
                        </div>
                    </div>
                `;
            });
            miniList.innerHTML = miniHTML;
        }

    } catch (err) {
        console.error('Queue Refresher failed: ', err);
    }
}

async function controlQueueItem(taskId, action) {
    try {
        const response = await fetch('/api/queue/control', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ task_id: taskId, action })
        });
        const data = await response.json();
        if (data.status === 'success') {
            if (action === 'delete') {
                showToast('Queue item removed.', 'info');
            } else {
                showToast(`Task extraction ${action}d successfully.`, 'info');
            }
            refreshQueue();
            refreshStats();
        }
    } catch (err) {
        showToast('Error sending action signal.', 'error');
    }
}

// ==========================================================================
// API CLIENT: HISTORY REGISTRY
// ==========================================================================
async function loadHistory() {
    try {
        const response = await fetch('/api/history');
        const history = await response.json();

        const tbody = document.getElementById('history-tbody');
        if (history.length === 0) {
            tbody.innerHTML = `
                <tr>
                    <td colspan="7" class="loading-state">
                        <p class="empty-state">No logs registered in database history.</p>
                    </td>
                </tr>
            `;
        } else {
            let tbodyHTML = '';
            history.forEach(row => {
                const durationFormatted = row.duration ? formatETADuration(parseInt(row.duration)) : 'Unknown';
                const viewsFormatted = row.views ? parseInt(row.views).toLocaleString() : '0';
                
                tbodyHTML += `
                    <tr>
                        <td><code>${row.video_id}</code></td>
                        <td><strong title="${row.title}">${row.title}</strong></td>
                        <td>${row.channel}</td>
                        <td><i class="fa-solid fa-eye text-muted"></i> ${viewsFormatted}</td>
                        <td>${durationFormatted}</td>
                        <td>${row.fetched_at}</td>
                        <td>
                            <a class="history-action-btn" href="${row.url}" target="_blank" title="Play Video in Web Browser">
                                <i class="fa-solid fa-play"></i>
                            </a>
                        </td>
                    </tr>
                `;
            });
            tbody.innerHTML = tbodyHTML;
        }
    } catch (err) {
        showToast('Failed to fetch download history.', 'error');
    }
}

function filterHistory() {
    const query = document.getElementById('history-filter').value.toLowerCase();
    const rows = document.querySelectorAll('#history-tbody tr');

    rows.forEach(row => {
        const text = row.innerText.toLowerCase();
        if (text.includes(query)) {
            row.style.display = '';
        } else {
            row.style.display = 'none';
        }
    });
}

function formatETADuration(eta_seconds) {
    if (eta_seconds >= 3600) {
        const h = Math.floor(eta_seconds / 3600);
        const m = Math.floor((eta_seconds % 3600) / 60);
        const s = eta_seconds % 60;
        return `${h}h ${m}m ${s}s`;
    } else {
        const m = Math.floor(eta_seconds / 60);
        const s = eta_seconds % 60;
        return `${m}m ${s}s`;
    }
}

// ==========================================================================
// API CLIENT: STATS MONITORING
// ==========================================================================
async function refreshStats() {
    try {
        const response = await fetch('/api/stats');
        const stats = await response.json();

        // Populate Headers Cards
        document.getElementById('stat-today').innerText = `${stats.downloaded_today} / ${stats.daily_quota}`;
        document.getElementById('stat-total').innerText = stats.total_downloads;

        // Circular Progress Update ( r=50 )
        const percent = Math.min(100, Math.round((stats.downloaded_today / stats.daily_quota) * 100));
        document.getElementById('quota-percentage').innerText = percent + '%';
        document.getElementById('quota-used-txt').innerText = stats.downloaded_today;
        document.getElementById('quota-total-txt').innerText = stats.daily_quota;

        const offset = QUOTA_CIRCUMFERENCE - (percent / 100) * QUOTA_CIRCUMFERENCE;
        const circle = document.getElementById('quota-circle');
        circle.style.strokeDashoffset = offset;

        // Quota Warnings
        const warning = document.getElementById('quota-warning-msg');
        if (percent >= 100) {
            warning.innerText = 'Quota Status: Depleted (Reset Tomorrow)';
            warning.style.color = 'var(--danger)';
        } else if (percent >= 80) {
            warning.innerText = 'Quota Status: Approaching Limit';
            warning.style.color = 'var(--warning)';
        } else {
            warning.innerText = 'Quota Status: Optimal';
            warning.style.color = 'var(--accent-cyan)';
        }

    } catch (err) {
        console.error('Stats refresher exception: ', err);
    }
}

// ==========================================================================
// SYSTEM SETTINGS PANEL CONTROLS
// ==========================================================================
async function loadSystemSettings() {
    try {
        const response = await fetch('/api/settings');
        const settings = await response.json();

        document.getElementById('setting-max-concurrent').value = settings.max_concurrent;
        document.getElementById('setting-fragments').value = settings.concurrent_fragments;
        document.getElementById('setting-download-dir').value = settings.download_dir;
        document.getElementById('setting-default-res').value = settings.default_resolution;
        document.getElementById('setting-default-mode').value = settings.default_mode;
        
        const tgCheck = document.getElementById('setting-tg-enabled');
        tgCheck.checked = settings.telegram_enabled;
        toggleTgSettings(settings.telegram_enabled);
        
        document.getElementById('setting-tg-token').value = settings.telegram_bot_token;
        document.getElementById('setting-tg-chat').value = settings.telegram_chat_id;

        // Display Active Queue Limits
        const lbl = document.getElementById('lbl-concurrency');
        if (lbl) lbl.innerText = settings.max_concurrent;

    } catch (err) {
        showToast('Error syncing system configurations.', 'error');
    }
}

function toggleTgSettings(enabled) {
    const container = document.getElementById('tg-settings-fields');
    if (enabled) {
        container.style.opacity = '1';
        container.style.pointerEvents = 'auto';
    } else {
        container.style.opacity = '0.3';
        container.style.pointerEvents = 'none';
    }
}

async function saveSettingsForm(e) {
    e.preventDefault();

    const payload = {
        max_concurrent: parseInt(document.getElementById('setting-max-concurrent').value),
        concurrent_fragments: parseInt(document.getElementById('setting-fragments').value),
        download_dir: document.getElementById('setting-download-dir').value,
        default_resolution: document.getElementById('setting-default-res').value,
        default_mode: document.getElementById('setting-default-mode').value,
        telegram_enabled: document.getElementById('setting-tg-enabled').checked,
        telegram_bot_token: document.getElementById('setting-tg-token').value,
        telegram_chat_id: document.getElementById('setting-tg-chat').value
    };

    try {
        const response = await fetch('/api/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await response.json();
        if (data.status === 'success') {
            showToast('System settings applied successfully.', 'success');
            loadSystemSettings(); // Refresh
        } else {
            throw new Error(data.message);
        }
    } catch (err) {
        showToast('Failed to apply settings configurations.', 'error');
    }
}
