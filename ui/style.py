"""Custom CSS — mobile-first dark theme for Torrent Preference Annotator."""

CUSTOM_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap');

/* ─── Design tokens ─────────────────────────────────────────── */
:root {
    --bg-base:        #0d0f14;
    --bg-surface:     #13161f;
    --bg-elevated:    #1a1f2e;
    --bg-hover:       #212740;

    --border:         #252c42;
    --border-subtle:  #1a1f2e;

    --text-primary:   #eef0f8;
    --text-secondary: #8892aa;
    --text-muted:     #4a5268;

    --accent:         #3b82f6;
    --accent-dim:     rgba(59, 130, 246, 0.12);
    --accent-border:  rgba(59, 130, 246, 0.3);
    --accent-hover:   #2563eb;

    --green:          #22c55e;
    --green-dim:      rgba(34, 197, 94, 0.1);
    --orange:         #f59e0b;
    --orange-dim:     rgba(245, 158, 11, 0.1);
    --red:            #ef4444;

    --radius-sm:  6px;
    --radius-md:  10px;
    --radius-lg:  14px;
    --radius-xl:  18px;
}

/* ─── Global base ───────────────────────────────────────────── */
html, body,
[data-testid="stAppViewContainer"],
[data-testid="stApp"] {
    background-color: var(--bg-base) !important;
    color: var(--text-primary) !important;
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif !important;
    -webkit-font-smoothing: antialiased !important;
}

/* Hide Streamlit chrome */
#MainMenu, footer,
header[data-testid="stHeader"],
[data-testid="stDecoration"],
[data-testid="stToolbar"],
[data-testid="stStatusWidget"] {
    display: none !important;
}

/* ─── Hide sidebar entirely ──────────────────────────────────── */
[data-testid="stSidebar"],
[data-testid="collapsedControl"] {
    display: none !important;
}

/* ─── Top bar ───────────────────────────────────────────────── */
.topbar {
    display: flex;
    align-items: center;
    padding: 0.55rem 0 0.5rem;
    margin-bottom: 0.5rem;
    border-bottom: 1px solid var(--border);
}
.brand-inline {
    display: flex;
    align-items: center;
    gap: 0.5rem;
}
.brand-dot {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: var(--accent);
    flex-shrink: 0;
}
.brand-text {
    font-size: 0.8rem;
    font-weight: 700;
    color: var(--text-primary);
    letter-spacing: -0.01em;
}

/* ─── Streamlit widgets cleanup ─────────────────────────────── */
/* Radio */
[data-testid="stRadio"] > label { display: none !important; }
[data-testid="stRadio"] [data-baseweb="radio"] span:last-child {
    font-size: 0.8rem !important;
    color: var(--text-secondary) !important;
}
[data-testid="stRadio"] [data-baseweb="radio"][aria-checked="true"] span:last-child {
    color: var(--text-primary) !important;
    font-weight: 600 !important;
}

/* Selectbox */
[data-testid="stSelectbox"] > label { display: none !important; }
[data-testid="stSelectbox"] [data-baseweb="select"] > div:first-child {
    background: var(--bg-elevated) !important;
    border: 1px solid var(--border) !important;
    border-radius: var(--radius-sm) !important;
    padding: 0.45rem 0.7rem !important;
    display: flex !important;
    align-items: center !important;
}
[data-testid="stSelectbox"] [data-baseweb="select"] span {
    font-size: 0.82rem !important;
    color: var(--text-primary) !important;
    line-height: 1.2 !important;
}
[data-testid="stSelectbox"] svg { color: var(--text-muted) !important; }

/* Selectbox dropdown */
[data-baseweb="popover"] {
    background: var(--bg-elevated) !important;
    border: 1px solid var(--border) !important;
    border-radius: var(--radius-md) !important;
}
[data-baseweb="option"] {
    background: var(--bg-elevated) !important;
    color: var(--text-secondary) !important;
    font-size: 0.82rem !important;
}
[data-baseweb="option"]:hover,
[data-baseweb="option"][aria-selected="true"] {
    background: var(--bg-hover) !important;
    color: var(--text-primary) !important;
}

/* Text input */
[data-testid="stTextInput"] > label {
    font-size: 0.72rem !important;
    color: var(--text-muted) !important;
    font-weight: 500 !important;
    margin-bottom: 0.3rem !important;
}
[data-testid="stTextInput"] input {
    background: var(--bg-surface) !important;
    border: 1px solid var(--border) !important;
    border-radius: var(--radius-sm) !important;
    color: var(--text-primary) !important;
    font-size: 0.83rem !important;
    padding: 0.5rem 0.75rem !important;
    transition: border-color 0.15s, box-shadow 0.15s !important;
}
[data-testid="stTextInput"] input:focus {
    border-color: var(--accent) !important;
    box-shadow: 0 0 0 3px rgba(59,130,246,0.15) !important;
    outline: none !important;
}
[data-testid="stTextInput"] input::placeholder {
    color: var(--text-muted) !important;
    font-size: 0.8rem !important;
}

/* Expander */
[data-testid="stExpander"] {
    background: var(--bg-surface) !important;
    border: 1px solid var(--border) !important;
    border-radius: var(--radius-md) !important;
    overflow: hidden !important;
}
[data-testid="stExpander"] summary {
    color: var(--text-secondary) !important;
    font-size: 0.83rem !important;
    font-weight: 500 !important;
    padding: 0.7rem 0.9rem !important;
    background: transparent !important;
}
[data-testid="stExpander"] summary:hover {
    color: var(--text-primary) !important;
    background: var(--bg-elevated) !important;
}
[data-testid="stExpander"] summary svg { color: var(--text-muted) !important; }

/* Buttons — base */
.stButton > button {
    background: var(--bg-elevated) !important;
    border: 1px solid var(--border) !important;
    color: var(--text-secondary) !important;
    border-radius: var(--radius-sm) !important;
    font-size: 0.67rem !important;
    font-weight: 600 !important;
    padding: 0.22rem 0.3rem !important;
    line-height: 1.2 !important;
    min-height: unset !important;
    height: auto !important;
    letter-spacing: 0.01em !important;
    transition: all 0.15s ease !important;
    font-family: 'Inter', sans-serif !important;
    white-space: nowrap !important;
    overflow: hidden !important;
    text-overflow: ellipsis !important;
}
.stButton > button:hover {
    background: var(--bg-hover) !important;
    border-color: rgba(59,130,246,0.4) !important;
    color: var(--text-primary) !important;
}
.stButton > button:active { transform: translateY(0) !important; }

/* Active state (primary) — used for filter + sort chips */
.stButton > button[kind="primary"] {
    background: var(--accent) !important;
    border-color: var(--accent) !important;
    color: #fff !important;
    box-shadow: 0 2px 8px rgba(59,130,246,0.28) !important;
}
.stButton > button[kind="primary"]:hover {
    background: var(--accent-hover) !important;
    border-color: var(--accent-hover) !important;
    color: #fff !important;
    transform: none !important;
}

/* Action buttons — Sélectionner / Télécharger / Désélectionner */
.stButton > button[kind="secondary"] {
    background: var(--bg-elevated) !important;
    border: 1px solid var(--border) !important;
    color: var(--text-secondary) !important;
}
.stButton > button[kind="secondary"]:hover {
    background: var(--accent) !important;
    border-color: var(--accent) !important;
    color: #fff !important;
    box-shadow: 0 3px 10px rgba(59,130,246,0.25) !important;
}

/* ─── Main area ─────────────────────────────────────────────── */
[data-testid="stMain"] {
    background-color: var(--bg-base) !important;
}
[data-testid="stMainBlockContainer"] {
    padding: 1.25rem 1.25rem 3rem !important;
    max-width: 720px !important;
    margin: 0 auto !important;
}

/* ─── Empty state ────────────────────────────────────────────── */
.empty-state {
    text-align: center;
    padding: 3rem 1rem;
    color: var(--text-muted);
}
.empty-icon {
    font-size: 2rem;
    margin-bottom: 0.75rem;
    color: var(--border);
}
.empty-title {
    font-size: 1rem;
    font-weight: 600;
    color: var(--text-secondary);
    margin-bottom: 0.35rem;
}
.empty-sub {
    font-size: 0.78rem;
    color: var(--text-muted);
}

/* ─── Page header ───────────────────────────────────────────── */
.page-header { margin-bottom: 1.25rem; }

.query-eyebrow {
    font-size: 0.62rem;
    font-weight: 700;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    color: var(--text-muted);
    margin-bottom: 0.25rem;
}
.query-title {
    font-size: 1.15rem;
    font-weight: 700;
    color: var(--text-primary);
    letter-spacing: -0.025em;
    line-height: 1.3;
    margin-bottom: 0.3rem;
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 0.5rem;
}
.status-badge {
    display: inline-flex;
    align-items: center;
    gap: 0.3rem;
    padding: 0.15rem 0.55rem;
    border-radius: 999px;
    font-size: 0.62rem;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    border: 1px solid;
    vertical-align: middle;
}
.status-badge.pending  { background: var(--orange-dim); border-color: rgba(245,158,11,0.4); color: var(--orange); }
.status-badge.done     { background: var(--green-dim);  border-color: rgba(34,197,94,0.4);  color: var(--green);  }
.status-badge .dot     { width: 4px; height: 4px; border-radius: 50%; background: currentColor; }

.query-id {
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.68rem;
    color: var(--text-muted);
    margin-top: 0.1rem;
}

/* ─── LLM banner ────────────────────────────────────────────── */
.llm-banner {
    display: flex;
    align-items: flex-start;
    gap: 0.75rem;
    background: var(--accent-dim);
    border: 1px solid var(--accent-border);
    border-left: 3px solid var(--accent);
    border-radius: var(--radius-md);
    padding: 0.85rem 1rem;
    margin-bottom: 0.75rem;
}
.llm-banner-icon {
    width: 28px;
    height: 28px;
    border-radius: 7px;
    background: rgba(59,130,246,0.2);
    border: 1px solid var(--accent-border);
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 0.85rem;
    flex-shrink: 0;
    margin-top: 0.05rem;
}
.llm-banner-label {
    font-size: 0.62rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: #6ea8fb;
    margin-bottom: 0.25rem;
}
.llm-banner-text {
    font-size: 0.845rem;
    font-weight: 600;
    color: #93c4fd;
    line-height: 1.45;
    word-break: break-word;
}

/* ─── Reasoning panel ───────────────────────────────────────── */
.reasoning-body {
    font-size: 0.8rem;
    color: var(--text-secondary);
    line-height: 1.75;
    white-space: pre-line;
    padding: 0.25rem 0 0.5rem;
}
.reasoning-label {
    font-size: 0.62rem;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--text-muted);
    font-weight: 700;
    margin-top: 0.4rem;
}
.reasoning-empty {
    color: var(--text-muted) !important;
    font-style: italic;
}

/* ─── Section divider heading ───────────────────────────────── */
.section-head {
    display: flex;
    align-items: center;
    gap: 0.6rem;
    font-size: 0.6rem;
    font-weight: 700;
    letter-spacing: 0.13em;
    text-transform: uppercase;
    color: var(--text-muted);
    margin: 1.4rem 0 0.7rem;
}
.section-head::after {
    content: '';
    flex: 1;
    height: 1px;
    background: var(--border-subtle);
    background: var(--border);
    opacity: 0.4;
}

/* ─── Selection confirmation ─────────────────────────────────── */
.confirm-banner {
    display: flex;
    align-items: center;
    gap: 0.55rem;
    background: var(--green-dim);
    border: 1px solid rgba(34,197,94,0.3);
    border-radius: var(--radius-md);
    padding: 0.6rem 0.9rem;
    font-size: 0.8rem;
    font-weight: 600;
    color: var(--green);
    margin-bottom: 0.85rem;
}
.confirm-icon {
    width: 18px;
    height: 18px;
    border-radius: 50%;
    background: rgba(34,197,94,0.2);
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 0.7rem;
    flex-shrink: 0;
}

/* ─── Torrent card ───────────────────────────────────────────── */
.torrent-card {
    background: var(--bg-surface);
    border: 1px solid var(--border);
    border-radius: var(--radius-lg);
    padding: 0.875rem 1rem;
    margin-bottom: 0.55rem;
    position: relative;
    overflow: hidden;
    transition: border-color 0.15s, background 0.15s, box-shadow 0.15s;
}
.torrent-card:hover {
    border-color: rgba(59,130,246,0.25);
    background: var(--bg-elevated);
}
.torrent-card.is-llm {
    border-color: var(--accent-border);
    background: linear-gradient(135deg, var(--accent-dim) 0%, var(--bg-surface) 100%);
}
.torrent-card.is-llm::before {
    content: '';
    position: absolute;
    top: 0; left: 0; bottom: 0;
    width: 3px;
    background: linear-gradient(to bottom, var(--accent), rgba(59,130,246,0.3));
    border-radius: 0;
}
.torrent-card.is-selected {
    border-color: rgba(34,197,94,0.35);
    background: linear-gradient(135deg, var(--green-dim) 0%, var(--bg-surface) 100%);
}
.torrent-card.is-selected::before {
    content: '';
    position: absolute;
    top: 0; left: 0; bottom: 0;
    width: 3px;
    background: var(--green);
}

.card-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 0.45rem;
}
.card-rank {
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.68rem;
    font-weight: 500;
    color: var(--text-muted);
    display: flex;
    align-items: center;
    gap: 0.4rem;
}
.tag {
    display: inline-flex;
    align-items: center;
    gap: 0.2rem;
    padding: 0.1rem 0.4rem;
    border-radius: 4px;
    font-size: 0.58rem;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
}
.tag.llm-tag      { background: var(--accent);            color: #fff; }
.tag.selected-tag { background: var(--green);             color: #fff; }

.card-name {
    font-size: 0.83rem;
    font-weight: 500;
    color: var(--text-primary);
    line-height: 1.5;
    margin-bottom: 0.6rem;
    word-break: break-all;
}
.torrent-card.is-llm .card-name {
    color: #93c4fd;
    font-weight: 600;
}
.torrent-card.is-selected .card-name {
    color: #86efac;
}

.card-meta {
    display: flex;
    align-items: center;
    gap: 0.4rem;
    flex-wrap: wrap;
}
.chip {
    display: inline-flex;
    align-items: center;
    gap: 0.3rem;
    padding: 0.22rem 0.55rem;
    border-radius: 5px;
    background: var(--bg-elevated);
    border: 1px solid var(--border);
    font-size: 0.7rem;
    color: var(--text-secondary);
    font-weight: 500;
    white-space: nowrap;
}
.torrent-card.is-llm .chip     { background: rgba(59,130,246,0.08); border-color: var(--accent-border); }
.torrent-card.is-selected .chip { background: rgba(34,197,94,0.08); border-color: rgba(34,197,94,0.25); }
.chip .cdot {
    width: 5px; height: 5px;
    border-radius: 50%;
    flex-shrink: 0;
}
.chip.seeds-chip .cdot { background: var(--green); }
.chip.seeds-chip.mid   .cdot { background: var(--orange); }
.chip.seeds-chip.low   .cdot { background: var(--red); }
.chip.seeds-chip { font-weight: 600; }
.chip.seeds-chip.hi  { color: var(--green); }
.chip.seeds-chip.mid { color: var(--orange); }
.chip.seeds-chip.low { color: var(--red); }

/* ─── Scrollbar ────────────────────────────────────────────── */
::-webkit-scrollbar { width: 4px; height: 4px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: var(--border); border-radius: 99px; }
::-webkit-scrollbar-thumb:hover { background: var(--text-muted); }

/* ─── Column gap fix ───────────────────────────────────────── */
[data-testid="stHorizontalBlock"] { gap: 0.25rem !important; align-items: center !important; }
[data-testid="stHorizontalBlock"] [data-testid="stVerticalBlockBorderWrapper"] { margin-bottom: 0 !important; }
[data-testid="stHorizontalBlock"] .stButton { margin-bottom: 0 !important; }
/* Remove Streamlit's default block spacing around button rows */
[data-testid="stHorizontalBlock"] + div[data-testid="stVerticalBlock"] { margin-top: 0 !important; }

/* ─── Responsive ───────────────────────────────────────────── */
@media (max-width: 640px) {
    [data-testid="stMainBlockContainer"] { padding: 0.75rem 0.65rem 2rem !important; }
    .torrent-card { padding: 0.7rem 0.75rem; }
    .llm-banner   { padding: 0.7rem 0.8rem; gap: 0.55rem; }
    .query-title  { font-size: 0.95rem; }
    /* Filter chips: smaller font on mobile */
    .stButton > button {
        font-size: 0.62rem !important;
        padding: 0.2rem 0.25rem !important;
    }
}

/* Sort chips row: allow horizontal scroll on very small screens */
@media (max-width: 420px) {
    /* Streamlit renders columns in a flex row — allow overflow scroll */
    [data-testid="stHorizontalBlock"]:has([data-testid="stButton"]) {
        overflow-x: auto !important;
        flex-wrap: nowrap !important;
        -webkit-overflow-scrolling: touch !important;
        padding-bottom: 2px !important;
    }
    [data-testid="stHorizontalBlock"]:has([data-testid="stButton"]) > div {
        flex-shrink: 0 !important;
        min-width: 52px !important;
    }
}
</style>
"""
