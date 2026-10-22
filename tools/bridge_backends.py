"""Agent backends driven by the Discord bridge.

Two backends are supported:

* **Antigravity** (``agy``) - the primary agent.  Emits NDJSON events through
  ``--output-format stream-json`` and identifies a conversation by a UUID.
* **Kilo Code** (``kilo``) - the secondary agent and the automatic fallback.
  Emits NDJSON events through ``--format json`` and identifies a session by
  ``ses_*``.  Its default model is the free router so a fallback never costs
  money.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

WORKSPACE_DIR = Path(__file__).resolve().parent.parent

ANTIGRAVITY_MODELS: Dict[str, str] = {
    "gemini-3.8-flash-low": "Gemini 3.8 Flash (Low - High Speed)",
    "gemini-3.8-flash-medium": "Gemini 3.8 Flash (Medium)",
    "gemini-3.8-flash-high": "Gemini 3.8 Flash (High)",
    "claude-sonnet-4-6": "Claude Sonnet 4.6 (Thinking)",
    "claude-opus-4-6-thinking": "Claude Opus 4.6 (Thinking)",
    "gpt-oss-120b-medium": "GPT-OSS 120B (Medium)",
}

KILO_DEFAULT_MODEL = "kilo/kilo-auto/free"

KILO_MODELS: Dict[str, str] = {
    "kilo/kilo-auto/free": "Kilo Auto Free (無料自動ルーティング)",
    "kilo/openrouter/free": "OpenRouter Free Router (無料)",
    "kilo/minimax/minimax-m3.1-flash:free": "MiniMax M3.1 Flash (無料)",
    "kilo/nvidia/nemotron-3-nano-30b-a3b-reasoning:free": "Nemotron 3 Nano Reasoning (無料)",
    "kilo/nvidia/nemotron-3-super-120b-a12b:free": "Nemotron 3 Super 120B (無料)",
    "kilo/nvidia/nemotron-3-ultra-550b-a55b:free": "Nemotron 3 Ultra (無料)",
    "kilo/poolside/laguna-s-2.1:free": "Laguna S 2.1 (無料)",
    "kilo/cohere/north-mini-code:free": "North Mini Code (無料)",
    "kilo/liquid/lfm-2.5-2.6b:free": "LFM 2.5 2.6B (無料)",
    "kilo/xingchen-agi/xing4.0-29b-a4b:free": "Xing 4.0 29B (無料)",
    "kilo/dots-studio/dots-3-note-preview:free": "Dots 3 Note Preview (無料)",
    "kilo/kilo-auto/efficient": "Kilo Auto Efficient (低コスト)",
    "kilo/kilo-auto/balanced": "Kilo Auto Balanced (標準)",
    "kilo/kilo-auto/frontier": "Kilo Auto Frontier (最上位)",
    "kilo/kilo-auto/small": "Kilo Auto Small (軽量)",
}

BACKEND_MODELS: Dict[str, Dict[str, str]] = {
    "antigravity": ANTIGRAVITY_MODELS,
    "kilo": KILO_MODELS,
}

ANTIGRAVITY_BACKEND = "antigravity"
KILO_BACKEND = "kilo"


def detect_backend(model: str) -> str:
    """Derive the owning backend from a model identifier."""
    lowered = (model or "").strip().lower()
    if lowered.startswith("kilo/"):
        return KILO_BACKEND
    if lowered.startswith("antigravity/"):
        return ANTIGRAVITY_BACKEND
    return ANTIGRAVITY_BACKEND


def normalize_model(model: str, backend: str) -> str:
    """Return the model id in the shape the target CLI expects.

    Antigravity takes a bare model id (``gemini-3.8-flash-low``) while the
    Kilo Code CLI takes ``provider/model`` (``kilo/kilo-auto/free``), so only
    the explicit ``antigravity/`` prefix is removed.
    """
    text = (model or "").strip()
    if backend == ANTIGRAVITY_BACKEND and text.lower().startswith("antigravity/"):
        return text[len("antigravity/"):]
    return text


def resolve_kilo_executable() -> Optional[str]:
    """Locate the Kilo Code CLI binary.

    Lookup order: ``KILO_CLI_PATH`` environment variable, ``PATH``, the
    installed VS Code extension and the user local bin directory.
    """
    explicit = os.getenv("KILO_CLI_PATH", "").strip()
    if explicit and Path(explicit).exists():
        return explicit
    found = shutil.which("kilo")
    if found:
        return found
    home = Path(os.path.expanduser("~"))
    candidates: List[Path] = []
    extension_root = home / ".vscode" / "extensions"
    if extension_root.is_dir():
        for entry in sorted(extension_root.glob("kilocode.kilo-code-*"), reverse=True):
            candidates.append(entry / "bin" / ("kilo.exe" if os.name == "nt" else "kilo"))
    candidates.append(home / ".local" / "bin" / "kilo")
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


def resolve_antigravity_executable() -> Optional[str]:
    """Locate the Antigravity CLI binary."""
    explicit = os.getenv("AGY_CLI_PATH", "").strip()
    if explicit and Path(explicit).exists():
        return explicit
    return shutil.which("agy")


@dataclass
class StreamEvent:
    """A single normalised event emitted by a backend."""

    kind: str
    text: str = ""
    session_id: Optional[str] = None
    model: Optional[str] = None
    tool: Optional[str] = None
    usage: Dict[str, Any] = field(default_factory=dict)
    raw: Dict[str, Any] = field(default_factory=dict)


class AgentBackend:
    """Common interface of the agent backends."""

    name = "base"

    def __init__(self, executable: Optional[str]) -> None:
        self.executable = executable

    def is_available(self) -> bool:
        if not self.executable:
            return False
        if Path(self.executable).exists():
            return True
        return shutil.which(self.executable) is not None

    def build_command(
        self,
        prompt: str,
        model: str,
        session_id: Optional[str] = None,
        continue_session: bool = False,
    ) -> List[str]:
        raise NotImplementedError

    def parse_line(self, line: str) -> Optional[StreamEvent]:
        raise NotImplementedError

    def list_sessions(self, limit: int = 10) -> List[Dict[str, Any]]:
        return []


class AntigravityBackend(AgentBackend):
    """Drives the Antigravity ``agy`` CLI in stream-json mode."""

    name = ANTIGRAVITY_BACKEND

    def build_command(
        self,
        prompt: str,
        model: str,
        session_id: Optional[str] = None,
        continue_session: bool = False,
    ) -> List[str]:
        cmd = [
            self.executable or "agy",
            "-p",
            prompt,
            "--model",
            normalize_model(model, ANTIGRAVITY_BACKEND),
            "--output-format",
            "stream-json",
            "--dangerously-skip-permissions",
        ]
        if session_id:
            cmd.extend(["--conversation", session_id])
        elif continue_session:
            cmd.append("--continue")
        return cmd

    def parse_line(self, line: str) -> Optional[StreamEvent]:
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            return StreamEvent(kind="raw", text=line)
        if not isinstance(data, dict):
            return StreamEvent(kind="raw", text=line)

        event = data.get("event")
        if event == "init":
            return StreamEvent(
                kind="session",
                session_id=data.get("conversation_id"),
                raw=data,
            )
        if event == "error":
            message = data.get("message") or data.get("error") or data.get("detail") or ""
            return StreamEvent(kind="error", text=str(message), raw=data)
        if event == "result":
            result = data.get("result") or {}
            response = result.get("response")
            usage = result.get("usage") or {}
            return StreamEvent(
                kind="final",
                text=str(response) if response else "",
                usage=usage if isinstance(usage, dict) else {},
                raw=data,
            )
        if event == "step_update":
            step = data.get("step_update") or {}
            step_type = step.get("step_type")
            tool_name = step.get("tool_name")
            if step_type == "agent_response":
                usage = step.get("usage") or {}
                return StreamEvent(
                    kind="text",
                    text=str(step.get("text_delta") or ""),
                    usage=usage if isinstance(usage, dict) else {},
                    raw=data,
                )
            if step_type == "subagent" or tool_name == "invoke_subagent":
                sub_info = step.get("subagent_info") or {}
                subagents = sub_info.get("subagents") or []
                names = ", ".join(str(item.get("type_name", "subagent")) for item in subagents)
                return StreamEvent(
                    kind="status",
                    text=f"👥 Subagent running: **{names or 'managing'}**",
                    tool=tool_name,
                    raw=data,
                )
            if step_type == "tool":
                return StreamEvent(
                    kind="status",
                    text=f"⚙️ Tool: `{tool_name}`",
                    tool=tool_name,
                    raw=data,
                )
        return None


class KiloCodeBackend(AgentBackend):
    """Drives the Kilo Code ``kilo`` CLI in raw JSON mode."""

    name = KILO_BACKEND

    def build_command(
        self,
        prompt: str,
        model: str,
        session_id: Optional[str] = None,
        continue_session: bool = False,
    ) -> List[str]:
        cmd = [
            self.executable or "kilo",
            "run",
            "--model",
            normalize_model(model, KILO_BACKEND),
            "--format",
            "json",
            "--auto",
        ]
        if session_id:
            cmd.extend(["--session", session_id])
        elif continue_session:
            cmd.append("--continue")
        cmd.extend(["--", prompt])
        return cmd

    def parse_line(self, line: str) -> Optional[StreamEvent]:
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            return StreamEvent(kind="raw", text=line)
        if not isinstance(data, dict):
            return StreamEvent(kind="raw", text=line)

        event_type = str(data.get("type") or "")
        part = data.get("part") or {}
        part_type = str(part.get("type") or "")
        session_id = data.get("sessionID")

        if "error" in event_type or "error" in part_type:
            payload = part.get("error") or data.get("error")
            if isinstance(payload, str):
                message = payload
            elif payload is not None:
                message = json.dumps(payload, ensure_ascii=False)
            else:
                message = str(part.get("message") or data.get("message") or "")
            if not message:
                message = json.dumps(data, ensure_ascii=False)
            return StreamEvent(kind="error", text=message[:2000], session_id=session_id, raw=data)
        if event_type == "text":
            return StreamEvent(
                kind="text",
                text=str(part.get("text") or ""),
                session_id=session_id,
                raw=data,
            )
        if event_type == "step_start":
            return StreamEvent(
                kind="status",
                text="🧠 Thinking / Analyzing codebase...",
                session_id=session_id,
                raw=data,
            )
        if event_type == "tool":
            tool = part.get("tool") or part.get("name") or "tool"
            state = part.get("state") or {}
            status = state.get("status") if isinstance(state, dict) else None
            return StreamEvent(
                kind="status",
                text=f"⚙️ Tool: `{tool}`{f' ({status})' if status else ''}",
                tool=str(tool),
                session_id=session_id,
                raw=data,
            )
        if event_type == "step_finish":
            model_info = part.get("model") or {}
            resolved = None
            provider = model_info.get("providerID")
            model_id = model_info.get("modelID")
            if provider and model_id:
                resolved = f"{provider}/{model_id}"
            usage: Dict[str, Any] = {
                "cost": part.get("cost"),
                "tokens": part.get("tokens") or {},
                "reason": part.get("reason"),
            }
            return StreamEvent(
                kind="final",
                text="",
                session_id=session_id,
                model=resolved,
                usage=usage,
                raw=data,
            )
        return None

    def list_sessions(self, limit: int = 10) -> List[Dict[str, Any]]:
        if not self.is_available():
            return []
        try:
            proc = subprocess.run(
                [self.executable or "kilo", "session", "list", "--format", "json", "-n", str(limit)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired):
            return []
        if proc.returncode != 0:
            return []
        try:
            data = json.loads(proc.stdout or "[]")
        except json.JSONDecodeError:
            return []
        if not isinstance(data, list):
            return []
        return [item for item in data if isinstance(item, dict)]


def build_default_backends() -> Dict[str, AgentBackend]:
    """Instantiate the backends with the executables found on this host."""
    return {
        ANTIGRAVITY_BACKEND: AntigravityBackend(resolve_antigravity_executable()),
        KILO_BACKEND: KiloCodeBackend(resolve_kilo_executable()),
    }


def model_catalog() -> Dict[str, str]:
    """Flat catalog of every selectable model (Antigravity first, then Kilo)."""
    catalog: Dict[str, str] = {}
    catalog.update(ANTIGRAVITY_MODELS)
    catalog.update(KILO_MODELS)
    return catalog


def is_kilo_model(model: str) -> bool:
    return detect_backend(model) == KILO_BACKEND


def describe_model(model: str) -> str:
    """Human readable label for a model identifier."""
    catalog = model_catalog()
    if model in catalog:
        return catalog[model]
    if is_kilo_model(model):
        return f"Kilo Code: {model}"
    return f"Antigravity: {model}"


def split_backend_model(model: str) -> Tuple[str, str]:
    """Return ``(backend, bare_model)`` for a possibly prefixed model id."""
    backend = detect_backend(model)
    return backend, normalize_model(model, backend)
