# JagX Coder — Extra-Powerful Autonomous Coding Agent

Powered by **JagX AI** (`https://jagx-ai-v2.onrender.com`)

## Features

- **ReAct agent loop** — Think → Act → Observe → repeat
- **Full coding toolset**
  - `list_dir` / `read_file` / `write_file` / `append_file`
  - `run_shell` (safe)
  - `run_python` (execute snippets)
  - `search_code` (grep-like)
- Project-aware workspace isolation
- Conversation memory
- Strong system prompt optimized for real coding work
- CLI + one-shot modes

## Quick Start

```bash
# 1. Install dependency
pip install -r requirements.txt

# 2. (Optional) set your own key / workspace
export JAGX_API_KEY="jagx-adb6112fe4192539858e02fae18053d1"
export JAGX_BASE_URL="https://jagx-ai-v2.onrender.com"
export JAGX_WORKSPACE="."          # default = current directory

# 3. Interactive mode
python jagx_coder.py

# 4. One-shot mode
python jagx_coder.py "Create a FastAPI hello-world app with a /health endpoint"
```

## Example Tasks

```text
Create a clean Python CLI todo app with add/list/done commands and JSON storage
```

```text
Refactor the file main.py — extract helpers, add type hints, and write tests
```

```text
Build a simple Flask blog with SQLite, create/read posts, and a nice HTML template
```

```text
Debug why this function is returning None and fix it
```

## Safety

- All file operations are restricted to the `JAGX_WORKSPACE` directory
- Extremely dangerous shell patterns are blocked
- Agent prefers read → plan → write → test cycles

## Who made this?

Agent personality is **JagX AI by JagX & JRILICENSE** (as required by the API).

Enjoy shipping code at high speed 🚀
