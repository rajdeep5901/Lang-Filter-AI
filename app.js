/* ===== LangFilterAI — Real-Time API Dashboard ===== */
/* Fetches from /status every 500ms using the same origin as Flask. */

const API_BASE = window.location.origin;
const API_STATUS_URL = API_BASE + '/status';
const API_SET_TARGET_URL = API_BASE + '/set_target';
const API_CONFIG_URL = API_BASE + '/config';
const POLL_INTERVAL = 300;  // ms
const FETCH_TIMEOUT_MS = 1500;

const LANGUAGES = {
    en:      { name: 'English',    flag: '🇺🇸' },
    hi:      { name: 'Hindi',      flag: '🇮🇳' },
    or:      { name: 'Odia',       flag: '🇮🇳' },
    bn:      { name: 'Bengali',    flag: '🇮🇳' },
    ta:      { name: 'Tamil',      flag: '🇮🇳' },
    te:      { name: 'Telugu',     flag: '🇮🇳' },
    mr:      { name: 'Marathi',    flag: '🇮🇳' },
    gu:      { name: 'Gujarati',   flag: '🇮🇳' },
    es:      { name: 'Spanish',    flag: '🇪🇸' },
    fr:      { name: 'French',     flag: '🇫🇷' },
    de:      { name: 'German',     flag: '🇩🇪' },
    ja:      { name: 'Japanese',   flag: '🇯🇵' },
    zh:      { name: 'Chinese',    flag: '🇨🇳' },
    ar:      { name: 'Arabic',     flag: '🇸🇦' },
    pt:      { name: 'Portuguese', flag: '🇧🇷' },
    ru:      { name: 'Russian',    flag: '🇷🇺' },
    ko:      { name: 'Korean',     flag: '🇰🇷' },
    it:      { name: 'Italian',    flag: '🇮🇹' },
};

/* ===== STATE ===== */
let state = {
    detected_language: 'unknown',
    raw_language: 'unknown',
    confidence: 0,
    decision: 'MUTE',
    history: [],          // last 10 detections (client-side)
    volume: 0,
    target: 'en',
    apiConnected: false,
    requestCount: 0,
    latencies: [],
    startTime: Date.now(),
};
let statusFetchInFlight = false;

/* ===== DOM REFS ===== */
const $ = id => document.getElementById(id);
const langCode     = $('langCode');
const langName     = $('langName');
const langFlag     = $('langFlag');
const confVal      = $('confVal');
const confFill     = $('confFill');
const statusCard   = $('statusCard');
const statusIcon   = $('statusIcon');
const statusText   = $('statusText');
const statusSub    = $('statusSub');
const volVal       = $('volVal');
const volMeter     = $('volMeter');
const histTimeline = $('histTimeline');
const histCount    = $('histCount');
const targetLang   = $('targetLang');
const clock        = $('clock');
const connStatus   = $('connStatus');
const sysDot       = $('sysDot');
const sysText      = $('sysText');
const statRequests = $('statRequests');
const statLatency  = $('statLatency');
const statSource   = $('statSource');
const statUptime   = $('statUptime');
const waveCanvas   = $('waveCanvas');

/* ===== VOLUME SEGMENTS INIT ===== */
const VOL_SEGMENTS = 40;
for (let i = 0; i < VOL_SEGMENTS; i++) {
    const seg = document.createElement('div');
    seg.className = 'vol-seg';
    volMeter.appendChild(seg);
}

/* ===== WAVEFORM (canvas animation — driven by real volume) ===== */
const ctx = waveCanvas.getContext('2d');
let wavePhase = 0;
let smoothedVol = 0;        // fast-interpolated display volume
let waveNoise = new Array(200).fill(0);  // rolling noise buffer for organic look

function resizeCanvas() {
    const rect = waveCanvas.parentElement.getBoundingClientRect();
    waveCanvas.width  = rect.width - 40;
    waveCanvas.height = 150;
}
window.addEventListener('resize', resizeCanvas);
resizeCanvas();

function drawWave() {
    const W = waveCanvas.width;
    const H = waveCanvas.height;
    const midY = H / 2;

    ctx.clearRect(0, 0, W, H);

    /* grid */
    ctx.strokeStyle = 'rgba(99,102,241,0.06)';
    ctx.lineWidth = 1;
    for (let y = 0; y < H; y += 20) {
        ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(W, y); ctx.stroke();
    }

    /* Smooth volume toward target — fast rise, slow decay */
    const target = state.volume;
    if (target > smoothedVol) {
        smoothedVol += (target - smoothedVol) * 0.3;   // fast attack
    } else {
        smoothedVol += (target - smoothedVol) * 0.08;  // slow release
    }

    wavePhase += 0.06 + smoothedVol * 0.08;  // faster phase when loud
    const amplitude = smoothedVol * (H * 0.42);

    /* Update noise buffer — shift left, add new sample on right */
    waveNoise.shift();
    waveNoise.push((Math.random() - 0.5) * 2);

    /* Pick colors based on MUTE/PLAY */
    const isMuted = state.decision === 'MUTE';
    const baseColor1 = isMuted ? '#ef4444' : '#6366f1';
    const baseColor2 = isMuted ? '#f87171' : '#8b5cf6';
    const baseColor3 = isMuted ? '#fca5a5' : '#a78bfa';
    const glowColor  = isMuted ? 'rgba(239,68,68,0.5)' : 'rgba(99,102,241,0.5)';
    const fillAlpha  = isMuted ? 0.06 : 0.08;

    /* main waveform */
    const gradient = ctx.createLinearGradient(0, 0, W, 0);
    gradient.addColorStop(0, baseColor1);
    gradient.addColorStop(0.5, baseColor2);
    gradient.addColorStop(1, baseColor3);

    ctx.strokeStyle = gradient;
    ctx.lineWidth = 2;
    ctx.shadowColor = glowColor;
    ctx.shadowBlur = 8;
    ctx.beginPath();

    for (let x = 0; x < W; x++) {
        const t = x / W;
        const noiseIdx = Math.floor(t * (waveNoise.length - 1));
        const noise = waveNoise[noiseIdx] || 0;

        /* When silent: near-flat with tiny sine. When loud: big noisy wave */
        const silentBase = Math.sin(t * 6 + wavePhase) * 2;  // barely visible wobble
        const speechWave =
            Math.sin(t * 8 + wavePhase) * 0.45 +
            Math.sin(t * 14 + wavePhase * 1.4) * 0.25 +
            Math.sin(t * 23 + wavePhase * 0.8) * 0.12 +
            noise * 0.35;                                      // noise makes it organic

        const y = midY + silentBase + speechWave * amplitude;
        if (x === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    }
    ctx.stroke();
    ctx.shadowBlur = 0;

    /* fill beneath */
    ctx.lineTo(W, midY);
    ctx.lineTo(0, midY);
    ctx.closePath();
    const safeAmp = Math.max(amplitude, 1);
    const fillGrad = ctx.createLinearGradient(0, midY - safeAmp, 0, midY + safeAmp);
    fillGrad.addColorStop(0, `rgba(${isMuted ? '239,68,68' : '99,102,241'},${fillAlpha})`);
    fillGrad.addColorStop(1, `rgba(${isMuted ? '239,68,68' : '99,102,241'},0)`);
    ctx.fillStyle = fillGrad;
    ctx.fill();

    /* mirror wave (subtle) */
    ctx.strokeStyle = isMuted ? 'rgba(239,68,68,0.2)' : 'rgba(139,92,246,0.2)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    for (let x = 0; x < W; x++) {
        const t = x / W;
        const y = midY -
            (Math.sin(t * 8 + wavePhase) * amplitude * 0.35 +
             Math.sin(t * 14 + wavePhase * 1.4) * amplitude * 0.12);
        if (x === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    }
    ctx.stroke();

    /* center line */
    ctx.strokeStyle = 'rgba(99,102,241,0.15)';
    ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(0, midY); ctx.lineTo(W, midY); ctx.stroke();

    requestAnimationFrame(drawWave);
}
drawWave();

/* ===== UI UPDATE FUNCTIONS ===== */
function updateLanguage() {
    const code = (state.detected_language || '??').toLowerCase();
    const lang = LANGUAGES[code] || { name: code, flag: '🏳️' };
    langCode.textContent = code.toUpperCase();
    langName.textContent = lang.name;
    langFlag.textContent = lang.flag;
}

function updateConfidence() {
    const pct = Math.round(state.confidence * 100);
    confVal.textContent = pct + '%';
    confFill.style.width = pct + '%';

    if (pct >= 75) {
        confFill.style.background = 'linear-gradient(90deg, #6366f1, #8b5cf6, #a78bfa)';
    } else if (pct >= 45) {
        confFill.style.background = 'linear-gradient(90deg, #eab308, #f59e0b)';
    } else {
        confFill.style.background = 'linear-gradient(90deg, #ef4444, #f87171)';
    }
}

function updateStatus() {
    const isPlay = state.decision === 'PLAY';
    statusCard.className = 'card card-status ' + (isPlay ? 'play' : 'mute');
    statusIcon.textContent = isPlay ? '✔' : '✖';
    statusText.textContent = isPlay ? 'PLAY' : 'MUTE';
    statusSub.textContent  = isPlay ? 'Audio playing' : 'Audio muted';
}

function updateVolume() {
    const pct = Math.round(state.volume * 100);
    volVal.textContent = pct + '%';
    const segs = volMeter.children;
    const activeCount = Math.round(state.volume * VOL_SEGMENTS);

    for (let i = 0; i < VOL_SEGMENTS; i++) {
        const ratio = i / VOL_SEGMENTS;
        const isActive = i < activeCount;
        const height = isActive ? (8 + ratio * 24) : 4;
        let bg;
        if (!isActive) {
            bg = 'rgba(255,255,255,0.04)';
        } else if (ratio < 0.5) {
            bg = `hsl(${140 + ratio * 60}, 80%, 55%)`;
        } else if (ratio < 0.8) {
            bg = `hsl(${200 - (ratio - 0.5) * 300}, 80%, 55%)`;
        } else {
            bg = `hsl(0, 80%, 58%)`;
        }
        segs[i].style.height = height + 'px';
        segs[i].style.background = bg;
        segs[i].style.boxShadow = isActive ? `0 0 6px ${bg}` : 'none';
    }
}

function updateHistory() {
    histTimeline.innerHTML = '';
    state.history.forEach((entry, i) => {
        const chip = document.createElement('span');
        const code = (typeof entry === 'string' ? entry : entry.lang).toLowerCase();
        const decision = typeof entry === 'string' ? null : entry.decision;
        const cls = LANGUAGES[code] ? code : 'default';
        chip.className = `hist-chip ${cls}`;
        const lang = LANGUAGES[code] || { flag: '🏳️' };

        /* Show a small PLAY/MUTE dot before the language code */
        const dot = decision === 'PLAY' ? '🟢' : decision === 'MUTE' ? '🔴' : '';
        chip.textContent = `${dot} ${code.toUpperCase()}`;
        chip.title = `${lang.flag} ${(LANGUAGES[code] || {}).name || code}` +
                     (decision ? ` — ${decision}` : '');
        chip.style.animationDelay = (i * 0.04) + 's';
        histTimeline.appendChild(chip);
    });
    histCount.textContent = state.history.length + ' / 10';
}

function updateClock() {
    const now = new Date();
    clock.textContent = now.toLocaleTimeString('en-US', { hour12: false });
}

function updateConnectionStatus() {
    if (state.apiConnected) {
        connStatus.className = 'system-status';
        sysText.textContent = 'API Connected';
        statSource.textContent = 'API';
        statSource.className = 'stat-val api';
    } else {
        connStatus.className = 'system-status disconnected';
        sysText.textContent = 'API Disconnected';
        statSource.textContent = 'OFFLINE';
        statSource.className = 'stat-val offline';
    }

    statRequests.textContent = state.requestCount.toLocaleString();

    if (state.latencies.length > 0) {
        const avg = state.latencies.reduce((a, b) => a + b, 0) / state.latencies.length;
        statLatency.textContent = avg.toFixed(1) + 'ms';
    }

    const elapsed = Math.floor((Date.now() - state.startTime) / 1000);
    const mm = String(Math.floor(elapsed / 60)).padStart(2, '0');
    const ss = String(elapsed % 60).padStart(2, '0');
    statUptime.textContent = mm + ':' + ss;
}

/* ===== RENDER ALL ===== */
function renderAll() {
    updateLanguage();
    updateConfidence();
    updateStatus();
    updateVolume();
    updateHistory();
    updateClock();
    updateConnectionStatus();
}

/* ===== API FETCH ===== */
async function fetchStatus() {
    if (statusFetchInFlight) return;
    statusFetchInFlight = true;
    const t0 = performance.now();

    try {
        const res = await fetch(API_STATUS_URL, { signal: AbortSignal.timeout(FETCH_TIMEOUT_MS) });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);

        const data = await res.json();
        const latency = performance.now() - t0;

        /* Map API fields → state */
        state.detected_language = (data.language || 'unknown').toLowerCase();
        state.raw_language = (data.raw_language || 'unknown').toLowerCase();
        state.confidence = typeof data.confidence === 'number'
            ? Math.max(0, Math.min(1, data.confidence))
            : 0;
        state.decision = data.decision === 'PLAY' ? 'PLAY' : 'MUTE';

        /* Use real volume from backend if available */
        state.volume = typeof data.volume_rms === 'number' ? data.volume_rms : 0;

        /* Sync target language display from backend */
        if (data.target_language && data.target_language !== state.target) {
            state.target = data.target_language;
            targetLang.value = data.target_language;
        }

        state.apiConnected = true;
        state.requestCount++;
        state.latencies.push(latency);
        if (state.latencies.length > 50) state.latencies.shift();  // rolling window

        state.history.push({
            lang: state.detected_language,
            decision: state.decision,
        });
        if (state.history.length > 10) state.history.shift();

    } catch (err) {
        /* API unreachable: stop guessing; show a safe disconnected state. */
        state.apiConnected = false;
        state.detected_language = 'unknown';
        state.raw_language = 'unknown';
        state.confidence = 0;
        state.decision = 'MUTE';
        state.volume = 0;

    }

    statusFetchInFlight = false;
    renderAll();
}

/* ===== SET TARGET LANGUAGE (POST to backend) ===== */
async function setTargetLanguage(langCode) {
    state.target = langCode;
    try {
        await fetch(API_SET_TARGET_URL, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ language: langCode }),
            signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
        });
        console.log(`Target language set to: ${langCode}`);
    } catch (err) {
        console.warn('Could not set target language on backend:', err.message);
    }
}

/* ===== FETCH INITIAL CONFIG FROM BACKEND ===== */
async function fetchConfig() {
    try {
        const res = await fetch(API_CONFIG_URL, { signal: AbortSignal.timeout(FETCH_TIMEOUT_MS) });
        if (!res.ok) return;
        const cfg = await res.json();
        if (cfg.target_language) {
            state.target = cfg.target_language;
            targetLang.value = cfg.target_language;
        }
    } catch (err) {
        /* Backend not available yet — that's fine */
    }
}

/* ===== INIT ===== */
renderAll();
fetchConfig();

targetLang.addEventListener('change', () => {
    setTargetLanguage(targetLang.value);
});

/* Start polling */
setInterval(fetchStatus, POLL_INTERVAL);
setInterval(updateClock, 1000);

/* First fetch immediately */
fetchStatus();
