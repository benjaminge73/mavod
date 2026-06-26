#!/bin/bash
cd "$(dirname "$0")"
uv run streamlit run main.py --server.port=8501 --server.address=0.0.0.0 --server.headless=true
