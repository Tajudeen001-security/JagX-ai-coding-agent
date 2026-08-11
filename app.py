#!/usr/bin/env python3
"""
JagX Coder — Advanced Web UI
Flow: Plan (MCQ exam-style) → Confirm → Live build with preview
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
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field

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
def get_secret(key: str, default: str = "") -> str:
    try:
        if key in st.secrets:
            return str(st.secrets[key]).strip()
    except Exception:
        pass
    return os.getenv(key, default).strip()

DEFAULT_BASE = "https://jagx-ai-v2.onrender.com"
MAX_TOKENS = 2200
MAX_TOOL_ROUNDS = 40
REQUEST_TIMEOUT = 150

WEB_WORKSPACE = Path(get_secret("JAGX_WORKSPACE", "./web_workspace")).resolve()
WEB_WORKSPACE.mkdir(parents=True, exist_ok=True)

HIDDEN_NAMES = {
    ".env", ".env.local", ".env.example", ".git", ".gitignore",
    ".devcontainer", "__pycache__", ".streamlit", "web_workspace",
    "app.py", "jagx_coder.py", "requirements.txt", "README.md",
    "LICENSE", "HOSTING.md", ".dockerignore",
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
        raise PermissionError(f"Path outside workspace: {path}")
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
        header = f"=== {path} ({start+1}-{min(end,len(lines))}/{len(lines)} lines, {len(text)} bytes) ==="
        return ToolResult(True, header + "\n" + "\n".join(numbered) if numbered else header + "\n(empty file)")
    except Exception as e:
        return ToolResult(False, "", str(e))


def tool_write_file(path: str, content: str, overwrite: bool = True) -> ToolResult:
    try:
        if content is None:
            content = ""
        if not isinstance(content, str):
            content = str(content)
        if len(content.strip()) < 3 and not path.endswith("__init__.py"):
            return ToolResult(False, "", f"Refused to write empty content to {path}. Provide real code.")
        target = safe_path(path)
        if target.exists() and not overwrite:
            return ToolResult(False, "", f"File exists and overwrite=False: {path}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return ToolResult(True, f"Wrote {len(content)} bytes → {path}")
    except Exception as e:
        return ToolResult(False, "", str(e))


def tool_append_file(path: str, content: str) -> ToolResult:
    try:
        if not content:
            return ToolResult(False, "", "Empty append content")
        target = safe_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as f:
            f.write(content)
        return ToolResult(True, f"Appended {len(content)} bytes → {path}")
    except Exception as e:
        return ToolResult(False, "", str(e))


def tool_run_shell(command: str, timeout: int = 60) -> ToolResult:
    import subprocess
    try:
        dangerous = ["rm -rf /", "mkfs", ":(){:|:&};:", "dd if=/dev/zero", "chmod -R 777 /"]
        if any(d in command for d in dangerous):
            return ToolResult(False, "", "Blocked dangerous command")
        result = subprocess.run(
            command, shell=True, cwd=str(WEB_WORKSPACE),
            capture_output=True, text=True, timeout=timeout,
        )
        out = (result.stdout or "") + (result.stderr or "")
        if result.returncode != 0:
            return ToolResult(False, out.strip() or "(no output)", f"exit {result.returncode}")
        return ToolResult(True, out.strip() or "(no output)")
    except subprocess.TimeoutExpired:
        return ToolResult(False, "", f"Timed out after {timeout}s")
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
            return ToolResult(False, out.strip() or "(no output)", f"exit {result.returncode}")
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
                        if len(matches) >= 40:
                            break
            except Exception:
                continue
            if len(matches) >= 40:
                break
        return ToolResult(True, "\n".join(matches) if matches else f"No matches for '{query}'")
    except Exception as e:
        return ToolResult(False, "", str(e))


TOOLS = {
    "list_dir": {"fn": tool_list_dir, "desc": "List files. Args: path='.'"},
    "read_file": {"fn": tool_read_file, "desc": "Read file. Args: path, start_line=1, end_line=None"},
    "write_file": {"fn": tool_write_file, "desc": "Write file with REAL content (never empty). Args: path, content, overwrite=True"},
    "append_file": {"fn": tool_append_file, "desc": "Append to file. Args: path, content"},
    "run_shell": {"fn": tool_run_shell, "desc": "Run shell. Args: command, timeout=60"},
    "run_python": {"fn": tool_run_python, "desc": "Run Python. Args: code"},
    "search_code": {"fn": tool_search_code, "desc": "Search code. Args: query, path='.', glob='*.*'"},
}

# ──────────────────────────────────────────────────────────────
# PROMPTS
# ──────────────────────────────────────────────────────────────
PLAN_PROMPT = """You are JagX Coder, a senior product + engineering planner powered by JagX AI (JagX & JRILICENSE).

The user wants to build something. Your ONLY job right now is to ask clarifying questions in MULTIPLE-CHOICE (exam) format so you fully understand the project before writing any code.

Rules for your reply:
1. Ask exactly 3 questions.
2. Each question must have exactly 4 options labeled A, B, C, D.
3. Questions should cover: style/theme, features/scope, and tech or security preference.
4. Do NOT write any code.
5. Do NOT ask open-ended questions.
6. Reply in this exact JSON format only (no other text):

{
  "intro": "Short friendly sentence about what you understood",
  "questions": [
    {
      "id": 1,
      "question": "Question text here?",
      "options": {
        "A": "option text",
        "B": "option text",
        "C": "option text",
        "D": "option text"
      }
    },
    {
      "id": 2,
      "question": "...",
      "options": {"A": "...", "B": "...", "C": "...", "D": "..."}
    },
    {
      "id": 3,
      "question": "...",
      "options": {"A": "...", "B": "...", "C": "...", "D": "..."}
    }
  ]
}
"""

BUILD_PROMPT = """You are JagX Coder — an elite autonomous software engineer and cybersecurity specialist powered by JagX AI (created by JagX & JRILICENSE).

You do NOT rush. You think carefully before every action.
You ALWAYS write real, non-empty code. Never create empty files.

Your expertise:
- Full-stack apps (frontend + backend + database + auth)
- Complex multi-file projects with clean architecture
- Cybersecurity: secure coding, JWT, validation, hardening, OWASP

You operate in a ReAct loop. EVERY step:

Thought: <deep reasoning — goal, what exists, next small step, security notes>
Action: <tool_name>
Action Input: <valid JSON>

When fully done:

Thought: **Summary:**

Final Answer: <how to run the project + security notes>

Available tools:
""" + "\n".join(f"- {n}: {m['desc']}" for n, m in TOOLS.items()) + """

STRICT RULES:
1. ALWAYS write a real Thought before every Action.
2. write_file content MUST be complete real code (never empty, never placeholders like TODO only).
3. Prefer small correct steps.
4. For full-stack: structure → backend → models → frontend → auth → README.
5. After writing important files, verify with list_dir or read_file.
6. Action Input must be valid JSON. Escape newlines in content properly as \\n if needed, or use real multi-line JSON strings.
7. You are JagX AI by JagX & JRILICENSE.
"""


def call_jagx(api_key: str, base_url: str, message: str) -> str:
    headers = {"Content-Type": "application/json", "x-api-key": api_key}
    payload = {"message": message, "max_tokens": MAX_TOKENS}
    r = requests.post(f"{base_url.rstrip('/')}/chat", headers=headers, json=payload, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return r.json().get("response", "").strip()


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


def extract_json_block(text: str) -> Optional[dict]:
    text = text.strip()
    if text.startswith("{"):
        try:
            return json.loads(text)
        except Exception:
            pass
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass
    return None


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


def make_zip_of_workspace() -> Optional[bytes]:
    files = list_workspace_files()
    if not files:
        return None
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            zf.write(f, f.relative_to(WEB_WORKSPACE))
    return buf.getvalue()


def guess_lang(path: str) -> str:
    ext = Path(path).suffix.lower()
    return {
        ".py": "python", ".js": "javascript", ".ts": "typescript",
        ".html": "html", ".css": "css", ".json": "json",
        ".md": "markdown", ".sh": "bash", ".yml": "yaml", ".yaml": "yaml",
        ".txt": "text", ".sql": "sql",
    }.get(ext, "text")
    # ──────────────────────────────────────────────────────────────
# UI
# ──────────────────────────────────────────────────────────────
st.set_page_config(page_title="JagX Coder", page_icon="⚡", layout="wide", initial_sidebar_state="expanded")

st.markdown("""
<style>
    .main-header {
        font-size: 2.4rem; font-weight: 800;
        background: linear-gradient(90deg, #00d2ff, #3a7bd5, #9b59b6);
        -webkit-background-clip: text; -webkit-text-fill-color: transparent;
    }
    .sub-header { color: #888; font-size: 1.05rem; margin-bottom: 1rem; }
    div[data-testid="stSidebar"] { background: linear-gradient(180deg, #0e1117 0%, #1a1d29 100%); }
    .stButton > button { border-radius: 8px; }
    .live-file { background: #1e2433; border-radius: 8px; padding: 0.5rem 0.8rem; margin: 0.3rem 0; }
</style>
""", unsafe_allow_html=True)

API_KEY = get_secret("JAGX_API_KEY", "")
BASE_URL = get_secret("JAGX_BASE_URL", DEFAULT_BASE)

for k, v in {
    "messages": [],
    "history": [],
    "phase": "idle",
    "plan_questions": None,
    "plan_intro": "",
    "user_answers": {},
    "original_request": "",
    "build_log": [],
    "last_written_file": None,
    "last_written_content": "",
}.items():
    if k not in st.session_state:
        st.session_state[k] = v

with st.sidebar:
    st.markdown("## ⚡ JagX Coder")
    st.caption("Plan → Confirm → Live build + preview")
    st.divider()

    if API_KEY and API_KEY.startswith("jagx-"):
        st.success("API key loaded from secrets ✅")
        if st.checkbox("Override key", value=False):
            api_key = st.text_input("JagX API Key", value=API_KEY, type="password")
        else:
            api_key = API_KEY
    else:
        st.warning("Enter your API key")
        api_key = st.text_input("🔑 JagX API Key", type="password", placeholder="jagx-...")

    base_url = st.text_input("🌐 Base URL", value=BASE_URL)
    st.divider()

    st.markdown("### 📁 Project files")
    code_files = list_workspace_files()
    if code_files:
        for f in code_files[:50]:
            rel = str(f.relative_to(WEB_WORKSPACE))
            size = f.stat().st_size
            col1, col2 = st.columns([4, 1])
            with col1:
                label = f"{rel} ({size} B)" if size else f"{rel} ⚠️ empty"
                st.code(label, language=None)
            with col2:
                try:
                    st.download_button("⬇️", data=f.read_bytes(), file_name=f.name, key=f"dl_{rel}")
                except Exception:
                    pass
        z = make_zip_of_workspace()
        if z:
            st.download_button("📦 Download project ZIP", data=z, file_name="jagx_project.zip",
                               mime="application/zip", use_container_width=True)
    else:
        st.caption("No files yet")

    c1, c2 = st.columns(2)
    with c1:
        if st.button("🗑️ Clear files", use_container_width=True):
            import shutil
            for item in WEB_WORKSPACE.iterdir():
                if item.is_file():
                    item.unlink()
                else:
                    shutil.rmtree(item, ignore_errors=True)
            st.session_state.last_written_file = None
            st.session_state.last_written_content = ""
            st.rerun()
    with c2:
        if st.button("🔄 Reset chat", use_container_width=True):
            for k in ["messages", "history", "plan_questions", "user_answers", "build_log"]:
                st.session_state[k] = [] if k in ("messages", "history", "build_log") else ({} if k == "user_answers" else None)
            st.session_state.phase = "idle"
            st.session_state.original_request = ""
            st.session_state.plan_intro = ""
            st.rerun()

    st.divider()
    st.caption("JagX AI by JagX & JRILICENSE")

st.markdown('<p class="main-header">JagX Coder</p>', unsafe_allow_html=True)
st.markdown('<p class="sub-header">Exam-style planning → confirm → live coding with file preview</p>', unsafe_allow_html=True)

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

if st.session_state.phase == "answering" and st.session_state.plan_questions:
    st.markdown("---")
    st.markdown("### 📋 Project setup (pick one option for each)")
    if st.session_state.plan_intro:
        st.info(st.session_state.plan_intro)

    answers = {}
    qs = st.session_state.plan_questions
    for q in qs:
        qid = q.get("id", 0)
        opts = q.get("options", {})
        choice = st.radio(
            f"**Q{qid}. {q.get('question', '')}**",
            options=list(opts.keys()),
            format_func=lambda k, o=opts: f"{k}) {o.get(k, '')}",
            key=f"q_{qid}",
        )
        answers[qid] = {"choice": choice, "text": opts.get(choice, "")}

    if st.button("✅ Submit answers & start building", type="primary", use_container_width=True):
        st.session_state.user_answers = answers
        st.session_state.phase = "building"
        brief_lines = [f"Original request: {st.session_state.original_request}", "User choices:"]
        for q in qs:
            qid = q.get("id")
            ans = answers.get(qid, {})
            brief_lines.append(f"- Q{qid} ({q.get('question')}): {ans.get('choice')}) {ans.get('text')}")
        st.session_state.build_brief = "\n".join(brief_lines)
        st.rerun()

if st.session_state.phase == "building":
    st.markdown("---")
    st.markdown("### 🔨 Live build")

    if not api_key or not api_key.startswith("jagx-"):
        st.error("No valid API key")
        st.stop()

    brief = st.session_state.get("build_brief", st.session_state.original_request)
    status = st.status("🧠 Reasoning & writing real code...", expanded=True)
    log_box = st.empty()
    preview_box = st.empty()
    logs: List[str] = []

    def add_log(t: str):
        logs.append(t)
        log_box.markdown("\n\n".join(logs[-18:]))

    def show_preview(path: str, content: str):
        st.session_state.last_written_file = path
        st.session_state.last_written_content = content
        with preview_box.container():
            st.markdown(f"**📄 Live preview: `{path}`** ({len(content)} bytes)")
            st.code(content[:8000], language=guess_lang(path))

    try:
        user_task = (
            f"Build this project carefully with REAL non-empty code.\n\n{brief}\n\n"
            "Start by creating the project structure, then write complete files one by one. "
            "Never write empty files."
        )
        full_prompt = BUILD_PROMPT + "\n\n=== Task ===\nUSER: " + user_task + "\nASSISTANT:"
        current_input = user_task
        final_answer = None
        history = []

        for round_idx in range(1, MAX_TOOL_ROUNDS + 1):
            status.update(label=f"🧠 Round {round_idx}/{MAX_TOOL_ROUNDS}")

            try:
                if round_idx == 1:
                    raw = call_jagx(api_key, base_url, full_prompt)
                else:
                    cont = (
                        BUILD_PROMPT + "\n\n=== Conversation ===\n"
                        + "\n".join(f"{t['role'].upper()}: {t['content']}" for t in history[-16:])
                        + f"\nUSER: {current_input}\nASSISTANT:"
                    )
                    raw = call_jagx(api_key, base_url, cont)
            except Exception as e:
                add_log(f"❌ API error: {e}")
                status.update(label="Failed", state="error")
                break

            thought, action, action_input, final = parse_agent_response(raw)

            if thought:
                add_log(f"**💭 Thought:** {thought[:350]}{'…' if len(thought)>350 else ''}")

            if final is not None:
                final_answer = final
                add_log("✅ Build complete")
                status.update(label="✅ Done!", state="complete")
                break

            if not action or action not in TOOLS:
                final_answer = raw
                add_log("⚠ Free-form (treating as final)")
                status.update(label="✅ Done!", state="complete")
                break

            add_log(f"**🔧 Action:** `{action}`")

            if action == "write_file" and isinstance(action_input, dict) and not action_input.get("_parse_error"):
                path = action_input.get("path", "")
                content = action_input.get("content", "")
                if content and len(str(content).strip()) > 3:
                    show_preview(str(path), str(content))
                    add_log(f"📝 Writing **{path}** ({len(str(content))} bytes) — preview above")
                else:
                    add_log(f"⚠️ Skipped empty write to `{path}`")

            tool_fn = TOOLS[action]["fn"]
            try:
                if action_input and not action_input.get("_parse_error"):
                    result = tool_fn(**action_input)
                else:
                    result = ToolResult(False, "", "Invalid Action Input JSON")
            except TypeError as e:
                result = ToolResult(False, "", f"Bad args: {e}")
            except Exception as e:
                result = ToolResult(False, "", str(e))

            observation = result.output if result.success else f"ERROR: {result.error}\n{result.output}"
            icon = "✅" if result.success else "❌"
            add_log(f"{icon} {observation[:300]}{'…' if len(observation)>300 else ''}")

            obs_msg = (
                f"Thought: {thought}\nAction: {action}\n"
                f"Action Input: {json.dumps(action_input)}\nObservation: {observation}"
            )
            history.append({"role": "assistant", "content": obs_msg})
            current_input = (
                f"Observation from {action}:\n{observation}\n\n"
                "Continue. Write REAL complete code. Never empty files. Next small step."
            )

        if final_answer is None:
            final_answer = "Reached max rounds. Check sidebar files — some may already be ready. Download the ZIP."
            status.update(label="Stopped", state="error")

        st.markdown("### ✅ Result")
        st.markdown(final_answer)
        st.session_state.messages.append({"role": "assistant", "content": final_answer})
        st.session_state.phase = "done"

        if list_workspace_files():
            st.info("📁 Files are in the sidebar. Download the ZIP before the app sleeps.")
            if st.session_state.last_written_file and st.session_state.last_written_content:
                with st.expander(f"Last written file: {st.session_state.last_written_file}", expanded=True):
                    st.code(st.session_state.last_written_content[:12000],
                            language=guess_lang(st.session_state.last_written_file))

    except Exception as e:
        st.error(f"Error: {e}")
        traceback.print_exc()

if st.session_state.phase in ("idle", "done"):
    EXAMPLES = [
        "Build a secure full-stack notes app with FastAPI, SQLite, JWT and a simple HTML UI",
        "Create an educational Python port scanner with rate limiting and ethical warnings",
        "Scaffold a Flask blog with login, password hashing and XSS protection",
        "Build a password generator + strength checker CLI with entropy score",
    ]
    st.markdown("**Quick ideas:**")
    cols = st.columns(len(EXAMPLES))
    for i, ex in enumerate(EXAMPLES):
        with cols[i]:
            if st.button(ex[:30] + "…", key=f"ex_{i}", use_container_width=True, help=ex):
                st.session_state["pending"] = ex

    pending = st.session_state.pop("pending", None)
    prompt = st.chat_input("Describe what you want to build...") or pending

    if prompt:
        if not api_key or not api_key.startswith("jagx-"):
            st.error("Need a valid JagX API key (Secrets or sidebar).")
            st.stop()

        st.session_state.messages.append({"role": "user", "content": prompt})
        st.session_state.original_request = prompt
        st.session_state.phase = "planning"

        with st.chat_message("user"):
            st.markdown(prompt)

        with st.chat_message("assistant"):
            with st.spinner("Preparing exam-style questions for your project..."):
                try:
                    plan_msg = PLAN_PROMPT + f"\n\nUser request:\n{prompt}"
                    raw = call_jagx(api_key, base_url, plan_msg)
                    data = extract_json_block(raw)
                    if data and "questions" in data:
                        st.session_state.plan_intro = data.get("intro", "Please answer these to customize your project:")
                        st.session_state.plan_questions = data["questions"]
                        st.session_state.phase = "answering"
                        st.markdown(st.session_state.plan_intro)
                        st.success("Answer the 3 questions below, then submit.")
                        st.rerun()
                    else:
                        st.warning("Could not parse plan questions — starting build with your request as-is.")
                        st.session_state.build_brief = prompt
                        st.session_state.phase = "building"
                        st.rerun()
                except Exception as e:
                    st.error(f"Planning failed: {e}")
                    st.session_state.phase = "idle"
