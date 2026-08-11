#!/usr/bin/env python3
"""
JagX Coder — Extra-Powerful Autonomous Coding Agent
Powered by JagX AI (https://jagx-ai-v2.onrender.com)

Features:
- ReAct-style agent loop (Think → Act → Observe)
- Full tool suite: read/write/list files, run shell, execute Python, search code
- Project-aware memory + conversation history
- Strong coding system prompt
- Safe execution with workspace isolation
- CLI interactive + one-shot modes

Usage:
  cp .env.example .env          # then edit with your key
  pip install -r requirements.txt
  python jagx_coder.py

  # One-shot
  python jagx_coder.py "Create a FastAPI hello world with /health"
"""

from __future__ import annotations

import os
import re
import sys
import json
import traceback
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

import requests

# Optional: load .env if python-dotenv is available
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ──────────────────────────────────────────────────────────────
# CONFIG
# ──────────────────────────────────────────────────────────────

JAGX_BASE_URL = os.getenv("JAGX_BASE_URL", "https://jagx-ai-v2.onrender.com").rstrip("/")
JAGX_API_KEY = os.getenv("JAGX_API_KEY", "").strip()

WORKSPACE = Path(os.getenv("JAGX_WORKSPACE", ".")).resolve()

MAX_TOKENS = 1800
MAX_TOOL_ROUNDS = 25
REQUEST_TIMEOUT = 120

# ──────────────────────────────────────────────────────────────
# TERMINAL COLORS
# ──────────────────────────────────────────────────────────────

class C:
    HEADER = "\033[95m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    END = "\033[0m"

def cprint(msg: str, color: str = "", end: str = "\n"):
    print(f"{color}{msg}{C.END}", end=end, flush=True)

# ──────────────────────────────────────────────────────────────
# JAGX CLIENT
# ──────────────────────────────────────────────────────────────

class JagXClient:
    def __init__(self, base_url: str, api_key: str):
        if not api_key:
            raise ValueError(
                "JAGX_API_KEY is missing.\n"
                "Copy .env.example → .env and set your key, or export JAGX_API_KEY=jagx-..."
            )
        self.base_url = base_url
        self.api_key = api_key
        self.session = requests.Session()
        self.session.headers.update({
            "Content-Type": "application/json",
            "x-api-key": api_key,
        })

    def chat(self, message: str, max_tokens: int = MAX_TOKENS) -> str:
        payload = {"message": message, "max_tokens": max_tokens}
        try:
            r = self.session.post(
                f"{self.base_url}/chat",
                json=payload,
                timeout=REQUEST_TIMEOUT,
            )
            r.raise_for_status()
            data = r.json()
            return data.get("response", "").strip()
        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"JagX API error: {e}") from e

# ──────────────────────────────────────────────────────────────
# TOOLS
# ──────────────────────────────────────────────────────────────

@dataclass
class ToolResult:
    success: bool
    output: str
    error: Optional[str] = None


def _safe_path(path: str) -> Path:
    """Resolve path and ensure it stays inside WORKSPACE."""
    target = (WORKSPACE / path).resolve()
    if not str(target).startswith(str(WORKSPACE)):
        raise PermissionError(f"Path outside workspace is not allowed: {path}")
    return target


def tool_list_dir(path: str = ".") -> ToolResult:
    """List files and directories relative to workspace."""
    try:
        target = _safe_path(path)
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
    """Read a file (optionally a line range)."""
    try:
        target = _safe_path(path)
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
    """Write content to a file. Creates parent directories if needed."""
    try:
        target = _safe_path(path)
        if target.exists() and not overwrite:
            return ToolResult(False, "", f"File exists and overwrite=False: {path}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return ToolResult(True, f"Wrote {len(content)} chars → {path}")
    except Exception as e:
        return ToolResult(False, "", str(e))


def tool_append_file(path: str, content: str) -> ToolResult:
    """Append content to a file."""
    try:
        target = _safe_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as f:
            f.write(content)
        return ToolResult(True, f"Appended {len(content)} chars → {path}")
    except Exception as e:
        return ToolResult(False, "", str(e))


def tool_run_shell(command: str, timeout: int = 60) -> ToolResult:
    """Run a shell command inside the workspace. Returns stdout+stderr."""
    import subprocess
    try:
        dangerous = ["rm -rf /", "mkfs", ":(){:|:&};:", "dd if=/dev/zero", "chmod -R 777 /"]
        if any(d in command for d in dangerous):
            return ToolResult(False, "", "Blocked potentially destructive command")

        result = subprocess.run(
            command,
            shell=True,
            cwd=str(WORKSPACE),
            capture_output=True,
            text=True,
            timeout=timeout,
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
    """Execute a Python code snippet and return stdout/stderr."""
    import subprocess
    import tempfile
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, dir=str(WORKSPACE)
        ) as f:
            f.write(code)
            tmp = f.name
        result = subprocess.run(
            [sys.executable, tmp],
            cwd=str(WORKSPACE),
            capture_output=True,
            text=True,
            timeout=45,
        )
        Path(tmp).unlink(missing_ok=True)
        out = (result.stdout or "") + (result.stderr or "")
        if result.returncode != 0:
            return ToolResult(False, out.strip() or "(no output)", f"exit code {result.returncode}")
        return ToolResult(True, out.strip() or "(no output)")
    except Exception as e:
        return ToolResult(False, "", str(e))


def tool_search_code(query: str, path: str = ".", glob: str = "*.py") -> ToolResult:
    """Simple recursive text search (grep-like)."""
    try:
        target = _safe_path(path)
        matches = []
        for p in target.rglob(glob):
            if not p.is_file():
                continue
            try:
                for i, line in enumerate(
                    p.read_text(encoding="utf-8", errors="ignore").splitlines(), 1
                ):
                    if query.lower() in line.lower():
                        rel = p.relative_to(WORKSPACE)
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
    "list_dir": {
        "fn": tool_list_dir,
        "desc": "List files/dirs. Args: path (default '.')",
    },
    "read_file": {
        "fn": tool_read_file,
        "desc": "Read file content with line numbers. Args: path, start_line=1, end_line=None",
    },
    "write_file": {
        "fn": tool_write_file,
        "desc": "Write (or overwrite) a file. Args: path, content, overwrite=True",
    },
    "append_file": {
        "fn": tool_append_file,
        "desc": "Append text to a file. Args: path, content",
    },
    "run_shell": {
        "fn": tool_run_shell,
        "desc": "Run a shell command in the workspace. Args: command, timeout=60",
    },
    "run_python": {
        "fn": tool_run_python,
        "desc": "Execute a Python code snippet. Args: code",
    },
    "search_code": {
        "fn": tool_search_code,
        "desc": "Search for text in files. Args: query, path='.', glob='*.py'",
    },
}

# ──────────────────────────────────────────────────────────────
# AGENT PROMPT & PARSING
# ──────────────────────────────────────────────────────────────

SYSTEM_CORE = """You are JagX Coder — an elite autonomous coding agent powered by JagX AI (created by JagX & JRILICENSE).

You are extremely skilled at:
- Writing production-quality code from scratch
- Debugging, refactoring, and improving existing codebases
- Designing clean architecture and project structure
- Creating complete applications (CLI, web, scripts, libraries)
- Explaining complex technical concepts clearly

You operate in a ReAct loop. For every step you MUST reply in EXACTLY this format:

Thought: <your reasoning about what to do next>
Action: <tool_name>
Action Input: <valid JSON object with the tool arguments>

When you have the final answer or the task is complete, reply with:

Thought: <final reasoning>
Final Answer: <your complete response to the user>

Available tools:
"""

TOOL_DOCS = "\n".join(f"- {name}: {meta['desc']}" for name, meta in TOOLS.items())

SYSTEM_PROMPT = (
    SYSTEM_CORE
    + TOOL_DOCS
    + """

Rules:
1. Always use the exact Thought / Action / Action Input format when calling tools.
2. Action Input MUST be valid JSON.
3. Prefer small, focused steps. Read before you write. Test after you change.
4. Never invent file contents — always read first if unsure.
5. Keep the workspace clean. Prefer relative paths.
6. When writing code, make it correct, readable, and well-structured.
7. If a tool fails, analyze the error and try a different approach.
8. You are JagX AI by JagX & JRILICENSE — stay in character when asked who you are.
"""
)


def parse_agent_response(text: str) -> Tuple[str, Optional[str], Optional[Dict], Optional[str]]:
    """Returns (thought, action_name, action_input_dict, final_answer)"""
    thought = ""
    action = None
    action_input = None
    final = None

    m = re.search(
        r"Thought:\s*(.*?)(?=\n(?:Action|Final Answer):|\Z)",
        text,
        re.DOTALL | re.IGNORECASE,
    )
    if m:
        thought = m.group(1).strip()

    m = re.search(r"Final Answer:\s*(.*)", text, re.DOTALL | re.IGNORECASE)
    if m:
        final = m.group(1).strip()
        return thought, None, None, final

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

# ──────────────────────────────────────────────────────────────
# AGENT
# ──────────────────────────────────────────────────────────────

class JagXCoder:
    def __init__(self):
        self.client = JagXClient(JAGX_BASE_URL, JAGX_API_KEY)
        self.history: List[Dict[str, str]] = []
        self.step = 0

    def _build_prompt(self, user_msg: str) -> str:
        parts = [SYSTEM_PROMPT, "\n\n=== Conversation ==="]
        for turn in self.history[-12:]:
            role = turn["role"].upper()
            parts.append(f"{role}: {turn['content']}")
        parts.append(f"USER: {user_msg}")
        parts.append("\nASSISTANT:")
        return "\n".join(parts)

    def run(self, user_task: str) -> str:
        cprint(f"\n{'═' * 60}", C.CYAN)
        cprint("  JagX Coder  ·  Task received", C.BOLD + C.CYAN)
        cprint(f"{'═' * 60}\n", C.CYAN)
        cprint(f"▶ {user_task}\n", C.YELLOW)

        self.history.append({"role": "user", "content": user_task})
        current_input = user_task

        for round_idx in range(1, MAX_TOOL_ROUNDS + 1):
            self.step = round_idx
            cprint(f"── Round {round_idx}/{MAX_TOOL_ROUNDS} ──", C.DIM)

            full_prompt = self._build_prompt(current_input)

            try:
                raw = self.client.chat(full_prompt, max_tokens=MAX_TOKENS)
            except Exception as e:
                cprint(f"API failure: {e}", C.RED)
                return f"Error talking to JagX: {e}"

            thought, action, action_input, final = parse_agent_response(raw)

            if thought:
                preview = thought[:300] + ("..." if len(thought) > 300 else "")
                cprint(f"💭 Thought: {preview}", C.BLUE)

            if final is not None:
                cprint("\n✅ Final Answer\n", C.GREEN + C.BOLD)
                print(final)
                self.history.append({"role": "assistant", "content": final})
                return final

            if not action or action not in TOOLS:
                cprint("⚠ Model returned free-form text (treating as final)", C.YELLOW)
                print(raw)
                self.history.append({"role": "assistant", "content": raw})
                return raw

            cprint(f"🔧 Action: {action}", C.CYAN)
            cprint(
                f"   Input:  {json.dumps(action_input, ensure_ascii=False)[:200]}",
                C.DIM,
            )

            tool_fn = TOOLS[action]["fn"]
            try:
                if action_input and not action_input.get("_parse_error"):
                    result: ToolResult = tool_fn(**action_input)
                else:
                    result = ToolResult(False, "", "Invalid or missing Action Input JSON")
            except TypeError as e:
                result = ToolResult(False, "", f"Bad arguments for {action}: {e}")
            except Exception as e:
                result = ToolResult(
                    False, "", f"Tool crash: {e}\n{traceback.format_exc()}"
                )

            observation = (
                result.output if result.success else f"ERROR: {result.error}\n{result.output}"
            )
            status = "OK" if result.success else "FAIL"
            color = C.GREEN if result.success else C.RED
            preview = observation[:400] + ("..." if len(observation) > 400 else "")
            cprint(f"   [{status}] {preview}", color)

            obs_msg = (
                f"Thought: {thought}\n"
                f"Action: {action}\n"
                f"Action Input: {json.dumps(action_input)}\n"
                f"Observation: {observation}"
            )
            self.history.append({"role": "assistant", "content": obs_msg})
            current_input = f"Observation from {action}:\n{observation}\n\nContinue the task."

        return "Reached maximum tool rounds. Please refine the task or try again."

    def chat_loop(self):
        cprint(
            r"""
     ╦╔═╗╔═╗═╗ ╦  ╔═╗╔═╗╔╦╗╔═╗╦═╗
     ║╠═╣║ ╦╔╩╦╝  ║  ║ ║ ║║║╣ ╠╦╝
    ╚╝╩ ╩╚═╝╩ ╚═  ╚═╝╚═╝═╩╝╚═╝╩╚═
        Extra-Powerful Coding Agent
        Powered by JagX AI
        """,
            C.CYAN + C.BOLD,
        )
        cprint(f"Workspace : {WORKSPACE}", C.DIM)
        cprint(f"API       : {JAGX_BASE_URL}", C.DIM)
        cprint("Type your coding task (or 'quit' / 'exit' / 'clear')\n", C.DIM)

        while True:
            try:
                user = input(f"{C.GREEN}you › {C.END}").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nBye.")
                break
            if not user:
                continue
            if user.lower() in {"quit", "exit", "q"}:
                cprint("Goodbye 👋", C.CYAN)
                break
            if user.lower() == "clear":
                self.history.clear()
                cprint("History cleared.", C.YELLOW)
                continue
            if user.lower() == "workspace":
                cprint(str(WORKSPACE), C.CYAN)
                continue

            try:
                self.run(user)
            except Exception as e:
                cprint(f"Agent error: {e}", C.RED)
                traceback.print_exc()
            print()


# ──────────────────────────────────────────────────────────────
# ENTRY POINT
# ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not JAGX_API_KEY:
        cprint(
            "❌ JAGX_API_KEY is not set.\n"
            "   1. cp .env.example .env\n"
            "   2. Put your jagx-... key inside .env\n"
            "   3. Run again\n",
            C.RED,
        )
        sys.exit(1)

    agent = JagXCoder()
    if len(sys.argv) > 1:
        task = " ".join(sys.argv[1:])
        agent.run(task)
    else:
        agent.chat_loop()
