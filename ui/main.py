"""
mavod-ui — Read-only viewer for DeepSeek torrent selections.

Shows for each historical search:
  - The torrent picked by DeepSeek + its reasoning
  - The full ranked candidate list
  - A button to push an alternative candidate to qBittorrent
"""

import streamlit as st
from data import (
    get_all_searches,
    display_label,
    download_torrent,
    invalidate_cache,
)
from style import CUSTOM_CSS

st.set_page_config(
    page_title="mavod-ui",
    page_icon="🎬",
    layout="centered",
    initial_sidebar_state="collapsed",
)

st.markdown(CUSTOM_CSS, unsafe_allow_html=True)

if "selected_search" not in st.session_state:
    st.session_state.selected_search = None

all_searches = get_all_searches()
search_list = list(all_searches.keys())

st.markdown(
    '<div class="topbar">'
    '  <div class="brand-inline">'
    '    <span class="brand-dot"></span>'
    '    <span class="brand-text">mavod-ui</span>'
    '  </div>'
    '</div>',
    unsafe_allow_html=True,
)

if not search_list:
    st.markdown(
        '<div class="empty-state">'
        '  <div class="empty-icon">—</div>'
        '  <div class="empty-title">Aucune recherche</div>'
        '  <div class="empty-sub">Aucun result.json trouvé sous torrents/.</div>'
        '</div>',
        unsafe_allow_html=True,
    )
    st.stop()

if st.session_state.selected_search not in search_list:
    # Deep-link : si ?search_id=… est fourni et correspond à une recherche
    # connue, on l'utilise comme sélection initiale. Sinon, on retombe sur
    # la recherche la plus récente.
    requested = st.query_params.get("search_id")
    if isinstance(requested, list):
        requested = requested[0] if requested else None
    if requested and requested in search_list:
        st.session_state.selected_search = requested
    else:
        st.session_state.selected_search = search_list[0]

selected = st.selectbox(
    label="Recherche",
    options=search_list,
    index=search_list.index(st.session_state.selected_search),
    format_func=display_label,
    label_visibility="collapsed",
    key="search_selector",
)
st.session_state.selected_search = selected

search_data = all_searches.get(st.session_state.selected_search, {})
if not search_data:
    st.stop()

title = search_data["title"]
year = search_data["year"]
search_id = search_data["id"]
llm_choice = search_data["llm_choice"]
torrents = search_data["torrents"]

year_html = f' <span class="year-muted">({year})</span>' if year else ""
st.markdown(
    f'<div class="page-header">'
    f'  <div class="query-title">{title}{year_html}</div>'
    f'  <div class="query-id">ID: {search_id}</div>'
    f'</div>',
    unsafe_allow_html=True,
)

llm_rank = llm_choice["rank"]
llm_name = llm_choice["name"]
llm_rank_label = f"#{llm_rank}" if llm_rank is not None else "—"
st.markdown(
    f'<div class="llm-banner">'
    f'  <div class="llm-banner-icon">AI</div>'
    f'  <div class="llm-banner-body">'
    f'    <div class="llm-banner-label">Choix DeepSeek &mdash; {llm_rank_label}</div>'
    f'    <div class="llm-banner-text">{llm_name}</div>'
    f'  </div>'
    f'</div>',
    unsafe_allow_html=True,
)

with st.expander("Voir le raisonnement LLM", expanded=False):
    reasoning_content = llm_choice.get("reasoning_content") or ""
    conclusion = llm_choice.get("reasoning") or ""
    st.markdown('<div class="reasoning-label">Raisonnement interne</div>', unsafe_allow_html=True)
    if reasoning_content:
        st.markdown(
            f'<div class="reasoning-body">{reasoning_content}</div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            '<div class="reasoning-body reasoning-empty">Non disponible '
            '(recherche antérieure à la persistance du raisonnement).</div>',
            unsafe_allow_html=True,
        )
    st.markdown('<div class="reasoning-label">Conclusion</div>', unsafe_allow_html=True)
    st.markdown(
        f'<div class="reasoning-body">{conclusion or "Aucune conclusion disponible."}</div>',
        unsafe_allow_html=True,
    )

sort_key = f"sort_{search_id}"
if sort_key not in st.session_state:
    st.session_state[sort_key] = "seeds_desc"

SORT_OPTIONS = [
    ("seeds_desc", "Seeds ↓"),
    ("seeds_asc",  "Seeds ↑"),
    ("size_asc",   "Taille ↑"),
    ("size_desc",  "Taille ↓"),
    ("rank",       "Rang"),
]

st.markdown('<div class="section-head">Torrents</div>', unsafe_allow_html=True)

sort_cols = st.columns(len(SORT_OPTIONS), gap="small")
for i, (val, label) in enumerate(SORT_OPTIONS):
    with sort_cols[i]:
        active = st.session_state[sort_key] == val
        if st.button(
            label,
            key=f"sort_chip_{search_id}_{val}",
            use_container_width=True,
            type="primary" if active else "secondary",
        ):
            st.session_state[sort_key] = val
            st.rerun()


def _parse_size(s: str) -> float:
    """Parse `"8.20 GB"` ou `"450 MB"` en float GB (utilisé pour le tri par taille)."""
    try:
        parts = s.split()
        val = float(parts[0])
        unit = parts[1].upper() if len(parts) > 1 else "GB"
        return val / 1024 if unit == "MB" else val
    except Exception:
        return 0.0


sort_opt = st.session_state[sort_key]
sorted_torrents = list(torrents)
if sort_opt == "seeds_desc":
    sorted_torrents.sort(key=lambda t: t.get("seeds", 0), reverse=True)
elif sort_opt == "seeds_asc":
    sorted_torrents.sort(key=lambda t: t.get("seeds", 0))
elif sort_opt == "size_asc":
    sorted_torrents.sort(key=lambda t: _parse_size(t.get("size", "0 GB")))
elif sort_opt == "size_desc":
    sorted_torrents.sort(key=lambda t: _parse_size(t.get("size", "0 GB")), reverse=True)
else:
    sorted_torrents.sort(key=lambda t: t.get("rank", 99))

for torrent in sorted_torrents:
    rank = torrent.get("rank", "?")
    name = torrent.get("name", "Unknown")
    size = torrent.get("size", "—")
    seeds = torrent.get("seeds", 0)
    is_llm = torrent.get("llm", False)

    seed_cls = "hi" if seeds >= 80 else ("mid" if seeds >= 25 else "low")

    card_cls = "torrent-card"
    if is_llm:
        card_cls += " is-llm"

    tags_html = '<span class="tag llm-tag">Choisi par DeepSeek</span>' if is_llm else ""

    st.markdown(
        f'<div class="{card_cls}">'
        f'  <div class="card-header">'
        f'    <span class="card-rank">#{rank}</span>'
        f'    <span class="card-tags">{tags_html}</span>'
        f'  </div>'
        f'  <div class="card-name">{name}</div>'
        f'  <div class="card-meta">'
        f'    <span class="chip">'
        f'      <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">'
        f'        <rect x="2" y="3" width="20" height="14" rx="2"/>'
        f'        <path d="M8 21h8M12 17v4"/>'
        f'      </svg>'
        f'      {size}'
        f'    </span>'
        f'    <span class="chip seeds-chip {seed_cls}">'
        f'      <span class="cdot"></span>'
        f'      {seeds} seeds'
        f'    </span>'
        f'  </div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    if is_llm:
        continue

    has_source = bool(torrent.get("dl") or torrent.get("magnet") or torrent.get("url"))
    dl_status_key = f"dl_status_{search_id}_{rank}"

    # Bouton toujours cliquable : un clic produit TOUJOURS un retour (succès,
    # absence de source, ou erreur réelle) plutôt qu'un bouton désactivé muet.
    if not has_source:
        st.caption("Aucune source enregistrée pour ce candidat — le clic expliquera pourquoi.")

    if st.button(
        "Télécharger ce torrent à la place",
        key=f"dl_{search_id}_{rank}",
        use_container_width=False,
    ):
        with st.spinner("Envoi à qBittorrent…"):
            try:
                torrent_hash = download_torrent(search_id=search_id, torrent_id=rank)
                invalidate_cache()
                st.session_state[dl_status_key] = (
                    "success",
                    f"✅ Torrent #{rank} envoyé à qBittorrent ({torrent_hash[:8]}…)",
                )
            except FileNotFoundError as exc:
                st.session_state[dl_status_key] = (
                    "warning", f"⚠️ Torrent #{rank} non envoyé : {exc}",
                )
            except Exception as exc:
                # Message d'échec symétrique du succès (bandeau rouge ↔ vert),
                # avec la raison réelle remontée par qBittorrent / la résolution.
                st.session_state[dl_status_key] = (
                    "error", f"❌ Échec de l'envoi du torrent #{rank} : {exc}",
                )

    status = st.session_state.get(dl_status_key)
    if status:
        level, msg = status
        if level == "success":
            st.success(msg)
        elif level == "warning":
            st.warning(msg)
        else:
            st.error(msg)
