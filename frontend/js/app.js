// ═══════════════════════════════════════════════════════
// Smart Counter AI — Dashboard JavaScript
// ═══════════════════════════════════════════════════════

// Chart.js defaults
Chart.defaults.color = '#6b7280';
Chart.defaults.font.family = "'Inter', sans-serif";
Chart.defaults.font.size = 12;

let genderChart = null;
let ageChart = null;

// ── Relógio ──────────────────────────────────────────
function updateClock() {
    const now = new Date();
    const h = String(now.getHours()).padStart(2, '0');
    const m = String(now.getMinutes()).padStart(2, '0');
    const s = String(now.getSeconds()).padStart(2, '0');
    document.getElementById('clock').textContent = `${h}:${m}:${s}`;
}
setInterval(updateClock, 1000);
updateClock();

// ── Inicializar Gráficos ─────────────────────────────
function initCharts() {
    // Gênero (Doughnut)
    const ctxG = document.getElementById('genderChart').getContext('2d');
    genderChart = new Chart(ctxG, {
        type: 'doughnut',
        data: {
            labels: ['Masculino', 'Feminino', 'Não Identificado'],
            datasets: [{
                data: [0, 0, 0],
                backgroundColor: ['#6366f1', '#ec4899', '#334155'],
                borderWidth: 0,
                hoverOffset: 6,
                spacing: 3
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            cutout: '72%',
            plugins: {
                legend: { display: false },
                tooltip: {
                    backgroundColor: 'rgba(15,15,25,0.9)',
                    titleColor: '#e8eaed',
                    bodyColor: '#9ca3af',
                    borderColor: 'rgba(255,255,255,0.08)',
                    borderWidth: 1,
                    cornerRadius: 8,
                    padding: 10,
                }
            },
            animation: {
                animateRotate: true,
                duration: 600
            }
        }
    });

    // Idade (Horizontal Bar)
    const ctxA = document.getElementById('ageChart').getContext('2d');
    ageChart = new Chart(ctxA, {
        type: 'bar',
        data: {
            labels: ['Criança (0-12)', 'Jovem (15-32)', 'Adulto (38-53)', 'Idoso (60+)', 'N/I'],
            datasets: [{
                label: 'Pessoas',
                data: [0, 0, 0, 0, 0],
                backgroundColor: [
                    'rgba(59, 130, 246, 0.7)',
                    'rgba(99, 102, 241, 0.7)',
                    'rgba(245, 158, 11, 0.7)',
                    'rgba(236, 72, 153, 0.7)',
                    'rgba(51, 65, 85, 0.5)'
                ],
                borderRadius: 6,
                borderSkipped: false,
                barThickness: 24,
            }]
        },
        options: {
            indexAxis: 'y',
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
                tooltip: {
                    backgroundColor: 'rgba(15,15,25,0.9)',
                    titleColor: '#e8eaed',
                    bodyColor: '#9ca3af',
                    borderColor: 'rgba(255,255,255,0.08)',
                    borderWidth: 1,
                    cornerRadius: 8,
                    padding: 10,
                }
            },
            scales: {
                x: {
                    beginAtZero: true,
                    grid: { color: 'rgba(255,255,255,0.04)', drawBorder: false },
                    ticks: { precision: 0, color: '#4b5563' }
                },
                y: {
                    grid: { display: false },
                    ticks: { color: '#9ca3af', font: { weight: 500 } }
                }
            },
            animation: { duration: 500 }
        }
    });
}

// ── Animação de número incremental ───────────────────
function animateValue(el, newVal) {
    const current = parseInt(el.textContent) || 0;
    if (current === newVal) return;

    const diff = newVal - current;
    const steps = Math.min(Math.abs(diff), 15);
    const stepTime = Math.max(20, 300 / steps);
    let step = 0;

    const interval = setInterval(() => {
        step++;
        const progress = step / steps;
        const value = Math.round(current + diff * progress);
        el.textContent = value;
        if (step >= steps) {
            el.textContent = newVal;
            clearInterval(interval);
        }
    }, stepTime);
}

// ── Traduzir Gênero ──────────────────────────────────
function translateGender(g) {
    if (g === 'Male') return 'Masculino';
    if (g === 'Female') return 'Feminino';
    return 'N/I';
}

// ── Atualizar Dashboard ──────────────────────────────
let lastEventCount = 0;

function updateDashboard(data) {
    // KPIs
    animateValue(document.getElementById('kpi-current'), data.current);
    animateValue(document.getElementById('kpi-entries'), data.entries);
    animateValue(document.getElementById('kpi-exits'), data.exits);

    // Gênero
    const gm = data.gender['Male'] || 0;
    const gf = data.gender['Female'] || 0;
    const gu = data.gender['Unknown'] || 0;

    genderChart.data.datasets[0].data = [gm, gf, gu];
    genderChart.update('none');

    document.getElementById('g-male').textContent = gm;
    document.getElementById('g-female').textContent = gf;
    document.getElementById('g-unknown').textContent = gu;

    // Idade
    const age = data.age || {};
    ageChart.data.datasets[0].data = [
        age['Criança (0-12)'] || 0,
        age['Jovem (15-32)'] || 0,
        age['Adulto (38-53)'] || 0,
        age['Idoso (60+)'] || 0,
        age['Desconhecido'] || 0
    ];
    ageChart.update('none');

    // Timeline
    const events = data.last_events || [];
    const totalEvents = data.entries + data.exits;
    document.getElementById('event-count').textContent = `${totalEvents} evento${totalEvents !== 1 ? 's' : ''}`;

    if (events.length !== lastEventCount) {
        lastEventCount = events.length;
        const container = document.getElementById('timeline-list');

        if (events.length === 0) {
            container.innerHTML = '<p class="timeline-empty">Aguardando detecções...</p>';
        } else {
            // Mostrar os últimos eventos em ordem reversa
            const reversed = [...events].reverse();
            container.innerHTML = reversed.map(ev => {
                const typeLabel = ev.type === 'entry' ? 'Entrada' : 'Saída';
                const typeClass = ev.type;
                const genderText = translateGender(ev.gender);
                return `
                    <div class="tl-item">
                        <span class="tl-time">${ev.time}</span>
                        <span class="tl-tag ${typeClass}">${typeLabel}</span>
                        <span class="tl-detail"><strong>${genderText}</strong> · ${ev.age}</span>
                    </div>
                `;
            }).join('');

            // Scroll para o topo (último evento)
            container.scrollTop = 0;
        }
    }
}

// ── Polling ──────────────────────────────────────────
async function fetchStats() {
    try {
        const res = await fetch('/api/stats');
        if (!res.ok) return;
        const data = await res.json();
        updateDashboard(data);
    } catch {
        // Backend ainda não disponível
    }
}

// ── Modal Settings ───────────────────────────────────
const btnSettings = document.getElementById('btn-settings');
const btnCloseSettings = document.getElementById('btn-close-settings');
const modalSettings = document.getElementById('settings-modal');

const confMirror = document.getElementById('conf-mirror');
const confSwap = document.getElementById('conf-swap');
const confFace = document.getElementById('conf-face');
const faceConfVal = document.getElementById('face-conf-val');

const btnSaveSettings = document.getElementById('btn-save-settings');
const btnResetData = document.getElementById('btn-reset-data');

function openSettings() {
    modalSettings.classList.add('show');
    // Fetch config
    fetch('/api/config')
        .then(res => res.json())
        .then(data => {
            if (!data.error) {
                confMirror.checked = data.mirror_camera;
                confSwap.checked = data.swap_direction;
                confFace.value = Math.round(data.face_conf_threshold * 100);
                faceConfVal.textContent = confFace.value + '%';
            }
        });
}

function closeSettings() {
    modalSettings.classList.remove('show');
}

if(btnSettings) {
    btnSettings.addEventListener('click', openSettings);
    btnCloseSettings.addEventListener('click', closeSettings);
    modalSettings.addEventListener('click', (e) => {
        if (e.target === modalSettings) closeSettings();
    });

    confFace.addEventListener('input', (e) => {
        faceConfVal.textContent = e.target.value + '%';
    });

    btnSaveSettings.addEventListener('click', () => {
        btnSaveSettings.textContent = "Salvando...";
        const payload = {
            mirror_camera: confMirror.checked,
            swap_direction: confSwap.checked,
            face_conf_threshold: parseInt(confFace.value) / 100
        };
        fetch('/api/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        }).then(res => {
            btnSaveSettings.textContent = "Salvar Ajustes";
            if(res.ok) closeSettings();
        });
    });

    btnResetData.addEventListener('click', () => {
        if (confirm('Tem certeza que deseja zerar a contagem do sistema?')) {
            fetch('/api/reset', { method: 'POST' })
                .then(() => {
                    closeSettings();
                    lastEventCount = 0;
                    document.getElementById('timeline-list').innerHTML = '<p class="timeline-empty">Aguardando detecções...</p>';
                    fetchStats();
                });
        }
    });
}

const btnShutdown = document.getElementById('btn-shutdown');
if (btnShutdown) {
    btnShutdown.addEventListener('click', () => {
        if (confirm('Atenção: A câmera e o servidor serão encerrados definitivamente.\nDeseja desligar o sistema?')) {
            fetch('/api/shutdown', { method: 'POST' });
            document.body.innerHTML = `
                <div style="height: 100vh; display: flex; flex-direction: column; align-items: center; justify-content: center; background: #0a0a0a; color: white; font-family: Inter, sans-serif;">
                    <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="#ef4444" stroke-width="2" style="margin-bottom: 20px;"><path d="M18.36 6.64a9 9 0 1 1-12.73 0"></path><line x1="12" y1="2" x2="12" y2="12"></line></svg>
                    <h1 style="font-size: 24px; font-weight: 600; margin: 0 0 8px 0;">Sistema Encerrado</h1>
                    <p style="color: #9ca3af; margin: 0;">Você já pode fechar esta janela com segurança.</p>
                </div>
            `;
        }
    });
}

// ── Inicialização ────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    initCharts();
    fetchStats();
    setInterval(fetchStats, 1000);
});
