"""Drift guard: ensure ui/main.py only imports symbols that exist in ui/data.py.

Regression test for the crash where PR #18 removed DPO-era functions from
ui/data.py but ui/main.py still imported them, leading to a runtime
ImportError when streamlit loaded the page.
"""

from __future__ import annotations

import ast
import importlib.util
import py_compile
from pathlib import Path

import pytest

UI_DIR = Path(__file__).resolve().parent.parent / "ui"
MAIN_PY = UI_DIR / "main.py"
DATA_PY = UI_DIR / "data.py"


@pytest.mark.unit
def test_main_py_compiles():
    """ui/main.py must be syntactically valid Python."""
    py_compile.compile(str(MAIN_PY), doraise=True)


@pytest.mark.unit
def test_main_imports_resolve_in_data():
    """Every name imported from `data` in main.py must exist as an attribute."""
    tree = ast.parse(MAIN_PY.read_text(encoding="utf-8"))
    imported_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "data":
            for alias in node.names:
                imported_names.add(alias.name)

    assert imported_names, "main.py should import something from data"

    spec = importlib.util.spec_from_file_location("mavod_ui_data_drift", DATA_PY)
    data_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(data_module)

    missing = sorted(n for n in imported_names if not hasattr(data_module, n))
    assert not missing, (
        f"main.py imports symbols that do not exist in ui/data.py: {missing}"
    )


@pytest.mark.unit
def test_main_uses_only_supported_data_api():
    """Lock the surface area used by main.py to the documented public API."""
    tree = ast.parse(MAIN_PY.read_text(encoding="utf-8"))
    imported_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "data":
            for alias in node.names:
                imported_names.add(alias.name)

    allowed = {
        "get_all_searches",
        "display_label",
        "download_torrent",
        "invalidate_cache",
    }
    unexpected = imported_names - allowed
    assert not unexpected, (
        f"main.py uses non-public data API: {sorted(unexpected)}. "
        f"Update either ui/data.py or the allowed set in this test."
    )


@pytest.mark.unit
def test_main_page_title_is_mavod_ui():
    """The browser tab must show 'mavod-ui'."""
    source = MAIN_PY.read_text(encoding="utf-8")
    assert 'page_title="mavod-ui"' in source, (
        "ui/main.py must call st.set_page_config(page_title='mavod-ui')"
    )


@pytest.mark.unit
def test_main_reads_search_id_query_param():
    """Deep-linking : ui/main.py doit consommer st.query_params['search_id']
    pour ouvrir directement la recherche pointée par le bot Telegram."""
    source = MAIN_PY.read_text(encoding="utf-8")
    assert "st.query_params" in source, (
        "ui/main.py doit lire st.query_params.get('search_id') pour deep-linker."
    )
    assert "search_id" in source


@pytest.mark.unit
def test_dockerfile_ui_bundles_mavod_package():
    """Dockerfile.ui must ship mavod/ + the deps download_torrent needs."""
    root = Path(__file__).resolve().parent.parent
    dockerfile = root / "docker_configs" / "Dockerfile.ui"
    content = dockerfile.read_text(encoding="utf-8")
    assert "COPY mavod/" in content, (
        "Dockerfile.ui must COPY mavod/ — otherwise the UI container raises "
        "'No module named mavod' when the user clicks the download override button."
    )

    # Either the Dockerfile pins requests/bencodepy inline (legacy form) OR
    # they come transitively via requirements/ui.txt → requirements/base.txt.
    base_path = root / "requirements" / "base.txt"
    ui_path = root / "requirements" / "ui.txt"
    base_deps = base_path.read_text(encoding="utf-8") if base_path.exists() else ""
    ui_deps = ui_path.read_text(encoding="utf-8") if ui_path.exists() else ""
    pip_sources = content + "\n" + ui_deps + "\n" + base_deps

    assert "requests" in pip_sources, (
        "Dockerfile.ui (or its requirements/ chain) must install requests — "
        "mavod/qbittorrent_client.py depends on it."
    )
    assert "bencodepy" in pip_sources, (
        "Dockerfile.ui (or its requirements/ chain) must install bencodepy — "
        "add_torrent() resolves the infohash via bencodepy.decode()."
    )
