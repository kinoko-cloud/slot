/**
 * リアルタイムデータ取得用JavaScript
 * Cloudflare Pages静的サイトからPythonAnywhere APIを呼び出す
 */

const API_BASE = 'https://autogmail.pythonanywhere.com';

// ランク色
const RANK_COLORS = {
    'S': '#ff6b6b',
    'A': '#ffa502',
    'B': '#2ed573',
    'C': '#70a1ff',
    'D': '#747d8c',
};

// 枚数バッジ
function getMedalsBadge(value) {
    const num = parseInt(value);
    if (isNaN(num)) return null;
    if (num >= 10000) return { class: 'medals-10k', icon: '🔥', label: '1万枚OVER' };
    if (num >= 5000) return { class: 'medals-5k', icon: '💰', label: '5千枚OVER' };
    if (num >= 3000) return { class: 'medals-3k', icon: '✨', label: '3千枚OVER' };
    if (num >= 2000) return { class: 'medals-2k', icon: '⭐', label: '2千枚OVER' };
    if (num >= 1000) return { class: 'medals-1k', icon: '👍', label: '1千枚OVER' };
    return null;
}

// 符号付き数値フォーマット
function formatSignedNumber(value) {
    const num = parseInt(value);
    if (isNaN(num)) return value;
    if (num >= 0) return '+' + num.toLocaleString();
    return num.toLocaleString();
}

// トップページのリアルタイム更新
async function updateIndexPage() {
    const statusEl = document.getElementById('realtime-status');
    if (statusEl) {
        statusEl.textContent = '更新中...';
        statusEl.className = 'realtime-status loading';
    }

    try {
        const response = await fetch(`${API_BASE}/api/v2/index`);
        if (!response.ok) throw new Error('API error');
        const data = await response.json();

        // 更新時刻を表示
        if (statusEl) {
            const updatedAt = new Date(data.updated_at);
            const now = new Date();
            const ageMinutes = Math.floor((now - updatedAt) / 60000);

            if (ageMinutes > 30) {
                const hours = Math.floor(ageMinutes / 60);
                const mins = ageMinutes % 60;
                statusEl.textContent = `${hours}時間${mins}分前のデータ`;
                statusEl.className = 'realtime-status stale';
            } else if (ageMinutes > 10) {
                statusEl.textContent = `${ageMinutes}分前のデータ`;
                statusEl.className = 'realtime-status warning';
            } else {
                statusEl.textContent = `${updatedAt.getHours()}:${String(updatedAt.getMinutes()).padStart(2, '0')} 更新`;
                statusEl.className = 'realtime-status success';
            }
        }

        // トップ3を更新
        updateTop3(data.top3);

        // モードバッジを更新
        updateModeBadge(data.display_mode, data.is_open);

    } catch (error) {
        console.error('Failed to fetch realtime data:', error);
        if (statusEl) {
            statusEl.textContent = 'リアルタイム取得失敗';
            statusEl.className = 'realtime-status error';
        }
    }
}

// トップ3の更新
function updateTop3(top3) {
    const container = document.getElementById('top3-container');
    if (!container || !top3 || top3.length === 0) return;

    container.innerHTML = top3.map(rec => {
        const badge = getMedalsBadge(rec.max_medals);
        const badgeHtml = badge ? `<span class="medals-badge ${badge.class}">${badge.icon} ${badge.label}</span>` : '';
        const availClass = rec.availability === '空き' ? 'available' : (rec.availability === '遊技中' ? 'playing' : '');
        const availText = rec.availability || '';

        return `
            <a href="/recommend/${rec.store_key}.html" class="top-unit-card">
                <div class="unit-header">
                    <span class="machine-icon">${rec.machine_icon}</span>
                    <span class="unit-number">${rec.unit_id}番台</span>
                    <span class="rank-badge" style="background-color: ${RANK_COLORS[rec.final_rank] || RANK_COLORS.D}">${rec.final_rank}</span>
                    ${availText ? `<span class="availability-badge ${availClass}">${availText}</span>` : ''}
                </div>
                <div class="unit-store">${rec.store_name}</div>
                <div class="unit-stats">
                    ${rec.today_art ? `<span>本日ART: ${rec.today_art}回</span>` : ''}
                    ${rec.max_medals ? `<span>最大: ${rec.max_medals.toLocaleString()}枚</span>` : ''}
                    ${badgeHtml}
                </div>
                <div class="unit-reasons">${(rec.reasons || []).join(' ')}</div>
            </a>
        `;
    }).join('');
}

// モードバッジの更新
function updateModeBadge(mode, isOpen) {
    const badge = document.querySelector('.mode-badge');
    if (!badge) return;

    badge.className = 'mode-badge ' + mode;
    if (mode === 'realtime') {
        badge.textContent = '営業中';
    } else if (mode === 'collecting') {
        badge.textContent = '集計中';
    } else if (mode === 'before_open') {
        badge.textContent = '営業前';
    } else {
        badge.textContent = '閉店後';
    }
}

// 推奨ページのリアルタイム更新
async function updateRecommendPage(storeKey) {
    const statusEl = document.getElementById('realtime-status');
    if (statusEl) {
        statusEl.textContent = '更新中...';
        statusEl.className = 'realtime-status loading';
    }

    try {
        const response = await fetch(`${API_BASE}/api/v2/recommend/${storeKey}`);
        if (!response.ok) throw new Error('API error');
        const data = await response.json();

        // 更新時刻
        if (statusEl) {
            const updatedAt = new Date(data.updated_at);
            const now = new Date();
            const ageMinutes = Math.floor((now - updatedAt) / 60000);

            if (ageMinutes > 30) {
                const hours = Math.floor(ageMinutes / 60);
                const mins = ageMinutes % 60;
                statusEl.textContent = `${hours}時間${mins}分前のデータ`;
                statusEl.className = 'realtime-status stale';
            } else if (ageMinutes > 10) {
                statusEl.textContent = `${ageMinutes}分前のデータ`;
                statusEl.className = 'realtime-status warning';
            } else {
                statusEl.textContent = `${updatedAt.getHours()}:${String(updatedAt.getMinutes()).padStart(2, '0')} 更新`;
                statusEl.className = 'realtime-status success';
            }
        }

        // データ取得時刻
        const updateTimeEl = document.getElementById('update-time');
        if (updateTimeEl && data.cache_info) {
            updateTimeEl.textContent = data.cache_info.fetched_at;
        }

        // 古いデータ警告バナー
        updateStaleWarning(data.updated_at);

        // 推奨台を更新
        updateRecommendations(data.top_recs, 'top-recs-container');
        updateRecommendations(data.other_recs, 'other-recs-container');

    } catch (error) {
        console.error('Failed to fetch realtime data:', error);
        if (statusEl) {
            statusEl.textContent = 'リアルタイム取得失敗';
            statusEl.className = 'realtime-status error';
        }
    }
}

// 推奨台リストの更新
function updateRecommendations(recs, containerId) {
    const container = document.getElementById(containerId);
    if (!container || !recs) return;

    if (recs.length === 0) {
        container.innerHTML = '<p class="no-data">データなし</p>';
        return;
    }

    container.innerHTML = recs.map(rec => {
        const badge = getMedalsBadge(rec.max_medals);
        const badgeHtml = badge ? `<span class="medals-badge ${badge.class}">${badge.icon} ${badge.label}</span>` : '';
        const availClass = rec.availability === '空き' ? 'available' : (rec.availability === '遊技中' ? 'playing' : '');

        return `
            <div class="unit-card ${rec.is_running ? 'running' : ''}">
                <div class="unit-main">
                    <span class="unit-number">${rec.unit_id}番台</span>
                    <span class="rank-badge" style="background-color: ${RANK_COLORS[rec.final_rank] || RANK_COLORS.D}">${rec.final_rank}</span>
                    ${rec.availability ? `<span class="availability-badge ${availClass}">${rec.availability}</span>` : ''}
                </div>
                <div class="unit-stats">
                    ${rec.today_art !== undefined ? `<div>本日ART: <strong>${rec.today_art}回</strong></div>` : ''}
                    ${rec.today_games !== undefined ? `<div>本日G数: <strong>${rec.today_games.toLocaleString()}G</strong></div>` : ''}
                    ${rec.current_games !== undefined ? `<div>現在: <strong>${rec.current_games}G</strong></div>` : ''}
                    ${rec.max_medals ? `<div>最大獲得: <strong>${rec.max_medals.toLocaleString()}枚</strong> ${badgeHtml}</div>` : ''}
                </div>
                <div class="unit-reasons">${(rec.reasons || []).join(' ')}</div>
            </div>
        `;
    }).join('');
}

// ページ読み込み時に実行
document.addEventListener('DOMContentLoaded', function() {
    // ページタイプを判定
    const pageType = document.body.dataset.pageType;
    const storeKey = document.body.dataset.storeKey;

    if (pageType === 'index') {
        updateIndexPage();
        // 5分ごとに更新
        setInterval(updateIndexPage, 5 * 60 * 1000);
    } else if (pageType === 'recommend' && storeKey) {
        updateRecommendPage(storeKey);
        // 3分ごとに更新
        setInterval(() => updateRecommendPage(storeKey), 3 * 60 * 1000);
    }
});

// 古いデータ警告バナーの更新
function updateStaleWarning(updatedAtStr) {
    const existing = document.getElementById('stale-warning-banner');
    if (existing) existing.remove();

    if (!updatedAtStr) return;

    const updatedAt = new Date(updatedAtStr);
    const now = new Date();
    const ageMinutes = Math.floor((now - updatedAt) / 60000);

    if (ageMinutes > 30) {
        const hours = Math.floor(ageMinutes / 60);
        const mins = ageMinutes % 60;
        const banner = document.createElement('div');
        banner.id = 'stale-warning-banner';
        banner.className = 'stale-warning-banner';
        banner.innerHTML = `データが${hours}時間${mins}分前のものです。「最新データ取得」を押してください。`;
        const container = document.querySelector('.container');
        if (container) {
            container.insertBefore(banner, container.firstChild);
        }
    }
}

// 手動更新ボタン
function refreshData() {
    const pageType = document.body.dataset.pageType;
    const storeKey = document.body.dataset.storeKey;

    if (pageType === 'index') {
        updateIndexPage();
    } else if (pageType === 'recommend' && storeKey) {
        updateRecommendPage(storeKey);
    }
}
