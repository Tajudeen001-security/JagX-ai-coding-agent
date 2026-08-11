# JagX Coder

**Extra-Powerful Autonomous Coding Agent** powered by [JagX AI](https://jagx-ai-v2.onrender.com)

[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Streamlit](https://img.shields.io/badge/Streamlit-FF4B4B?logo=streamlit&logoColor=white)](https://streamlit.io)

JagX Coder is a **ReAct-style coding agent** that can read, write, run, and debug code by itself using the JagX AI API.

It comes in two forms:
- **Website** (`app.py`) — easy browser UI anyone can use
- **CLI** (`jagx_coder.py`) — powerful terminal agent

---

## Features

### Agent capabilities
- ReAct loop (Think → Act → Observe → repeat)
- Tools: `list_dir`, `read_file`, `write_file`, `append_file`, `run_shell`, `run_python`, `search_code`
- Workspace isolation (safe file access)
- Conversation memory
- Strong coding-focused system prompt

### Web UI extras
- One-click example tasks
- Download individual files or full ZIP
- Clear chat / clear workspace buttons
- Image generation (JagX `/image` endpoint)
- Live status log while the agent works
- Dark modern UI

---

## Quick Start (Website)

```bash
git clone https://github.com/YOUR_USERNAME/jagx-coder.git
cd jagx-coder
pip install -r requirements.txt
streamlit run app.py
