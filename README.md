# JagX Coder

**Extra-Powerful Autonomous Coding Agent** powered by [JagX AI](https://jagx-ai-v2.onrender.com)

[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

JagX Coder is a ReAct-style coding agent that can **read, write, run, and debug code** by itself using the JagX AI API.

---

## Features

- **ReAct agent loop** — Think → Act → Observe → repeat
- **Full coding toolset**
  - `list_dir` / `read_file` / `write_file` / `append_file`
  - `run_shell` (with safety checks)
  - `run_python` (execute code snippets)
  - `search_code` (grep-like search)
- Workspace isolation (agent cannot escape the project folder)
- Conversation memory across turns
- Strong system prompt optimized for real coding work
- Interactive CLI + one-shot mode
- Clean `.env` configuration (no secrets in code)

---

## Quick Start

### 1. Clone & install

```bash
git clone https://github.com/YOUR_USERNAME/jagx-coder.git
cd jagx-coder
pip install -r requirements.txt
