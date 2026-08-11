#!/usr/bin/env python3
"""
JagX Coder — Web UI (Enhanced)
Free to host on Streamlit Community Cloud / Hugging Face Spaces / Render
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
# CONFIG
# ──────────────────────────────────────────────────────────────
DEFAULT_BASE = "https://jagx-ai-v2.onrender.com"
MAX_TOKENS = 1800
MAX_TOOL_ROUNDS = 20
REQUEST_TIMEOUT = 120

WEB_WORKSPACE = Path(os.getenv("JAGX_WORKSPACE", "./web_workspace")).resolve()
WEB_WORKSPACE.mkdir(parents=True, exist_ok=True)

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
            kind = "DIR " if p.is_dir() else "FILE"
            size = f"{p.stat().st_size:>8}" if p.is_file() else "       -"
            entries.append(f"{kind}  {size}  {p.name}")
        return ToolResult(True, "\n".join(entries) if entries else "(empty)")
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


def tool_run_shell(command: str, timeout: int = 45) -> ToolResult:
    import subprocess
    try:
        dangerous = ["rm -rf /", "mkfs", ":(){:|:&};:", "dd if=/dev/zero", "chmod -R 777 /"]
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
            capture_output=True, text=True, timeout=40,
        )
        Path(tmp).unlink(missing_ok=True)
        out = (result.stdout or "") + (result.stderr or "")
        if result.returncode != 0:
            return ToolResult(False, out.strip() or "(no output)", f"exit code {result.returncode}")
        return ToolResult(True, out.strip() or "(no output)")
    except Exception as e:
        return ToolResult(False, "", str(e))


def tool_search_code(query: str, path: str = ".", glob: str = "*.py") -> ToolResult:
    try:
        target = safe_path(path)
        matches = []
        for p in target.rglob(glob):
            if not p.is_file():
                continue
            try:
                for i, line in enumerate(p.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                    if query.lower() in line.lower():
                        rel = p.relative_to(WEB_WORKSPACE)
                        matches.append(f"{rel}:{i}: {line.strip()[:120]}")
                        if len(matches) >= 40:
                            break
            except Exception:
                continue
            if len(matches) >= 40:
                break
        if not matches:
            return ToolResult(True, f"No matches for '{query}' in {glob}")
        return ToolResult(True, "\n".join(matches))
    except Exception as e:
        return ToolResult(False, "", str(e))


TOOLS = {
    "list_dir": {"fn": tool_list_dir, "desc": "List files/dirs. Args: path (default '.')"},
    "read_file": {"fn": tool_read_file, "desc": "Read file. Args: path, start_line=1, end_line=None"},
    "write_file": {"fn": tool_write_file, "desc": "Write file. Args: path, content, overwrite=True"},
    "append_file": {"fn": tool_append_file, "desc": "Append to file. Args: path, content"},
    "run_shell": {"fn": tool_run_shell, "desc": "Run shell command. Args: command, timeout=45"},
    "run_python": {"fn": tool_run_python, "desc": "Execute Python code. Args: code"},
    "search_code": {"fn": tool_search_code, "desc": "Search code. Args: query, path='.', glob='*.py'"},
}

# ──────────────────────────────────────────────────────────────
# PROMPT + PARSER
# ──────────────────────────────────────────────────────────────
SYSTEM_CORE = """You are JagX Coder — an elite autonomous coding agent powered by JagX AI (created by JagX & JRILICENSE).

You are extremely skilled at writing production-quality code, debugging, refactoring, and building complete applications.

You operate in a ReAct loop. For every step reply in EXACTLY this format:

Thought: <your reasoning>
Action: <tool_name>
Action Input: <valid JSON object>

When the task is complete:

Thought: <final reasoning>
Final Answer: <your complete response to the user>

Available tools:
"""
TOOL_DOCS = "\n".join(f"- {name}: {meta['desc']}" for name, meta in TOOLS.items())
SYSTEM_PROMPT = SYSTEM_CORE + TOOL_DOCS + """

Rules:
1. Always use Thought / Action / Action Input format for tools.
2. Action Input MUST be valid JSON.
3. Prefer small steps. Read before write. Test after change.
4. Never invent file contents — read first if unsure.
5. Prefer relative paths.
6. You are JagX AI by JagX & JRILICENSE.
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
    """Returns base64 image string or None"""
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
    files = [f for f in WEB_WORKSPACE.rglob("*") if f.is_file()]
    if not files:
        return None
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            zf.write(f, f.relative_to(WEB_WORKSPACE))
    return buf.getvalue()


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
    .example-btn { margin: 0.2rem 0; }
</style>
""", unsafe_allow_html=True)

# ── Sidebar ──────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## ⚡ JagX Coder")
    st.caption("Autonomous coding agent · Powered by JagX AI")
    st.divider()

    api_key = st.text_input(
        "🔑 JagX API Key",
        value=os.getenv("JAGX_API_KEY", ""),
        type="password",
        placeholder="jagx-xxxxxxxxxxxxxxxx",
        help="Get your key from the JagX AI service",
    )
    base_url = st.text_input(
        "🌐 API Base URL",
        value=os.getenv("JAGX_BASE_URL", DEFAULT_BASE),
    )

    st.divider()
    st.markdown("### 📁 Workspace")

    code_files = sorted([f for f in WEB_WORKSPACE.rglob("*") if f.is_file()])
    if code_files:
        for f in code_files[:25]:
            rel = str(f.relative_to(WEB_WORKSPACE))
            col1, col2 = st.columns([4, 1])
            with col1:
                st.code(rel, language=None)
            with col2:
                try:
                    data = f.read_bytes()
                    st.download_button("⬇️", data=data, file_name=f.name, key=f"dl_{rel}", help=f"Download {rel}")
                except Exception:
                    pass
        zip_data = make_zip_of_workspace()
        if zip_data:
            st.download_button(
                "📦 Download all as ZIP",
                data=zip_data,
                file_name="jagx_workspace.zip",
                mime="application/zip",
                use_container_width=True,
            )
    else:
        st.caption("No files yet — ask the agent to create some!")

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
    img_prompt = st.text_input("Image prompt", placeholder="A futuristic robot coding...")
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
                    st.warning("Image generation failed or not available")

    st.divider()
    st.caption("JagX AI by JagX & JRILICENSE")
    st.caption("Free · Open · Powerful")

# ── Main ─────────────────────────────────────────────────────
st.markdown('<p class="main-header">JagX Coder</p>', unsafe_allow_html=True)
st.markdown('<p class="sub-header">Tell me what to build — I write, run & fix the code for you.</p>', unsafe_allow_html=True)

# Example chips
EXAMPLES = [
    "Create hello.py that prints Hello from JagX Coder! and run it",
    "Build a Python CLI todo app with add/list/done and JSON storage",
    "Write a FastAPI app with /health and /items CRUD",
    "Create a factorial function and test it with 5, 7, 10",
    "Scaffold a notes API: FastAPI + SQLite + full CRUD",
]

st.markdown("**Quick start:**")
cols = st.columns(len(EXAMPLES))
for i, ex in enumerate(EXAMPLES):
    with cols[i]:
        if st.button(ex[:28] + "…", key=f"ex_{i}", use_container_width=True, help=ex):
            st.session_state["pending_prompt"] = ex

# Session state
if "messages" not in st.session_state:
    st.session_state.messages = []
if "history" not in st.session_state:
    st.session_state.history = []

# Show history
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# Handle pending example click
pending = st.session_state.pop("pending_prompt", None)
prompt = st.chat_input("Describe what you want me to code...") or pending

if prompt:
    if not api_key or not api_key.startswith("jagx-"):
        st.error("👉 Please enter a valid JagX API key in the sidebar (starts with `jagx-`).")
        st.stop()

    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        status = st.status("🤖 JagX Coder is working...", expanded=True)
        log_area = st.empty()
        logs: List[str] = []

        def add_log(text: str):
            logs.append(text)
            log_area.markdown("\n\n".join(logs[-14:]))

        try:
            history = st.session_state.history[-10:]
            parts = [SYSTEM_PROMPT, "\n\n=== Conversation ==="]
            for turn in history:
                parts.append(f"{turn['role'].upper()}: {turn['content']}")
            parts.append(f"USER: {prompt}")
            parts.append("\nASSISTANT:")
            current_input = prompt
            full_prompt = "\n".join(parts)
            final_answer = None

            for round_idx in range(1, MAX_TOOL_ROUNDS + 1):
                status.update(label=f"🤖 Round {round_idx}/{MAX_TOOL_ROUNDS} — thinking...")

                try:
                    if round_idx == 1:
                        raw = call_jagx(api_key, base_url, full_prompt)
                    else:
                        cont = (
                            SYSTEM_PROMPT + "\n\n=== Conversation ===\n"
                            + "\n".join(f"{t['role'].upper()}: {t['content']}" for t in st.session_state.history[-12:])
                            + f"\nUSER: {current_input}\nASSISTANT:"
                        )
                        raw = call_jagx(api_key, base_url, cont)
                except Exception as e:
                    add_log(f"❌ API error: {e}")
                    status.update(label="Failed", state="error")
                    break

                thought, action, action_input, final = parse_agent_response(raw)

                if thought:
                    add_log(f"**💭 Thought:** {thought[:280]}{'…' if len(thought) > 280 else ''}")

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
                add_log(f"**Input:** `{json.dumps(action_input, ensure_ascii=False)[:180]}`")

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
                add_log(f"{icon} **Result:** {observation[:350]}{'…' if len(observation) > 350 else ''}")

                obs_msg = (
                    f"Thought: {thought}\nAction: {action}\n"
                    f"Action Input: {json.dumps(action_input)}\nObservation: {observation}"
                )
                st.session_state.history.append({"role": "assistant", "content": obs_msg})
                current_input = f"Observation from {action}:\n{observation}\n\nContinue the task."

            if final_answer is None:
                final_answer = "Reached maximum rounds. Try a smaller task or refine your request."
                status.update(label="Stopped", state="error")

            st.markdown(final_answer)
            st.session_state.messages.append({"role": "assistant", "content": final_answer})
            st.session_state.history.append({"role": "user", "content": prompt})
            st.session_state.history.append({"role": "assistant", "content": final_answer})

            if code_files or list(WEB_WORKSPACE.rglob("*")):
                st.info("📁 New files may appear in the sidebar — you can download them.")

        except Exception as e:
            st.error(f"Unexpected error: {e}")
            traceback.print_exc()
