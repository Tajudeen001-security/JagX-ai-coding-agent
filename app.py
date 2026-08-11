#!/usr/bin/env python3
"""
JagX Coder — Advanced Web UI
- Shared API key via Streamlit Secrets (all users can use it)
- Deep reasoning for complex & full-stack apps
- Cybersecurity-focused capabilities
"""

from __future__ import annotations

import os
import re
import sys
import json
import base64
import zipfile
import traceback
import io
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

import requests
import streamlit as st

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ──────────────────────────────────────────────────────────────
# CONFIG (Secrets → env → default)
# ──────────────────────────────────────────────────────────────
def get_secret(key: str, default: str = "") -> str:
    """Read from Streamlit secrets first, then environment, then default."""
    try:
        if key in st.secrets:
            return str(st.secrets[key]).strip()
    except Exception:
        pass
    return os.getenv(key, default).strip()

DEFAULT_BASE = "https://jagx-ai-v2.onrender.com"
MAX_TOKENS = 2200
MAX_TOOL_ROUNDS = 35
REQUEST_TIMEOUT = 150

WEB_WORKSPACE = Path(get_secret("JAGX_WORKSPACE", "./web_workspace")).resolve()
WEB_WORKSPACE.mkdir(parents=True, exist_ok=True)

HIDDEN_NAMES = {
    ".env", ".env.local", ".env.example",
    ".git", ".gitignore", ".devcontainer",
    "__pycache__", ".streamlit", "web_workspace",
    "app.py", "jagx_coder.py", "requirements.txt",
    "README.md", "LICENSE", "HOSTING.md", ".dockerignore",
}

# ──────────────────────────────────────────────────────────────
# TOOLS
# ──────────────────────────────────────────────────────────────
@dataclass
class ToolResult:
    success: bool
    output: str
    error: Optional[str] = None


def safe_path(path: str) -> Path:
    target = (WEB_WORKSPACE / path).resolve()
    if not str(target).startswith(str(WEB_WORKSPACE)):
        raise PermissionError(f"Path outside workspace is not allowed: {path}")
    return target


def tool_list_dir(path: str = ".") -> ToolResult:
    try:
        target = safe_path(path)
        if not target.exists():
            return ToolResult(False, "", f"Path does not exist: {path}")
        if target.is_file():
            return ToolResult(True, f"[FILE] {path}")
        entries = []
        for p in sorted(target.iterdir()):
            if p.name in HIDDEN_NAMES or p.name.startswith(".git"):
                continue
            kind = "DIR " if p.is_dir() else "FILE"
            size = f"{p.stat().st_size:>8}" if p.is_file() else "       -"
            entries.append(f"{kind}  {size}  {p.name}")
        return ToolResult(True, "\n".join(entries) if entries else "(empty workspace)")
    except Exception as e:
        return ToolResult(False, "", str(e))


def tool_read_file(path: str, start_line: int = 1, end_line: Optional[int] = None) -> ToolResult:
    try:
        target = safe_path(path)
        if not target.is_file():
            return ToolResult(False, "", f"Not a file: {path}")
        text = target.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        start = max(1, start_line) - 1
        end = end_line if end_line is not None else len(lines)
        selected = lines[start:end]
        numbered = [f"{i + start + 1:4d} | {line}" for i, line in enumerate(selected)]
        header = f"=== {path} (lines {start + 1}-{min(end, len(lines))}/{len(lines)}) ==="
        return ToolResult(True, header + "\n" + "\n".join(numbered))
    except Exception as e:
        return ToolResult(False, "", str(e))


def tool_write_file(path: str, content: str, overwrite: bool = True) -> ToolResult:
    try:
        target = safe_path(path)
        if target.exists() and not overwrite:
            return ToolResult(False, "", f"File exists and overwrite=False: {path}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return ToolResult(True, f"Wrote {len(content)} chars → {path}")
    except Exception as e:
        return ToolResult(False, "", str(e))


def tool_append_file(path: str, content: str) -> ToolResult:
    try:
        target = safe_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as f:
            f.write(content)
        return ToolResult(True, f"Appended {len(content)} chars → {path}")
    except Exception as e:
        return ToolResult(False, "", str(e))


def tool_run_shell(command: str, timeout: int = 60) -> ToolResult:
    import subprocess
    try:
        dangerous = [
            "rm -rf /", "mkfs", ":(){:|:&};:", "dd if=/dev/zero",
            "chmod -R 777 /", "curl | bash", "wget | sh",
        ]
        if any(d in command for d in dangerous):
            return ToolResult(False, "", "Blocked potentially destructive command")
        result = subprocess.run(
            command, shell=True, cwd=str(WEB_WORKSPACE),
            capture_output=True, text=True, timeout=timeout,
        )
        out = (result.stdout or "") + (result.stderr or "")
        if result.returncode != 0:
            return ToolResult(False, out.strip() or "(no output)", f"exit code {result.returncode}")
        return ToolResult(True, out.strip() or "(no output)")
    except subprocess.TimeoutExpired:
        return ToolResult(False, "", f"Command timed out after {timeout}s")
    except Exception as e:
        return ToolResult(False, "", str(e))


def tool_run_python(code: str) -> ToolResult:
    import subprocess, tempfile
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, dir=str(WEB_WORKSPACE)) as f:
            f.write(code)
            tmp = f.name
        result = subprocess.run(
            [sys.executable, tmp], cwd=str(WEB_WORKSPACE),
            capture_output=True, text=True, timeout=50,
        )
        Path(tmp).unlink(missing_ok=True)
        out = (result.stdout or "") + (result.stderr or "")
        if result.returncode != 0:
            return ToolResult(False, out.strip() or "(no output)", f"exit code {result.returncode}")
        return ToolResult(True, out.strip() or "(no output)")
    except Exception as e:
        return ToolResult(False, "", str(e))


def tool_search_code(query: str, path: str = ".", glob: str = "*.*") -> ToolResult:
    try:
        target = safe_path(path)
        matches = []
        for p in target.rglob(glob):
            if not p.is_file():
                continue
            if any(part in HIDDEN_NAMES for part in p.parts):
                continue
            try:
                for i, line in enumerate(p.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                    if query.lower() in line.lower():
                        rel = p.relative_to(WEB_WORKSPACE)
                        matches.append(f"{rel}:{i}: {line.strip()[:120]}")
                        if len(matches) >= 50:
                            break
            except Exception:
                continue
            if len(matches) >= 50:
                break
        if not matches:
            return ToolResult(True, f"No matches for '{query}'")
        return ToolResult(True, "\n".join(matches))
    except Exception as e:
        return ToolResult(False, "", str(e))


TOOLS = {
    "list_dir": {"fn": tool_list_dir, "desc": "List files/dirs. Args: path (default '.')"},
    "read_file": {"fn": tool_read_file, "desc": "Read file with line numbers. Args: path, start_line=1, end_line=None"},
    "write_file": {"fn": tool_write_file, "desc": "Create/overwrite a file. Args: path, content, overwrite=True"},
    "append_file": {"fn": tool_append_file, "desc": "Append text to a file. Args: path, content"},
    "run_shell": {"fn": tool_run_shell, "desc": "Run shell command in workspace. Args: command, timeout=60"},
    "run_python": {"fn": tool_run_python, "desc": "Execute Python code snippet. Args: code"},
    "search_code": {"fn": tool_search_code, "desc": "Search text in files. Args: query, path='.', glob='*.*'"},
}

# ──────────────────────────────────────────────────────────────
# DEEP REASONING SYSTEM PROMPT
# ──────────────────────────────────────────────────────────────
SYSTEM_CORE = """You are JagX Coder — an elite autonomous software engineer and cybersecurity specialist powered by JagX AI (created by JagX & JRILICENSE).

You do NOT rush. You think carefully before every action.

Your expertise covers:
1. Full-stack application development (frontend + backend + database + auth + deployment)
2. Complex multi-file projects with clean architecture
3. Cybersecurity: secure coding, vulnerability analysis, penetration-testing helpers, hardening, OWASP, auth/JWT, input validation, secrets management
4. Debugging, refactoring, testing, and documentation

=== HOW YOU MUST WORK ===

You operate in a careful ReAct loop. For EVERY step you MUST reply in EXACTLY this format:

Thought: <deep reasoning — what is the goal, what already exists, what is the best next small step, what could go wrong, security considerations>
Action: <tool_name>
Action Input: <valid JSON object>

When the entire task is truly finished:

Thought: **Summary:**

Final Answer: <clear complete response to the user, including how to run the project and any security notes>

Available tools:
"""
TOOL_DOCS = "\n".join(f"- {name}: {meta['desc']}" for name, meta in TOOLS.items())
SYSTEM_PROMPT = SYSTEM_CORE + TOOL_DOCS + """

=== STRICT RULES ===

1. ALWAYS write a thoughtful Thought before every Action. Never skip reasoning.
2. Prefer many small, correct steps over one giant risky step.
3. For complex or full-stack apps:
   - First create a clear project structure (folders + key files)
   - Then implement backend / API
   - Then database models
   - Then frontend or templates
   - Then auth / security layer
   - Then tests or a quick verification
   - Finally write a short README with run instructions
4. For cybersecurity tasks:
   - Prefer defensive and educational approaches
   - Point out risks and mitigations
   - Never help with real-world attacks against systems you do not own
   - Focus on secure coding patterns, auditing code, hardening configs, CTF-style learning, and defensive tools
5. Action Input MUST be valid JSON.
6. Read a file before modifying it if you are unsure of its contents.
7. After writing important code, run a quick test when possible.
8. Keep the workspace clean. Use relative paths only.
9. You are JagX AI by JagX & JRILICENSE.
"""


def parse_agent_response(text: str) -> Tuple[str, Optional[str], Optional[Dict], Optional[str]]:
    thought, action, action_input, final = "", None, None, None
    m = re.search(r"Thought:\s*(.*?)(?=\n(?:Action|Final Answer):|\Z)", text, re.DOTALL | re.IGNORECASE)
    if m:
        thought = m.group(1).strip()
    m = re.search(r"Final Answer:\s*(.*)", text, re.DOTALL | re.IGNORECASE)
    if m:
        return thought, None, None, m.group(1).strip()
    m = re.search(r"Action:\s*(\w+)", text, re.IGNORECASE)
    if m:
        action = m.group(1).strip()
    m = re.search(r"Action Input:\s*(\{.*\})", text, re.DOTALL | re.IGNORECASE)
    if m:
        raw = m.group(1).strip()
        try:
            action_input = json.loads(raw)
        except json.JSONDecodeError:
            try:
                cleaned = re.sub(r",\s*}", "}", raw)
                cleaned = re.sub(r",\s*]", "]", cleaned)
                action_input = json.loads(cleaned)
            except Exception:
                action_input = {"_raw": raw, "_parse_error": True}
    return thought, action, action_input, final


def call_jagx(api_key: str, base_url: str, message: str) -> str:
    headers = {"Content-Type": "application/json", "x-api-key": api_key}
    payload = {"message": message, "max_tokens": MAX_TOKENS}
    r = requests.post(f"{base_url.rstrip('/')}/chat", headers=headers, json=payload, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return r.json().get("response", "").strip()


def call_jagx_image(api_key: str, base_url: str, prompt: str) -> Optional[str]:
    try:
        headers = {"Content-Type": "application/json", "x-api-key": api_key}
        payload = {"prompt": prompt, "width": 1024, "height": 1024}
        r = requests.post(f"{base_url.rstrip('/')}/image", headers=headers, json=payload, timeout=90)
        if r.status_code == 200:
            data = r.json()
            if data.get("success") and data.get("image_base64"):
                return data["image_base64"]
    except Exception:
        pass
    return None


def make_zip_of_workspace() -> Optional[bytes]:
    files = []
    for f in WEB_WORKSPACE.rglob("*"):
        if not f.is_file():
            continue
        if any(part in HIDDEN_NAMES or part.startswith(".git") for part in f.relative_to(WEB_WORKSPACE).parts):
            continue
        files.append(f)
    if not files:
        return None
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            zf.write(f, f.relative_to(WEB_WORKSPACE))
    return buf.getvalue()


def list_workspace_files():
    result = []
    for f in sorted(WEB_WORKSPACE.rglob("*")):
        if not f.is_file():
            continue
        rel_parts = f.relative_to(WEB_WORKSPACE).parts
        if any(p in HIDDEN_NAMES or p.startswith(".git") for p in rel_parts):
            continue
        result.append(f)
    return result


# ──────────────────────────────────────────────────────────────
# STREAMLIT UI
# ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="JagX Coder",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
    .main-header {
        font-size: 2.5rem; font-weight: 800;
        background: linear-gradient(90deg, #00d2ff, #3a7bd5, #9b59b6);
        -webkit-background-clip: text; -webkit-text-fill-color: transparent;
        margin-bottom: 0.1rem;
    }
    .sub-header { color: #888; font-size: 1.05rem; margin-bottom: 1.2rem; }
    div[data-testid="stSidebar"] { background: linear-gradient(180deg, #0e1117 0%, #1a1d29 100%); }
    .stButton > button { border-radius: 8px; }
</style>
""", unsafe_allow_html=True)

API_KEY = get_secret("JAGX_API_KEY", "")
BASE_URL = get_secret("JAGX_BASE_URL", DEFAULT_BASE)

with st.sidebar:
    st.markdown("## ⚡ JagX Coder")
    st.caption("Deep-reasoning agent · Full-stack · Cybersecurity")
    st.divider()

    if API_KEY and API_KEY.startswith("jagx-"):
        st.success("API key loaded from secrets ✅")
        show_key = st.checkbox("Override API key", value=False)
        if show_key:
            api_key = st.text_input("JagX API Key", value=API_KEY, type="password")
        else:
            api_key = API_KEY
    else:
        st.warning("No shared key found — enter yours")
        api_key = st.text_input(
            "🔑 JagX API Key",
            value="",
            type="password",
            placeholder="jagx-xxxxxxxxxxxxxxxx",
        )

    base_url = st.text_input("🌐 API Base URL", value=BASE_URL)

    st.divider()
    st.markdown("### 📁 Project files")

    code_files = list_workspace_files()
    if code_files:
        for f in code_files[:40]:
            rel = str(f.relative_to(WEB_WORKSPACE))
            col1, col2 = st.columns([4, 1])
            with col1:
                st.code(rel, language=None)
            with col2:
                try:
                    st.download_button("⬇️", data=f.read_bytes(), file_name=f.name,
                                       key=f"dl_{rel}", help=f"Download {rel}")
                except Exception:
                    pass
        zip_data = make_zip_of_workspace()
        if zip_data:
            st.download_button(
                "📦 Download project ZIP",
                data=zip_data,
                file_name="jagx_project.zip",
                mime="application/zip",
                use_container_width=True,
            )
    else:
        st.caption("No project files yet — describe what you want to build.")

    col_a, col_b = st.columns(2)
    with col_a:
        if st.button("🗑️ Clear files", use_container_width=True):
            import shutil
            for item in WEB_WORKSPACE.iterdir():
                if item.is_file():
                    item.unlink()
                else:
                    shutil.rmtree(item, ignore_errors=True)
            st.rerun()
    with col_b:
        if st.button("💬 Clear chat", use_container_width=True):
            st.session_state.messages = []
            st.session_state.history = []
            st.rerun()

    st.divider()
    st.markdown("### 🎨 Generate Image")
    img_prompt = st.text_input("Image prompt", placeholder="Secure server room, cyber aesthetic...")
    if st.button("Generate image", use_container_width=True) and img_prompt:
        if not api_key or not api_key.startswith("jagx-"):
            st.error("Need a valid API key")
        else:
            with st.spinner("Generating..."):
                b64 = call_jagx_image(api_key, base_url, img_prompt)
                if b64:
                    st.image(f"data:image/png;base64,{b64}", use_container_width=True)
                    st.download_button("⬇️ Download image", data=base64.b64decode(b64),
                                       file_name="jagx_image.png", mime="image/png")
                else:
                    st.warning("Image generation failed")

    st.divider()
    st.caption("JagX AI by JagX & JRILICENSE")
    st.caption("Full-stack · Secure · Reasoning-first")

st.markdown('<p class="main-header">JagX Coder</p>', unsafe_allow_html=True)
st.markdown(
    '<p class="sub-header">Deep-reasoning agent for complex apps, full-stack projects & cybersecurity work.</p>',
    unsafe_allow_html=True,
)

EXAMPLES = [
    "Build a secure full-stack notes app: FastAPI + SQLite + JWT auth + simple HTML frontend + README",
    "Create a Python port scanner (educational) with rate limiting and clear ethical use warning",
    "Scaffold a Flask blog with user login, password hashing, and basic XSS protection",
    "Write a secure password generator + strength checker CLI with entropy calculation",
    "Build a REST API with FastAPI, API-key auth, input validation, and rate limiting",
]

st.markdown("**Try these (complex / security focused):**")
cols = st.columns(len(EXAMPLES))
for i, ex in enumerate(EXAMPLES):
    with cols[i]:
        if st.button(ex[:32] + "…", key=f"ex_{i}", use_container_width=True, help=ex):
            st.session_state["pending_prompt"] = ex

if "messages" not in st.session_state:
    st.session_state.messages = []
if "history" not in st.session_state:
    st.session_state.history = []

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

pending = st.session_state.pop("pending_prompt", None)
prompt = st.chat_input("Describe a complex app, full-stack project, or security task...") or pending

if prompt:
    if not api_key or not api_key.startswith("jagx-"):
        st.error("👉 No valid JagX API key. Add it in Streamlit Secrets or type it in the sidebar.")
        st.stop()

    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        status = st.status("🧠 JagX Coder is reasoning deeply...", expanded=True)
        log_area = st.empty()
        logs: List[str] = []

        def add_log(text: str):
            logs.append(text)
            log_area.markdown("\n\n".join(logs[-16:]))

        try:
            history = st.session_state.history[-12:]
            parts = [SYSTEM_PROMPT, "\n\n=== Conversation ==="]
            for turn in history:
                parts.append(f"{turn['role'].upper()}: {turn['content']}")
            parts.append(f"USER: {prompt}")
            parts.append("\nASSISTANT:")
            current_input = prompt
            full_prompt = "\n".join(parts)
            final_answer = None

            for round_idx in range(1, MAX_TOOL_ROUNDS + 1):
                status.update(label=f"🧠 Round {round_idx}/{MAX_TOOL_ROUNDS} — deep reasoning...")

                try:
                    if round_idx == 1:
                        raw = call_jagx(api_key, base_url, full_prompt)
                    else:
                        cont = (
                            SYSTEM_PROMPT + "\n\n=== Conversation ===\n"
                            + "\n".join(f"{t['role'].upper()}: {t['content']}" for t in st.session_state.history[-14:])
                            + f"\nUSER: {current_input}\nASSISTANT:"
                        )
                        raw = call_jagx(api_key, base_url, cont)
                except Exception as e:
                    add_log(f"❌ API error: {e}")
                    status.update(label="Failed", state="error")
                    break

                thought, action, action_input, final = parse_agent_response(raw)

                if thought:
                    add_log(f"**💭 Thought:** {thought[:400]}{'…' if len(thought) > 400 else ''}")

                if final is not None:
                    final_answer = final
                    add_log("✅ Task complete")
                    status.update(label="✅ Done!", state="complete")
                    break

                if not action or action not in TOOLS:
                    final_answer = raw
                    add_log("⚠ Free-form reply (treating as final)")
                    status.update(label="✅ Done!", state="complete")
                    break

                add_log(f"**🔧 Action:** `{action}`")
                add_log(f"**Input:** `{json.dumps(action_input, ensure_ascii=False)[:200]}`")

                tool_fn = TOOLS[action]["fn"]
                try:
                    if action_input and not action_input.get("_parse_error"):
                        result = tool_fn(**action_input)
                    else:
                        result = ToolResult(False, "", "Invalid Action Input JSON")
                except TypeError as e:
                    result = ToolResult(False, "", f"Bad arguments: {e}")
                except Exception as e:
                    result = ToolResult(False, "", str(e))

                observation = result.output if result.success else f"ERROR: {result.error}\n{result.output}"
                icon = "✅" if result.success else "❌"
                add_log(f"{icon} **Result:** {observation[:400]}{'…' if len(observation) > 400 else ''}")

                obs_msg = (
                    f"Thought: {thought}\nAction: {action}\n"
                    f"Action Input: {json.dumps(action_input)}\nObservation: {observation}"
                )
                st.session_state.history.append({"role": "assistant", "content": obs_msg})
                current_input = (
                    f"Observation from {action}:\n{observation}\n\n"
                    "Continue carefully. Reason about the next best small step."
                )

            if final_answer is None:
                final_answer = "Reached maximum rounds. The project may be partially built — check the files in the sidebar and download the ZIP."
                status.update(label="Stopped (max rounds)", state="error")

            st.markdown(final_answer)
            st.session_state.messages.append({"role": "assistant", "content": final_answer})
            st.session_state.history.append({"role": "user", "content": prompt})
            st.session_state.history.append({"role": "assistant", "content": final_answer})

            if list_workspace_files():
                st.info("📁 Project files are in the sidebar — download them as ZIP before the app sleeps.")

        except Exception as e:
            st.error(f"Unexpected error: {e}")
            traceback.print_exc()
