#!/usr/bin/env python
from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import os
import platform
import re
import socket
import sqlite3
import subprocess
import sys
import time
import textwrap
from pathlib import Path
from typing import Any


APP_DIR = Path(__file__).resolve().parent
CODEX_HOME = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
BOARD_HOME = Path(os.environ.get("CODEX_BOARD_HOME", CODEX_HOME / "instance-board"))
MANIFEST_PATH = BOARD_HOME / "projects.yaml"
SESSIONS_DIR = BOARD_HOME / "sessions"
MACHINES_DIR = BOARD_HOME / "machines"
ASSIGNMENTS_PATH = BOARD_HOME / "assignments.json"
SUMMARIES_PATH = BOARD_HOME / "summaries.json"
PROJECTS_DIR = BOARD_HOME / "projects"
LAUNCHES_PATH = BOARD_HOME / "launches.json"
PEERS_PATH = BOARD_HOME / "peers.json"
RECENT_SECONDS = 7 * 24 * 60 * 60
HEARTBEAT_SECONDS = 120
SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

if platform.system().lower() == "windows":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass


def now_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def machine_id() -> str:
    configured = os.environ.get("CODEX_BOARD_MACHINE_ID")
    if configured:
        return configured
    return socket.gethostname().lower()


def normalize_path(value: str | None) -> str:
    if not value:
        return ""
    value = value.replace("\\\\?\\", "")
    return str(Path(value).expanduser())


def path_startswith(path: str, root: str) -> bool:
    path_norm = os.path.normcase(os.path.abspath(normalize_path(path)))
    root_norm = os.path.normcase(os.path.abspath(normalize_path(root)))
    return path_norm == root_norm or path_norm.startswith(root_norm + os.sep)


def parse_scalar(value: str) -> Any:
    value = value.strip()
    if value == "":
        return ""
    if value in {"true", "false"}:
        return value == "true"
    if value[0:1] in {"'", '"'} and value[-1:] == value[0]:
        return value[1:-1]
    return value


def parse_manifest_yaml(path: Path) -> dict[str, Any]:
    """Parse the small manifest shape used by this tool.

    This intentionally supports only the project manifest style in README.md.
    It keeps the tool dependency-free while still letting the file be YAML.
    """
    if not path.exists():
        return {"projects": []}

    root: dict[str, Any] = {}
    stack: list[tuple[int, Any]] = [(-1, root)]

    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        text = line.strip()

        while stack and indent <= stack[-1][0]:
            stack.pop()
        if not stack:
            raise ValueError(f"{path}:{lineno}: invalid indentation")

        parent = stack[-1][1]
        if text.startswith("- "):
            item_text = text[2:].strip()
            if not isinstance(parent, list):
                raise ValueError(f"{path}:{lineno}: list item under non-list")
            item: Any
            if re.match(r"^[A-Za-z_][\w-]*:\s+", item_text):
                key, value = item_text.split(":", 1)
                item = {key.strip(): parse_scalar(value)}
                parent.append(item)
                stack.append((indent, item))
            else:
                parent.append(parse_scalar(item_text))
            continue

        if ":" not in text:
            raise ValueError(f"{path}:{lineno}: expected key: value")

        key, value = text.split(":", 1)
        key = key.strip()
        value = value.strip()

        if value:
            if not isinstance(parent, dict):
                raise ValueError(f"{path}:{lineno}: key under non-map")
            parent[key] = parse_scalar(value)
            continue

        container: Any = [] if key in {"projects", "roots", "subprojects"} else {}
        if not isinstance(parent, dict):
            raise ValueError(f"{path}:{lineno}: nested key under non-map")
        parent[key] = container
        stack.append((indent, container))

    projects = root.get("projects")
    if not isinstance(projects, list):
        raise ValueError(f"{path}: manifest must contain a projects list")
    return root


def load_manifest() -> dict[str, Any]:
    manifest = parse_manifest_yaml(MANIFEST_PATH)
    for project in manifest.get("projects", []):
        roots = project.get("roots", [])
        subprojects = project.get("subprojects", [])
        project["roots"] = roots if isinstance(roots, list) else []
        project["subprojects"] = [
            sub for sub in subprojects
            if isinstance(subprojects, list) and isinstance(sub, dict)
        ]
    return manifest


def slugify(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip().lower()).strip("-")
    return value or "project"


def load_assignments() -> dict[str, Any]:
    if not ASSIGNMENTS_PATH.exists():
        return {"sessions": {}}
    try:
        data = json.loads(ASSIGNMENTS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"sessions": {}}
    if not isinstance(data, dict):
        return {"sessions": {}}
    data.setdefault("sessions", {})
    return data


def save_assignments(data: dict[str, Any]) -> None:
    ASSIGNMENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    ASSIGNMENTS_PATH.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def load_summaries() -> dict[str, Any]:
    if not SUMMARIES_PATH.exists():
        return {"sessions": {}}
    try:
        data = json.loads(SUMMARIES_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"sessions": {}}
    if not isinstance(data, dict):
        return {"sessions": {}}
    data.setdefault("sessions", {})
    return data


def save_summaries(data: dict[str, Any]) -> None:
    SUMMARIES_PATH.parent.mkdir(parents=True, exist_ok=True)
    SUMMARIES_PATH.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def load_launches() -> dict[str, Any]:
    if not LAUNCHES_PATH.exists():
        return {"sessions": {}}
    try:
        data = json.loads(LAUNCHES_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"sessions": {}}
    if not isinstance(data, dict):
        return {"sessions": {}}
    data.setdefault("sessions", {})
    return data


def save_launches(data: dict[str, Any]) -> None:
    LAUNCHES_PATH.parent.mkdir(parents=True, exist_ok=True)
    LAUNCHES_PATH.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def load_peers() -> dict[str, Any]:
    if not PEERS_PATH.exists():
        return {"peers": []}
    try:
        data = json.loads(PEERS_PATH.read_text(encoding="utf-8-sig"))
    except Exception:
        return {"peers": []}
    if not isinstance(data, dict):
        return {"peers": []}
    peers = data.get("peers")
    data["peers"] = peers if isinstance(peers, list) else []
    return data


def detect_launch_origin(args: argparse.Namespace | None = None) -> dict[str, str]:
    args = args or argparse.Namespace()
    ssh_connection = os.environ.get("SSH_CONNECTION", "")
    ssh_client = os.environ.get("SSH_CLIENT", "")
    tmux = os.environ.get("TMUX", "")
    tmux_pane = os.environ.get("TMUX_PANE", "")
    explicit_origin = str(getattr(args, "origin", "") or os.environ.get("CODEX_DASH_ORIGIN", "")).strip()
    origin = explicit_origin or ("tmux" if tmux or tmux_pane else "ssh" if ssh_connection or ssh_client else "local")
    origin_hint = str(getattr(args, "origin_hint", "") or os.environ.get("CODEX_DASH_ORIGIN_HINT", "")).strip()
    if not origin_hint and ssh_connection:
        origin_hint = ssh_connection.split()[0]
    if not origin_hint and ssh_client:
        origin_hint = ssh_client.split()[0]
    if not origin_hint:
        origin_hint = machine_id()
    tmux_session = str(
        getattr(args, "tmux_session", "")
        or os.environ.get("CODEX_DASH_TMUX_SESSION", "")
        or tmux_pane
    ).strip()
    attach_command = str(
        getattr(args, "attach_command", "")
        or os.environ.get("CODEX_DASH_ATTACH_COMMAND", "")
        or os.environ.get("CODEX_DASH_ATTACH", "")
    ).strip()
    return {
        "launch_origin": origin,
        "origin_hint": origin_hint,
        "tmux_session": tmux_session,
        "attach_command": attach_command,
    }


def launch_label(session: dict[str, Any]) -> str:
    origin = str(session.get("launch_origin") or "local")
    hint = str(session.get("origin_hint") or session.get("machine_id") or "")
    if origin == "tmux":
        session_name = str(session.get("tmux_session") or "").strip()
        return f"tmux {session_name}" if session_name else "tmux"
    if origin == "ssh":
        return f"ssh {hint}" if hint else "ssh"
    if origin == "remote":
        return f"remote {hint}" if hint else "remote"
    return hint or "local"


def project_context_path(project_id: str) -> Path:
    return PROJECTS_DIR / f"{slugify(project_id)}.md"


def ensure_project_context(project_id: str, name: str, context: str = "") -> Path:
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    path = project_context_path(project_id)
    if not path.exists():
        body = context.strip() or "Top-level context and plans for this project."
        path.write_text(
            f"# {name}\n\n## Context\n\n{body}\n\n## Plans\n\n- Keep this project context up to date.\n",
            encoding="utf-8",
        )
    return path


def project_context_summary(project_id: str, width: int = 110) -> str:
    path = project_context_path(project_id)
    if not path.exists():
        return ""
    lines = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        text = raw.strip()
        if not text or text.startswith("#"):
            continue
        if text.startswith("- "):
            text = text[2:].strip()
        lines.append(text)
        if len(" ".join(lines)) >= width:
            break
    return short_title(" ".join(lines), width) if lines else ""


def generated_project_summary(project_id: str, sessions: list[dict[str, Any]], width: int = 130) -> str:
    scoped = sessions if project_id == "all" else [session for session in sessions if session.get("project_id") == project_id]
    if not scoped:
        return ""
    names = []
    for session in scoped[:6]:
        summary = session.get("generated_summary") or session.get("summary") or session.get("title") or ""
        summary = meaningful_text(str(summary))
        if summary and summary not in names:
            names.append(summary)
    if not names:
        return ""
    return short_title("; ".join(names), width)


def generated_subproject_summary(project_id: str, subproject_id: str, sessions: list[dict[str, Any]], width: int = 130) -> str:
    scoped = [
        session for session in sessions
        if session.get("project_id") == project_id and session.get("subproject_id") == subproject_id
    ]
    if not scoped:
        return ""
    return short_title(scoped[0].get("generated_summary") or session_summary(scoped[0], width), width)


def append_project_manifest(project_id: str, name: str, root: str = "") -> None:
    manifest = load_manifest()
    if any(str(project.get("id")) == project_id for project in manifest.get("projects", [])):
        return
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not MANIFEST_PATH.exists() or MANIFEST_PATH.stat().st_size == 0:
        MANIFEST_PATH.write_text("projects:\n", encoding="utf-8")
    with MANIFEST_PATH.open("a", encoding="utf-8") as handle:
        if MANIFEST_PATH.stat().st_size:
            handle.write("\n")
        handle.write(f"  - id: {project_id}\n")
        handle.write(f"    name: {name}\n")
        handle.write("    roots:\n")
        if root:
            handle.write(f"      - {root}\n")
        handle.write("    subprojects:\n")


def session_summary(session: dict[str, Any], width: int = 90) -> str:
    title = session.get("summary") or session.get("generated_title") or session.get("generated_summary") or session.get("title") or ""
    title = " ".join(str(title).split())
    replacements = [
        (r"^(can you|could you|please|i want you to|i would like you to)\s+", ""),
        (r"^(hi[,! ]*)", ""),
    ]
    for pattern, repl in replacements:
        title = re.sub(pattern, repl, title, flags=re.IGNORECASE).strip()
    if not title:
        cwd = session.get("cwd") or ""
        title = Path(cwd).name if cwd else session.get("id", "Untitled session")
    return short_title(title[:1].upper() + title[1:], width)


def meaningful_text(value: str) -> str:
    value = re.sub(r"```.*?```", " ", value or "", flags=re.DOTALL)
    value = re.sub(r"`([^`]+)`", r"\1", value)
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def rollout_messages(path_value: str | None, limit: int = 80) -> list[tuple[str, str]]:
    path = Path(normalize_path(path_value))
    if not path.exists():
        return []
    messages: list[tuple[str, str]] = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return []
    for line in lines:
        try:
            row = json.loads(line)
        except Exception:
            continue
        payload = row.get("payload") or {}
        role = ""
        text = ""
        if row.get("type") == "response_item" and payload.get("type") == "message":
            role = str(payload.get("role") or "")
            parts = payload.get("content") or []
            text = " ".join(str(part.get("text") or "") for part in parts if isinstance(part, dict))
        elif row.get("type") == "event_msg":
            ptype = payload.get("type")
            if ptype == "user_message":
                role = "user"
                text = str(payload.get("message") or "")
            elif ptype == "agent_message":
                role = "assistant"
                text = str(payload.get("message") or "")
        raw_text = text or ""
        raw_lower = raw_text.lower()
        if (
            "<environment_context>" in raw_lower
            or "# agents.md instructions" in raw_lower
            or raw_lower.startswith("you are codex")
            or raw_lower.startswith("<permissions instructions>")
            or raw_lower.startswith("<skills_instructions>")
        ):
            continue
        text = meaningful_text(text)
        if not text or len(text) < 3:
            continue
        if text.startswith("<environment_context>") or text.startswith("<turn_aborted>"):
            continue
        if role in {"user", "assistant"}:
            messages.append((role, text))
    return messages[-limit:]


def rollout_activity(path_value: str | None) -> dict[str, Any]:
    empty_token_data = {
        "total_tokens": 0,
        "input_tokens": 0,
        "cached_input_tokens": 0,
        "output_tokens": 0,
        "reasoning_output_tokens": 0,
        "last_tokens": 0,
        "last_input_tokens": 0,
        "last_output_tokens": 0,
        "context_window": 0,
        "context_used_percent": 0,
        "rate_limit_id": "",
        "rate_used_percent": 0,
        "rate_window_minutes": 0,
        "rate_resets_at": 0,
        "rate_plan_type": "",
        "rate_limit_reached_type": "",
    }
    path = Path(normalize_path(path_value))
    if not path.exists():
        return {
            "last_role": "",
            "last_user_at": 0,
            "last_assistant_at": 0,
            "last_message_at": 0,
            "last_tool_at": 0,
            "last_reasoning_at": 0,
            "last_event_kind": "",
            **empty_token_data,
        }
    last_role = ""
    last_user_at = 0
    last_assistant_at = 0
    last_message_at = 0
    last_tool_at = 0
    last_reasoning_at = 0
    last_event_kind = ""
    token_data = dict(empty_token_data)
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return {
            "last_role": "",
            "last_user_at": 0,
            "last_assistant_at": 0,
            "last_message_at": 0,
            "last_tool_at": 0,
            "last_reasoning_at": 0,
            "last_event_kind": "",
            **empty_token_data,
        }
    for line in lines:
        try:
            row = json.loads(line)
        except Exception:
            continue
        payload = row.get("payload") or {}
        ts = parse_timestamp_epoch(str(row.get("timestamp") or ""))
        if not ts:
            continue
        role = ""
        if row.get("type") == "response_item" and payload.get("type") == "message":
            role = str(payload.get("role") or "")
        elif row.get("type") == "response_item" and payload.get("type") in {"function_call", "function_call_output"}:
            last_tool_at = ts
            last_event_kind = str(payload.get("type") or "")
        elif row.get("type") == "response_item" and payload.get("type") == "reasoning":
            last_reasoning_at = ts
            last_event_kind = "reasoning"
        elif row.get("type") == "event_msg":
            ptype = payload.get("type")
            if ptype == "user_message":
                role = "user"
            elif ptype == "agent_message":
                role = "assistant"
            elif ptype in {"tool_call", "tool_call_output"}:
                last_tool_at = ts
                last_event_kind = str(ptype)
            elif ptype == "token_count":
                info = payload.get("info") if isinstance(payload.get("info"), dict) else {}
                total = info.get("total_token_usage") if isinstance(info.get("total_token_usage"), dict) else {}
                last = info.get("last_token_usage") if isinstance(info.get("last_token_usage"), dict) else {}
                context_window = int(info.get("model_context_window") or 0)
                total_tokens = int(total.get("total_tokens") or 0)
                last_input_tokens = int(last.get("input_tokens") or 0)
                rate_limits = payload.get("rate_limits") if isinstance(payload.get("rate_limits"), dict) else {}
                primary = rate_limits.get("primary") if isinstance(rate_limits.get("primary"), dict) else {}
                token_data = {
                    "total_tokens": total_tokens,
                    "input_tokens": int(total.get("input_tokens") or 0),
                    "cached_input_tokens": int(total.get("cached_input_tokens") or 0),
                    "output_tokens": int(total.get("output_tokens") or 0),
                    "reasoning_output_tokens": int(total.get("reasoning_output_tokens") or 0),
                    "last_tokens": int(last.get("total_tokens") or 0),
                    "last_input_tokens": last_input_tokens,
                    "last_output_tokens": int(last.get("output_tokens") or 0),
                    "context_window": context_window,
                    "context_used_percent": round((last_input_tokens / context_window) * 100, 1) if context_window else 0,
                    "rate_limit_id": str(rate_limits.get("limit_id") or ""),
                    "rate_used_percent": primary.get("used_percent") or 0,
                    "rate_window_minutes": primary.get("window_minutes") or 0,
                    "rate_resets_at": primary.get("resets_at") or 0,
                    "rate_plan_type": str(rate_limits.get("plan_type") or ""),
                    "rate_limit_reached_type": str(rate_limits.get("rate_limit_reached_type") or ""),
                }
        if role not in {"user", "assistant"}:
            continue
        last_role = role
        last_message_at = ts
        last_event_kind = role
        if role == "user":
            last_user_at = ts
        elif role == "assistant":
            last_assistant_at = ts
    return {
        "last_role": last_role,
        "last_user_at": last_user_at,
        "last_assistant_at": last_assistant_at,
        "last_message_at": last_message_at,
        "last_tool_at": last_tool_at,
        "last_reasoning_at": last_reasoning_at,
        "last_event_kind": last_event_kind,
        **token_data,
    }


def sentence_candidates(text: str) -> list[str]:
    chunks = re.split(r"(?<=[.!?])\s+|\n+|; ", text)
    return [meaningful_text(chunk) for chunk in chunks if len(meaningful_text(chunk)) >= 18]


def summarize_rollout(path_value: str | None, fallback_title: str = "") -> dict[str, str]:
    messages = rollout_messages(path_value)
    user_texts = [text for role, text in messages if role == "user"]
    assistant_texts = [text for role, text in messages if role == "assistant"]
    all_text = " ".join(user_texts + assistant_texts)

    if not messages:
        summary = meaningful_text(fallback_title)
        return {"title": session_summary({"title": summary}, 70), "summary": short_title(summary, 180)}

    keywords = [
        "dashboard", "codex", "project", "session", "zebar", "glazewm", "diamondq", "expmv",
        "spinach", "optimal control", "tailscale", "ssh", "sioyek", "powershell", "matlab",
        "latex", "slides", "gpu", "transparency", "font", "svn",
    ]
    hits = [word for word in keywords if word in all_text.lower()]
    latest_user = user_texts[-1] if user_texts else fallback_title
    first_user = user_texts[0] if user_texts else fallback_title

    action_sentences: list[str] = []
    for text in assistant_texts:
        stripped = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
        line_actions: list[str] = []
        for raw_line in stripped.splitlines():
            line = meaningful_text(raw_line.lstrip("-* ").strip())
            lower = line.lower()
            if len(line) < 12:
                continue
            if lower.startswith(("verified", "new commands", "new tui", "run it", "use:", "keys:", "changed:", "what changed:")):
                continue
            if any(token in lower for token in ("implemented", "added", "fixed", "created", "updated", "installed", "configured", "changed", "refined", "wired")):
                line_actions.append(line)
        if line_actions:
            action_sentences.extend(line_actions)
        else:
            for sentence in sentence_candidates(stripped):
                lower = sentence.lower()
                if lower.startswith(("verified", "new commands", "new tui", "run it", "use:", "keys:")):
                    continue
                if any(token in lower for token in ("implemented", "added", "fixed", "created", "updated", "installed", "configured", "changed", "refined", "wired")):
                    action_sentences.append(sentence)

    goal = meaningful_text(first_user)
    latest = meaningful_text(latest_user)
    outcome = ""
    if action_sentences:
        outcome = "; ".join(dict.fromkeys(action_sentences[-2:]))
    elif latest and latest != goal:
        outcome = latest
    summary_parts = []
    if goal:
        summary_parts.append("Goal: " + short_title(goal, 88))
    if outcome:
        summary_parts.append("Work: " + short_title(outcome, 110))
    summary = " | ".join(summary_parts) or fallback_title
    if hits:
        prefix = ", ".join(hits[:3])
        summary = f"{prefix}: {summary}"

    lower_all = all_text.lower()
    if "codex" in lower_all and "dashboard" in lower_all:
        title_source = "Codex dashboard session organization"
    elif "diamondq" in lower_all or "diamond q" in lower_all:
        title_source = "DiamondQ project work"
    elif "spinach" in lower_all or "optimal control" in lower_all:
        title_source = "Spinach expmv/step.m work" if "expmv" in lower_all or "step.m" in lower_all else "Spinach optimal-control work"
    elif "expmv" in lower_all:
        title_source = "expmv benchmark automation"
    elif "zebar" in lower_all or "glazewm" in lower_all:
        title_source = "GlazeWM and Zebar configuration"
    else:
        title_source = latest_user if latest_user and latest_user.lower() not in {"hi", "thanks"} else first_user
    title = session_summary({"title": title_source}, 72)
    return {"title": title, "summary": short_title(summary, 240)}


def read_threads(limit: int | None = None) -> list[dict[str, Any]]:
    db_path = CODEX_HOME / "state_5.sqlite"
    if not db_path.exists():
        raise FileNotFoundError(f"Codex state db not found: {db_path}")

    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        columns = {str(row["name"]) for row in con.execute("pragma table_info(threads)")}
        wanted = [
            "id",
            "cwd",
            "title",
            "created_at",
            "updated_at",
            "rollout_path",
            "git_branch",
            "git_origin_url",
            "model",
            "reasoning_effort",
            "source",
            "archived",
            "first_user_message",
            "preview",
        ]
        select_exprs = [name if name in columns else f"NULL as {name}" for name in wanted]
        where = []
        if "source" in columns:
            where.append("source = 'cli'")
        if "archived" in columns:
            where.append("archived = 0")
        query = f"select {', '.join(select_exprs)} from threads"
        if where:
            query += " where " + " and ".join(where)
        query += " order by updated_at desc"
        if limit:
            query += f" limit {int(limit)}"
        return [dict(row) for row in con.execute(query)]
    finally:
        con.close()


def classify_thread(thread: dict[str, Any], manifest: dict[str, Any]) -> tuple[str, str]:
    cwd = normalize_path(thread.get("cwd"))
    text = " ".join(
        str(thread.get(key) or "")
        for key in ("title", "first_user_message", "preview", "cwd", "git_origin_url", "git_branch")
    ).lower()
    inferred = infer_project(text)
    if inferred:
        return inferred
    best: tuple[int, str, str] | None = None
    for project in manifest.get("projects", []):
        project_id = str(project.get("id", "uncategorized"))
        for sub in project.get("subprojects", []):
            sub_id = str(sub.get("id", "default"))
            sub_cwd = sub.get("cwd")
            if sub_cwd and path_startswith(cwd, str(sub_cwd)):
                score = len(os.path.abspath(normalize_path(str(sub_cwd))))
                if best is None or score > best[0]:
                    best = (score, project_id, sub_id)
        for root in project.get("roots", []):
            if root and path_startswith(cwd, str(root)):
                score = len(os.path.abspath(normalize_path(str(root))))
                if best is None or score > best[0]:
                    best = (score, project_id, "root")
    if best:
        return best[1], best[2]
    return "uncategorized", "default"


def infer_project(text: str) -> tuple[str, str] | None:
    if "spinach" in text or "optimal control" in text or "spin dynamics" in text:
        return "spinach", "optimal-control"
    rules = [
        ("diamondq", "root", ("diamondq", "diamond q", "nv center", "nitrogen vacancy", "quantum diamond")),
        ("benchmark-expmv", "root", ("benchmark_expmv", "benchmark expmv", "expmvauto", "expmv", "matrix exponential")),
        ("home", "glazewm-zebar", ("zebar", "glazewm", "glaze wm", ".glzr")),
        ("home", "codex-dashboard", ("codex-dash", "codex dashboard", "codex-board", "instance-board", "codex instances")),
        ("home", "sioyek", ("sioyek", "pdf viewer")),
    ]
    for project_id, subproject_id, needles in rules:
        if any(needle in text for needle in needles):
            return project_id, subproject_id
    return None


def get_local_codex_processes() -> list[dict[str, Any]]:
    if platform.system().lower() != "windows":
        try:
            result = subprocess.run(
                ["ps", "-axo", "pid=,ppid=,comm=,etime="],
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            return []
        rows = []
        for line in result.stdout.splitlines():
            if "codex" not in line.lower():
                continue
            parts = line.split(None, 3)
            if len(parts) >= 3:
                rows.append({"pid": int(parts[0]), "parent_pid": int(parts[1]), "name": parts[2]})
        return rows

    ps = (
        "Get-CimInstance Win32_Process | "
        "Where-Object { $_.Name -eq 'codex.exe' -or ($_.Name -eq 'node.exe' -and $_.CommandLine -like '*@openai*codex*') } | "
        "Select-Object ProcessId,ParentProcessId,Name,CommandLine | ConvertTo-Json -Depth 3"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    if not result.stdout.strip():
        return []
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []
    if isinstance(payload, dict):
        payload = [payload]
    return [
        {
            "pid": row.get("ProcessId"),
            "parent_pid": row.get("ParentProcessId"),
            "name": row.get("Name"),
            "command_line": row.get("CommandLine"),
        }
        for row in payload
    ]


def export_state(args: argparse.Namespace) -> None:
    manifest = load_manifest()
    threads = read_threads(limit=args.limit)
    current_machine = machine_id()
    exported = []
    now = int(time.time())
    summary_cache = load_summaries()
    cached_sessions = summary_cache.setdefault("sessions", {})
    launches = load_launches().get("sessions", {})
    current_account = load_current_account()
    for thread in threads:
        project_id, subproject_id = classify_thread(thread, manifest)
        title = thread.get("title") or thread.get("first_user_message") or thread.get("preview") or ""
        updated_at = int(thread.get("updated_at") or 0)
        rollout_path = normalize_path(thread.get("rollout_path"))
        cached = cached_sessions.get(thread["id"], {})
        launch = launches.get(thread["id"], {}) if isinstance(launches.get(thread["id"]), dict) else {}
        activity = rollout_activity(rollout_path)
        if cached.get("updated_at") == updated_at and cached.get("summary") and cached.get("version") == 5:
            generated = cached
        else:
            generated = summarize_rollout(rollout_path, str(title))
            generated["updated_at"] = updated_at
            generated["version"] = 5
            cached_sessions[thread["id"]] = generated
        exported.append(
            {
                "id": thread["id"],
                "machine_id": current_machine,
                "host_machine_id": current_machine,
                "project_id": project_id,
                "subproject_id": subproject_id,
                "cwd": normalize_path(thread.get("cwd")),
                "title": title,
                "generated_title": generated.get("title") or session_summary({"title": title}, 72),
                "summary": generated.get("title") or session_summary({"title": title}, 72),
                "generated_summary": generated.get("summary") or session_summary({"title": title}, 160),
                "created_at": thread.get("created_at"),
                "updated_at": updated_at,
                "rollout_path": rollout_path,
                "git_branch": thread.get("git_branch"),
                "git_origin_url": thread.get("git_origin_url"),
                "model": thread.get("model"),
                "reasoning_effort": thread.get("reasoning_effort"),
                "launch_origin": launch.get("launch_origin") or "local",
                "origin_hint": launch.get("origin_hint") or current_machine,
                "tmux_session": launch.get("tmux_session") or "",
                "attach_command": launch.get("attach_command") or "",
                "last_role": activity.get("last_role") or "",
                "last_user_at": activity.get("last_user_at") or 0,
                "last_assistant_at": activity.get("last_assistant_at") or 0,
                "last_message_at": activity.get("last_message_at") or 0,
                "last_tool_at": activity.get("last_tool_at") or 0,
                "last_reasoning_at": activity.get("last_reasoning_at") or 0,
                "last_event_kind": activity.get("last_event_kind") or "",
                "account_label": current_account.get("label") or "",
                "account_email": current_account.get("email") or "",
                "account_name": current_account.get("name") or "",
                "account_id": current_account.get("account_id") or "",
                "account_plan_type": current_account.get("plan_type") or activity.get("rate_plan_type") or "",
                "auth_mode": current_account.get("auth_mode") or "",
                "total_tokens": activity.get("total_tokens") or 0,
                "input_tokens": activity.get("input_tokens") or 0,
                "cached_input_tokens": activity.get("cached_input_tokens") or 0,
                "output_tokens": activity.get("output_tokens") or 0,
                "reasoning_output_tokens": activity.get("reasoning_output_tokens") or 0,
                "last_tokens": activity.get("last_tokens") or 0,
                "last_input_tokens": activity.get("last_input_tokens") or 0,
                "last_output_tokens": activity.get("last_output_tokens") or 0,
                "context_window": activity.get("context_window") or 0,
                "context_used_percent": activity.get("context_used_percent") or 0,
                "rate_limit_id": activity.get("rate_limit_id") or "",
                "rate_used_percent": activity.get("rate_used_percent") or 0,
                "rate_window_minutes": activity.get("rate_window_minutes") or 0,
                "rate_resets_at": activity.get("rate_resets_at") or 0,
                "rate_plan_type": activity.get("rate_plan_type") or "",
                "rate_limit_reached_type": activity.get("rate_limit_reached_type") or "",
                "status": "recent" if now - updated_at <= RECENT_SECONDS else "stale",
            }
        )
    try:
        save_summaries(summary_cache)
    except Exception as exc:
        if not getattr(args, "quiet", False):
            print(f"Warning: could not update summary cache: {exc}", file=sys.stderr)

    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    MACHINES_DIR.mkdir(parents=True, exist_ok=True)
    (SESSIONS_DIR / f"{current_machine}.json").write_text(
        json.dumps(
            {
                "machine_id": current_machine,
                "hostname": socket.gethostname(),
                "updated_at": now_utc(),
                "sessions": exported,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    processes = get_local_codex_processes()
    (MACHINES_DIR / f"{current_machine}.json").write_text(
        json.dumps(
            {
                "machine_id": current_machine,
                "hostname": socket.gethostname(),
                "updated_at": now_utc(),
                "updated_at_epoch": now,
                "account": current_account,
                "latest_usage": latest_usage_snapshot(exported),
                "codex_processes": processes,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    if not getattr(args, "quiet", False):
        print(f"Exported {len(exported)} sessions for {current_machine} into {BOARD_HOME}")


def refresh_export_quiet(limit: int = 500) -> tuple[bool, str]:
    try:
        export_state(argparse.Namespace(limit=limit, quiet=True))
        return True, ""
    except Exception as exc:
        return False, str(exc)


def codex_state_signature() -> tuple[int, int, int, int, int]:
    """Return a cheap signature for Codex state changes.

    The dashboard writes its own state under BOARD_HOME, which is usually inside
    CODEX_HOME. Watching only the Codex database and rollout tree avoids a
    refresh loop caused by our own exported JSON files changing.
    """
    db_path = CODEX_HOME / "state_5.sqlite"
    db_mtime = 0
    db_size = 0
    try:
        stat = db_path.stat()
        db_mtime = stat.st_mtime_ns
        db_size = stat.st_size
    except OSError:
        pass

    count = 0
    max_mtime = 0
    total_size = 0
    sessions_root = CODEX_HOME / "sessions"
    if sessions_root.exists():
        try:
            iterator = sessions_root.rglob("*.jsonl")
            for path in iterator:
                try:
                    stat = path.stat()
                except OSError:
                    continue
                count += 1
                max_mtime = max(max_mtime, stat.st_mtime_ns)
                total_size += stat.st_size
        except OSError:
            pass
    return db_mtime, db_size, count, max_mtime, total_size


def run_external(command: list[str], quiet: bool = False) -> bool:
    if not quiet:
        print(" ".join(command), flush=True)
    result = subprocess.run(
        command,
        check=False,
        text=True,
        stdout=subprocess.DEVNULL if quiet else None,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0 and not quiet:
        error = (result.stderr or "").strip()
        if error:
            print(error, file=sys.stderr)
    return result.returncode == 0


def ps_single_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def remote_refresh_command(limit: int, remote_board_path: str, remote_codex_home: str) -> str:
    statements = []
    if remote_board_path:
        statements.append(f"$env:CODEX_BOARD_HOME={ps_single_quote(remote_board_path)}")
    if remote_codex_home:
        statements.append(f"$env:CODEX_HOME={ps_single_quote(remote_codex_home)}")
    statements.append(f"codex-dash refresh --quiet --limit {int(limit)}")
    command = "; ".join(statements)
    return f"powershell -NoProfile -ExecutionPolicy Bypass -Command {json.dumps(command)}"


def remote_mkdir_command(remote_board_path: str) -> str:
    command = (
        "New-Item -ItemType Directory -Force "
        f"-Path {ps_single_quote(remote_board_path + '/machines')},{ps_single_quote(remote_board_path + '/sessions')} "
        "| Out-Null"
    )
    return f"powershell -NoProfile -ExecutionPolicy Bypass -Command {json.dumps(command)}"


def sync_peers_from_args(args: argparse.Namespace) -> list[dict[str, str]]:
    targets = [str(target).strip() for target in getattr(args, "targets", []) if str(target).strip()]
    peers = []
    if targets:
        for target in targets:
            peers.append(
                {
                    "target": target,
                    "remote_board_path": str(getattr(args, "remote_board_path", "~/.codex/instance-board") or "~/.codex/instance-board"),
                    "remote_codex_home": str(getattr(args, "remote_codex_home", "") or ""),
                    "local_board_path": str(getattr(args, "local_board_path", "") or ""),
                    "local_codex_home": str(getattr(args, "local_codex_home", "") or ""),
                }
            )
        return peers

    configured = load_peers().get("peers", [])
    for peer in configured:
        if not isinstance(peer, dict):
            continue
        target = str(peer.get("target") or peer.get("host") or "").strip()
        if not target:
            continue
        peers.append(
            {
                "target": target,
                "remote_board_path": str(peer.get("remote_board_path") or "~/.codex/instance-board"),
                "remote_codex_home": str(peer.get("remote_codex_home") or ""),
                "local_board_path": str(peer.get("local_board_path") or ""),
                "local_codex_home": str(peer.get("local_codex_home") or ""),
            }
        )
    return peers


def sync_state_once(args: argparse.Namespace) -> bool:
    peers = sync_peers_from_args(args)
    if not peers:
        raise SystemExit(f"At least one sync target is required, or configure {PEERS_PATH}.")

    direction = str(getattr(args, "direction", "both") or "both")
    quiet = bool(getattr(args, "quiet", False))
    limit = int(getattr(args, "limit", 500) or 500)
    ok = True

    local_board_path = next((peer["local_board_path"] for peer in peers if peer.get("local_board_path")), "")
    local_codex_home = next((peer["local_codex_home"] for peer in peers if peer.get("local_codex_home")), "")
    board_home = Path(local_board_path).expanduser() if local_board_path else BOARD_HOME
    sessions_dir = board_home / "sessions"
    machines_dir = board_home / "machines"

    sessions_dir.mkdir(parents=True, exist_ok=True)
    machines_dir.mkdir(parents=True, exist_ok=True)

    if local_board_path:
        os.environ["CODEX_BOARD_HOME"] = local_board_path
    if local_codex_home:
        os.environ["CODEX_HOME"] = local_codex_home

    if not getattr(args, "skip_local_refresh", False):
        if local_board_path or local_codex_home:
            command = [sys.executable, str(Path(__file__).resolve()), "refresh", "--quiet", "--limit", str(limit)]
            env = os.environ.copy()
            if local_board_path:
                env["CODEX_BOARD_HOME"] = local_board_path
            if local_codex_home:
                env["CODEX_HOME"] = local_codex_home
            result = subprocess.run(command, check=False, text=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, env=env)
            local_ok, error = result.returncode == 0, (result.stderr or "").strip()
        else:
            local_ok, error = refresh_export_quiet(limit)
        ok = ok and local_ok
        if error and not quiet:
            print(f"Local refresh failed: {error}", file=sys.stderr)

    for peer in peers:
        target = peer["target"]
        remote_board_path = peer["remote_board_path"]
        remote_codex_home = peer["remote_codex_home"]
        if not quiet:
            print(f"Syncing {target}", flush=True)

        ok = run_external(["ssh", target, remote_mkdir_command(remote_board_path)], quiet=quiet) and ok

        if not getattr(args, "skip_remote_refresh", False):
            remote_refresh = remote_refresh_command(limit, remote_board_path, remote_codex_home)
            remote_ok = run_external(["ssh", target, remote_refresh], quiet=quiet)
            ok = ok and remote_ok

        if direction in {"pull", "both"}:
            pull_machines = run_external(
                ["scp", "-q", f"{target}:{remote_board_path}/machines/*.json", str(machines_dir) + os.sep],
                quiet=True,
            )
            pull_sessions = run_external(
                ["scp", "-q", f"{target}:{remote_board_path}/sessions/*.json", str(sessions_dir) + os.sep],
                quiet=True,
            )
            ok = ok and pull_machines and pull_sessions
            if not quiet and not (pull_machines and pull_sessions):
                print(f"Warning: could not pull all board files from {target}", file=sys.stderr)

        if direction in {"push", "both"}:
            machine_files = [str(path) for path in machines_dir.glob("*.json")]
            session_files = [str(path) for path in sessions_dir.glob("*.json")]
            if machine_files:
                ok = run_external(["scp", "-q", *machine_files, f"{target}:{remote_board_path}/machines/"], quiet=True) and ok
            if session_files:
                ok = run_external(["scp", "-q", *session_files, f"{target}:{remote_board_path}/sessions/"], quiet=True) and ok

    return ok


def read_json_files(folder: Path) -> list[dict[str, Any]]:
    rows = []
    if not folder.exists():
        return rows
    for path in sorted(folder.glob("*.json")):
        try:
            rows.append(json.loads(path.read_text(encoding="utf-8")))
        except Exception as exc:
            rows.append({"_error": f"{path.name}: {exc}"})
    return rows


def all_sessions() -> list[dict[str, Any]]:
    assignments = load_assignments().get("sessions", {})
    launches = load_launches().get("sessions", {})
    sessions = []
    for payload in read_json_files(SESSIONS_DIR):
        for session in payload.get("sessions", []):
            session_id = session.get("id")
            assigned = assignments.get(session_id) if session_id else None
            if isinstance(assigned, dict):
                session = dict(session)
                session["project_id"] = assigned.get("project_id", session.get("project_id"))
                session["subproject_id"] = assigned.get("subproject_id", session.get("subproject_id", "default"))
                session["assigned_project"] = True
            launch = launches.get(session_id) if session_id else None
            if isinstance(launch, dict):
                session = dict(session)
                session["launch_origin"] = launch.get("launch_origin", session.get("launch_origin", "local"))
                session["origin_hint"] = launch.get("origin_hint", session.get("origin_hint", session.get("machine_id")))
                session["tmux_session"] = launch.get("tmux_session", session.get("tmux_session", ""))
                session["attach_command"] = launch.get("attach_command", session.get("attach_command", ""))
            if (not session.get("last_role") or not session.get("total_tokens")) and session.get("rollout_path"):
                activity = rollout_activity(session.get("rollout_path"))
                session = dict(session)
                session["last_role"] = activity.get("last_role") or ""
                session["last_user_at"] = activity.get("last_user_at") or 0
                session["last_assistant_at"] = activity.get("last_assistant_at") or 0
                session["last_message_at"] = activity.get("last_message_at") or 0
                session["last_tool_at"] = activity.get("last_tool_at") or 0
                session["last_reasoning_at"] = activity.get("last_reasoning_at") or 0
                session["last_event_kind"] = activity.get("last_event_kind") or ""
                for key in (
                    "total_tokens",
                    "input_tokens",
                    "cached_input_tokens",
                    "output_tokens",
                    "reasoning_output_tokens",
                    "last_tokens",
                    "last_input_tokens",
                    "last_output_tokens",
                    "context_window",
                    "context_used_percent",
                    "rate_limit_id",
                    "rate_used_percent",
                    "rate_window_minutes",
                    "rate_resets_at",
                    "rate_plan_type",
                    "rate_limit_reached_type",
                ):
                    session[key] = activity.get(key) or session.get(key) or 0
            session.setdefault("summary", session_summary(session, 120))
            session.setdefault("generated_title", session_summary(session, 120))
            session.setdefault("generated_summary", session_summary(session, 180))
            sessions.append(session)
    sessions.sort(key=lambda row: int(row.get("updated_at") or 0), reverse=True)
    return sessions


def machine_freshness() -> dict[str, dict[str, Any]]:
    machines = {}
    now = int(time.time())
    for payload in read_json_files(MACHINES_DIR):
        mid = payload.get("machine_id")
        if not mid:
            continue
        age = now - int(payload.get("updated_at_epoch") or 0)
        payload["fresh"] = age <= HEARTBEAT_SECONDS * 2
        payload["age_seconds"] = age
        machines[mid] = payload
    return machines


def short_title(value: str, width: int = 76) -> str:
    value = " ".join((value or "").split())
    if len(value) <= width:
        return value
    return value[: width - 1] + "..."


def format_ts(epoch: int | None) -> str:
    if not epoch:
        return "unknown"
    return dt.datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M")


def parse_timestamp_epoch(value: str | None) -> int:
    if not value:
        return 0
    try:
        normalized = value.replace("Z", "+00:00")
        return int(dt.datetime.fromisoformat(normalized).timestamp())
    except Exception:
        return 0


def format_duration(seconds: int | None) -> str:
    seconds = max(0, int(seconds or 0))
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    if hours < 48:
        return f"{hours}h{minutes % 60:02d}m"
    days = hours // 24
    return f"{days}d"


def compact_number(value: Any) -> str:
    try:
        number = int(value)
    except Exception:
        return ""
    if number >= 1_000_000:
        return f"{number / 1_000_000:.1f}M"
    if number >= 10_000:
        return f"{number // 1000}k"
    if number >= 1000:
        return f"{number / 1000:.1f}k"
    return str(number)


def format_percent(value: Any) -> str:
    try:
        number = float(value)
    except Exception:
        return ""
    if number.is_integer():
        return f"{int(number)}%"
    return f"{number:.1f}%"


def format_reset_at(epoch: Any) -> str:
    try:
        seconds = int(epoch)
    except Exception:
        return ""
    delta = seconds - int(time.time())
    if abs(delta) > 365 * 24 * 60 * 60:
        return format_ts(seconds)
    if delta <= 0:
        return "now"
    return format_duration(delta)


def decode_jwt_payload(token: str | None) -> dict[str, Any]:
    if not token or token.count(".") < 2:
        return {}
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload.encode("ascii")).decode("utf-8"))
    except Exception:
        return {}


def load_current_account() -> dict[str, Any]:
    path = CODEX_HOME / "auth.json"
    account: dict[str, Any] = {
        "auth_mode": "unknown",
        "label": "unknown",
        "email": "",
        "name": "",
        "account_id": "",
        "user_id": "",
        "plan_type": "",
        "organization": "",
        "updated_at": 0,
    }
    if not path.exists():
        return account
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return account
    if not isinstance(data, dict):
        return account
    account["auth_mode"] = str(data.get("auth_mode") or "unknown")
    tokens = data.get("tokens") if isinstance(data.get("tokens"), dict) else {}
    id_claims = decode_jwt_payload(tokens.get("id_token"))
    access_claims = decode_jwt_payload(tokens.get("access_token"))
    auth_claims = {}
    for claims in (access_claims, id_claims):
        value = claims.get("https://api.openai.com/auth")
        if isinstance(value, dict):
            auth_claims.update(value)
    profile = access_claims.get("https://api.openai.com/profile")
    if isinstance(profile, dict):
        account["email"] = str(profile.get("email") or "")
    account["email"] = account["email"] or str(id_claims.get("email") or "")
    account["name"] = str(id_claims.get("name") or "")
    account["account_id"] = str(tokens.get("account_id") or auth_claims.get("chatgpt_account_id") or "")
    account["user_id"] = str(auth_claims.get("chatgpt_user_id") or auth_claims.get("user_id") or "")
    account["plan_type"] = str(auth_claims.get("chatgpt_plan_type") or "")
    orgs = auth_claims.get("organizations")
    if isinstance(orgs, list) and orgs:
        default_org = next((org for org in orgs if isinstance(org, dict) and org.get("is_default")), orgs[0])
        if isinstance(default_org, dict):
            account["organization"] = str(default_org.get("title") or default_org.get("id") or "")
    if account["auth_mode"] == "api" or data.get("OPENAI_API_KEY"):
        account["label"] = "api key"
    else:
        account["label"] = account["email"] or account["name"] or account["account_id"] or account["auth_mode"]
    try:
        account["updated_at"] = int(path.stat().st_mtime)
    except Exception:
        account["updated_at"] = 0
    return account


def token_summary_label(data: dict[str, Any]) -> str:
    if int(data.get("total_tokens") or 0) <= 0:
        return ""
    total = compact_number(data.get("total_tokens"))
    context_pct = format_percent(data.get("context_used_percent"))
    if total and context_pct:
        return f"{total} tok {context_pct} ctx"
    if total:
        return f"{total} tok"
    return ""


def rate_summary_label(data: dict[str, Any]) -> str:
    used = format_percent(data.get("rate_used_percent"))
    reset = format_reset_at(data.get("rate_resets_at"))
    plan = str(data.get("rate_plan_type") or "")
    parts = []
    if used:
        parts.append(f"{used} limit")
    if reset:
        parts.append(f"reset {reset}")
    if plan:
        parts.append(plan)
    return " ".join(parts)


def latest_usage_snapshot(sessions: list[dict[str, Any]]) -> dict[str, Any]:
    with_usage = [session for session in sessions if int(session.get("total_tokens") or 0) > 0]
    if not with_usage:
        return {}
    latest = max(with_usage, key=lambda row: int(row.get("updated_at") or 0))
    keys = (
        "total_tokens",
        "last_tokens",
        "context_window",
        "context_used_percent",
        "rate_limit_id",
        "rate_used_percent",
        "rate_window_minutes",
        "rate_resets_at",
        "rate_plan_type",
        "rate_limit_reached_type",
    )
    return {key: latest.get(key) for key in keys}


def current_machine_payload(machines: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return machines.get(machine_id(), {}) or {}


def current_account_label(machines: dict[str, dict[str, Any]]) -> str:
    payload = current_machine_payload(machines)
    account = payload.get("account") if isinstance(payload.get("account"), dict) else {}
    if account:
        return str(account.get("label") or account.get("email") or account.get("name") or account.get("auth_mode") or "unknown")
    local_account = load_current_account()
    return str(local_account.get("label") or local_account.get("email") or local_account.get("name") or local_account.get("auth_mode") or "unknown")


def session_status(session: dict[str, Any], machines: dict[str, dict[str, Any]]) -> str:
    machine = machines.get(session.get("machine_id"), {})
    status = session.get("status", "unknown")
    session_age = int(time.time()) - int(session.get("updated_at") or 0)
    recently_touched = session_age <= HEARTBEAT_SECONDS * 2
    if machine.get("fresh") and machine.get("codex_processes") and recently_touched:
        return "remote-active" if session.get("machine_id") != machine_id() else "local-active"
    return status


def session_activity_state(session: dict[str, Any], machines: dict[str, dict[str, Any]]) -> str:
    status = session_status(session, machines)
    last_role = str(session.get("last_role") or "")
    last_user_at = int(session.get("last_user_at") or 0)
    last_assistant_at = int(session.get("last_assistant_at") or 0)
    last_tool_at = int(session.get("last_tool_at") or 0)
    last_reasoning_at = int(session.get("last_reasoning_at") or 0)
    last_event_at = max(
        int(session.get("last_message_at") or 0),
        last_tool_at,
        last_reasoning_at,
    )
    if status.endswith("active"):
        if last_user_at and last_user_at >= max(last_assistant_at, last_tool_at, last_reasoning_at):
            return "working"
        if max(last_tool_at, last_reasoning_at) > last_assistant_at:
            return "working"
        if last_assistant_at and last_assistant_at >= last_event_at:
            return "waiting"
        return "working"
    if status in {"stale", "recent"}:
        if last_assistant_at and last_assistant_at >= max(last_user_at, last_tool_at, last_reasoning_at):
            return "done"
        return "closed" if last_role == "user" or last_user_at else "unknown"
    if last_role == "assistant":
        return "waiting"
    if last_role == "user":
        return "working"
    return "unknown"


def session_prompt_age(session: dict[str, Any], now: int | None = None) -> str:
    last_user_at = int(session.get("last_user_at") or session.get("updated_at") or 0)
    if not last_user_at:
        return ""
    return format_duration((now or int(time.time())) - last_user_at)


def session_activity_label(session: dict[str, Any], machines: dict[str, dict[str, Any]], frame: int = 0) -> str:
    state = session_activity_state(session, machines)
    age = session_prompt_age(session)
    if state == "working":
        return f"{SPINNER_FRAMES[frame % len(SPINNER_FRAMES)]} work {age}".rstrip()
    if state == "waiting":
        return f"✓ wait {age}".rstrip()
    if state == "done":
        return "✓ done"
    if state == "closed":
        return "× closed"
    return "· unknown"


def manifest_names(manifest: dict[str, Any]) -> tuple[dict[str, str], dict[tuple[str, str], str]]:
    names = {
        str(project.get("id")): str(project.get("name", project.get("id")))
        for project in manifest.get("projects", [])
    }
    sub_names = {}
    for project in manifest.get("projects", []):
        for sub in project.get("subprojects", []):
            sub_names[(str(project.get("id")), str(sub.get("id")))] = str(sub.get("name", sub.get("id")))
    return names, sub_names


def filter_sessions(sessions: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    query = query.strip().lower()
    if not query:
        return sessions
    terms = query.split()
    rows = []
    for session in sessions:
        haystack = " ".join(
            str(session.get(key) or "")
            for key in (
                "id",
                "machine_id",
                "project_id",
                "subproject_id",
                "cwd",
                "title",
                "git_branch",
                "git_origin_url",
                "model",
                "launch_origin",
                "origin_hint",
                "tmux_session",
            )
        ).lower()
        if all(term in haystack for term in terms):
            rows.append(session)
    return rows


def command_list(args: argparse.Namespace) -> None:
    manifest = load_manifest()
    machines = machine_freshness()
    sessions = all_sessions()

    names, sub_names = manifest_names(manifest)

    grouped: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for session in sessions:
        grouped.setdefault(session.get("project_id", "uncategorized"), {}).setdefault(
            session.get("subproject_id", "default"), []
        ).append(session)

    if not grouped:
        print("No exported sessions yet. Run: codex-board refresh")
        return

    for project_id in sorted(grouped):
        print(f"\n{names.get(project_id, project_id)} [{project_id}]")
        for subproject_id in sorted(grouped[project_id]):
            print(f"  {sub_names.get((project_id, subproject_id), subproject_id)} [{subproject_id}]")
            for session in grouped[project_id][subproject_id][: args.per_group]:
                status = session_status(session, machines)
                origin = launch_label(session)
                activity = session_activity_label(session, machines)
                tokens = token_summary_label(session) or "-"
                print(
                    "    "
                    f"{session['id']}  {status:13}  {activity:12.12}  {tokens:12.12}  {origin:16.16}  {session.get('machine_id','?')}  "
                    f"{format_ts(session.get('updated_at'))}  {short_title(session.get('title',''))}"
                )


def command_pick(args: argparse.Namespace) -> None:
    sessions = all_sessions()
    if not sessions:
        print("No exported sessions yet. Run: codex-board refresh")
        return

    machines = machine_freshness()
    print("Codex sessions\n")
    visible = sessions[: args.limit]
    for index, session in enumerate(visible, 1):
        machine = machines.get(session.get("machine_id"), {})
        status = session.get("status", "unknown")
        session_age = int(time.time()) - int(session.get("updated_at") or 0)
        if machine.get("fresh") and machine.get("codex_processes") and session_age <= HEARTBEAT_SECONDS * 2:
            status = "remote-active" if session.get("machine_id") != machine_id() else "local-active"
        print(
            f"{index:2}. {status:13} {session.get('project_id')}/{session.get('subproject_id')} "
            f"{launch_label(session)} {session.get('machine_id')} {format_ts(session.get('updated_at'))}"
        )
        print(f"    {short_title(session.get('title', ''), 100)}")

    try:
        choice = input("\nResume session number, or blank to cancel: ").strip()
    except EOFError:
        print("\nNo interactive input is available. Use `codex-dash pick` in an interactive terminal.")
        return
    if not choice:
        return
    try:
        selected = visible[int(choice) - 1]
    except (ValueError, IndexError):
        raise SystemExit(f"Invalid selection: {choice}")
    args.session_id = selected["id"]
    command_resume(args)


KEY_BINDINGS: list[tuple[str, str, str]] = [
    ("Navigation", "h / l, arrows", "Move focus between Projects and Sessions"),
    ("Navigation", "j / k, arrows", "Move within the focused panel"),
    ("Navigation", "gg / G", "Jump to top or bottom"),
    ("Navigation", "0 / $", "Jump to first or last session"),
    ("Navigation", "H / M / L", "Jump to top, middle, or bottom of visible pane"),
    ("Navigation", "Ctrl-f / Ctrl-b", "Page down or up"),
    ("Navigation", "Ctrl-d / Ctrl-u", "Half-page down or up"),
    ("Navigation", "Ctrl-e / Ctrl-y", "Move down or up one row"),
    ("Panels", "Tab / Shift-Tab", "Switch Projects/Sessions focus"),
    ("Panels", "[ / ]", "Cycle project filter"),
    ("Filters", "/", "Search sessions"),
    ("Filters", "n / N", "Next or previous search match"),
    ("Filters", "s", "Cycle status filter"),
    ("Filters", "S", "Cycle sort mode"),
    ("Filters", "a", "Show all projects and statuses"),
    ("Filters", "x", "Clear filters"),
    ("Projects", "c", "Create a project and context file"),
    ("Projects", "p", "Assign selected session to current/project id"),
    ("Actions", "r", "Refresh local session export"),
    ("Actions", "Enter", "Resume selected session"),
    ("Actions", "o", "Open/attach selected tmux or SSH session when metadata exists"),
    ("Actions", "?", "Show or hide this help"),
    ("Actions", "q / Esc", "Quit"),
    ("Mouse", "Project click", "Filter to that project"),
    ("Mouse", "Session click", "Select that session"),
    ("Mouse", "Details click", "No selection; details are read-only"),
]


def command_keys(_: argparse.Namespace) -> None:
    current = None
    for group, keys, description in KEY_BINDINGS:
        if group != current:
            if current is not None:
                print()
            print(group)
            current = group
        print(f"  {keys:<18} {description}")


def command_project(args: argparse.Namespace) -> None:
    if args.project_command == "list":
        manifest = load_manifest()
        names, _ = manifest_names(manifest)
        for project in manifest.get("projects", []):
            project_id = str(project.get("id"))
            path = project_context_path(project_id)
            summary = project_context_summary(project_id)
            marker = "md" if path.exists() else "--"
            print(f"{project_id:<22} {names.get(project_id, project_id):<28} {marker}  {summary}")
        return

    if args.project_command == "add":
        project_id = slugify(args.id)
        name = args.name or args.id
        append_project_manifest(project_id, name, args.root or "")
        path = ensure_project_context(project_id, name, args.context or "")
        print(f"Added project {project_id}: {name}")
        print(f"Context: {path}")
        return

    raise SystemExit("Missing project command. Use: codex-dash project list|add")


def command_assign(args: argparse.Namespace) -> None:
    target = None
    for session in all_sessions():
        if session["id"].startswith(args.session_id):
            if target is not None:
                raise SystemExit(f"Ambiguous session prefix: {args.session_id}")
            target = session
    if target is None:
        raise SystemExit(f"Session not found: {args.session_id}")

    manifest = load_manifest()
    project_ids = {str(project.get("id")) for project in manifest.get("projects", [])}
    project_id = slugify(args.project_id)
    if project_id not in project_ids:
        name = args.name or args.project_id
        append_project_manifest(project_id, name)
        ensure_project_context(project_id, name)

    data = load_assignments()
    data.setdefault("sessions", {})[target["id"]] = {
        "project_id": project_id,
        "subproject_id": args.subproject_id or "default",
        "assigned_at": now_utc(),
    }
    save_assignments(data)
    print(f"Assigned {target['id']} to {project_id}/{args.subproject_id or 'default'}")


class DashboardApp:
    def __init__(self, stdscr: Any, args: argparse.Namespace) -> None:
        self.stdscr = stdscr
        self.args = args
        self.query = ""
        self.message = ""
        self.cursor = 0
        self.top = 0
        self.last_search = ""
        self.sessions: list[dict[str, Any]] = []
        self.visible: list[dict[str, Any]] = []
        self.resume_id: str | None = None
        self.manifest: dict[str, Any] = {"projects": []}
        self.machines: dict[str, dict[str, Any]] = {}
        self.names: dict[str, str] = {}
        self.sub_names: dict[tuple[str, str], str] = {}

    def reload(self, quiet: bool = False) -> None:
        self.manifest = load_manifest()
        self.machines = machine_freshness()
        self.sessions = all_sessions()
        self.names, self.sub_names = manifest_names(self.manifest)
        self.apply_filter()
        if not quiet:
            self.message = f"Loaded {len(self.sessions)} sessions"

    def apply_filter(self) -> None:
        self.visible = filter_sessions(self.sessions, self.query)
        if self.cursor >= len(self.visible):
            self.cursor = max(0, len(self.visible) - 1)
        if not self.visible:
            self.cursor = 0
            self.top = 0

    def move(self, delta: int) -> None:
        if not self.visible:
            return
        self.cursor = min(max(self.cursor + delta, 0), len(self.visible) - 1)

    def current(self) -> dict[str, Any] | None:
        if not self.visible:
            return None
        return self.visible[self.cursor]

    def resume_current(self) -> None:
        session = self.current()
        if not session:
            self.message = "No session selected"
            return
        self.resume_id = session["id"]

    def prompt_search(self) -> None:
        import curses

        curses.echo()
        height, width = self.stdscr.getmaxyx()
        self.stdscr.move(height - 1, 0)
        self.stdscr.clrtoeol()
        self.stdscr.addnstr(height - 1, 0, "/", max(1, width - 1), curses.color_pair(3))
        try:
            raw = self.stdscr.getstr(height - 1, 1, max(1, width - 2))
            self.query = raw.decode(errors="ignore").strip()
            self.last_search = self.query
            self.cursor = 0
            self.top = 0
            self.apply_filter()
            self.message = f"Filter: {self.query}" if self.query else "Filter cleared"
        finally:
            curses.noecho()

    def cycle_search(self, delta: int) -> None:
        if not self.last_search:
            self.message = "No search yet"
            return
        if self.query != self.last_search:
            self.query = self.last_search
            self.apply_filter()
        self.move(delta)

    def safe_add(self, y: int, x: int, text: str, width: int, attr: int = 0) -> None:
        if width <= 0:
            return
        height, screen_width = self.stdscr.getmaxyx()
        if y < 0 or y >= height or x >= screen_width:
            return
        self.stdscr.addnstr(y, max(0, x), text, min(width, screen_width - max(0, x)), attr)

    def draw_box(self, y: int, x: int, height: int, width: int, title: str = "") -> None:
        import curses

        if height < 2 or width < 4:
            return
        horizontal = "-" * max(0, width - 2)
        self.safe_add(y, x, f"+{horizontal}+", width, curses.color_pair(7))
        for row in range(y + 1, y + height - 1):
            self.safe_add(row, x, "|", 1, curses.color_pair(7))
            self.safe_add(row, x + width - 1, "|", 1, curses.color_pair(7))
        self.safe_add(y + height - 1, x, f"+{horizontal}+", width, curses.color_pair(7))
        if title:
            self.safe_add(y, x + 2, f" {title} ", max(0, width - 4), curses.color_pair(2) | curses.A_BOLD)

    def status_attr(self, status: str) -> int:
        import curses

        if status.endswith("active"):
            return curses.color_pair(5) | curses.A_BOLD
        if status == "recent":
            return curses.color_pair(6)
        if status == "stale":
            return curses.color_pair(8)
        return curses.color_pair(0)

    def ensure_cursor_visible(self, list_height: int) -> None:
        if self.cursor < self.top:
            self.top = self.cursor
        if self.cursor >= self.top + list_height:
            self.top = self.cursor - list_height + 1
        self.top = max(0, min(self.top, max(0, len(self.visible) - list_height)))

    def draw_header(self, width: int) -> None:
        import curses

        counts = f"{len(self.visible)}/{len(self.sessions)} sessions"
        query = f" filter: {self.query}" if self.query else " filter: none"
        left = f" CODEX DASH  {counts}  {query}"
        right = f" {machine_id()} "
        self.safe_add(0, 0, left.ljust(width), width, curses.color_pair(1) | curses.A_BOLD)
        self.safe_add(0, max(0, width - len(right)), right, len(right), curses.color_pair(1) | curses.A_BOLD)

    def draw_help(self, y: int, width: int) -> None:
        import curses

        help_text = " j/k move  gg/G top/bottom  ^f/^b page  ^d/^u half  / filter  n/N next  r refresh  Enter resume  q quit "
        if self.message:
            help_text = f" {self.message} |" + help_text
        self.safe_add(y, 0, help_text.ljust(width), width, curses.color_pair(1))

    def draw_rows(self, y: int, x: int, height: int, width: int) -> None:
        import curses

        self.draw_box(y, x, height, width, "Sessions")
        inner_y = y + 2
        inner_x = x + 2
        inner_width = max(1, width - 4)
        inner_height = max(1, height - 3)
        header = f"{'#':>3} {'status':13} {'activity':12} {'project':24} {'machine':10} {'updated':16} title"
        self.safe_add(y + 1, inner_x, header.ljust(inner_width), inner_width, curses.color_pair(7) | curses.A_BOLD)

        if not self.visible:
            text = "No sessions match this filter. Press x to clear, r to refresh, or q to quit."
            self.safe_add(inner_y, inner_x, text, inner_width, curses.color_pair(2))
            return

        self.ensure_cursor_visible(inner_height)
        rows = self.visible[self.top : self.top + inner_height]
        for offset, session in enumerate(rows):
            row_y = inner_y + offset
            selected = self.top + offset == self.cursor
            attr = curses.color_pair(4) if selected else curses.color_pair(0)
            status = session_status(session, self.machines)
            activity = session_activity_label(session, self.machines)
            if status == "stale" and not selected:
                attr = curses.color_pair(8) | curses.A_DIM
            project = session.get("project_id", "uncategorized")
            subproject = session.get("subproject_id", "default")
            machine = session.get("machine_id", "?")
            updated = format_ts(session.get("updated_at"))
            path = f"{project}/{subproject}"
            title_width = max(12, inner_width - 87)
            line = (
                f"{self.top + offset + 1:>3} "
                f"{status:13} {activity:12.12} {path:24.24} {machine:10.10} {updated:16} "
                f"{short_title(session.get('title', ''), title_width)}"
            )
            self.safe_add(row_y, inner_x, line.ljust(inner_width), inner_width, attr)
            if not selected:
                self.safe_add(row_y, inner_x + 4, status[:13].ljust(13), 13, self.status_attr(status))

        if len(self.visible) > inner_height:
            scroll = f" {self.cursor + 1}/{len(self.visible)} "
            self.safe_add(y + height - 1, x + width - len(scroll) - 2, scroll, len(scroll), curses.color_pair(7))

    def draw_detail(self, y: int, x: int, height: int, width: int) -> None:
        import curses

        self.draw_box(y, x, height, width, "Details")
        session = self.current()
        if not session or height <= 0:
            return
        project = session.get("project_id", "uncategorized")
        subproject = session.get("subproject_id", "default")
        status = session_status(session, self.machines)
        activity = session_activity_label(session, self.machines)
        inner_y = y + 1
        inner_x = x + 2
        inner_width = max(1, width - 4)
        heading = f"{self.names.get(project, project)} / {self.sub_names.get((project, subproject), subproject)}"
        self.safe_add(inner_y, inner_x, heading, inner_width, curses.color_pair(2) | curses.A_BOLD)
        lines = [
            f"id:      {session.get('id')}",
            f"status:  {status} on {session.get('machine_id', '?')} at {format_ts(session.get('updated_at'))}",
            f"activity:{activity}",
            f"cwd:     {session.get('cwd') or ''}",
            f"branch:  {session.get('git_branch') or ''}",
            f"model:   {session.get('model') or ''} {session.get('reasoning_effort') or ''}".rstrip(),
            "",
        ]
        title = session.get("title") or ""
        lines.extend(textwrap.wrap(title, width=max(20, inner_width)) or [""])
        for idx, line in enumerate(lines[: max(0, height - 3)], 1):
            attr = self.status_attr(status) if line.startswith("status:") else curses.color_pair(0)
            self.safe_add(inner_y + idx, inner_x, line, inner_width, attr)

    def run(self) -> None:
        import curses

        try:
            curses.curs_set(0)
        except curses.error:
            pass
        default_bg = -1
        try:
            curses.use_default_colors()
        except curses.error:
            default_bg = curses.COLOR_BLACK
        curses.init_pair(1, curses.COLOR_BLACK, curses.COLOR_CYAN)
        curses.init_pair(2, curses.COLOR_CYAN, default_bg)
        curses.init_pair(3, curses.COLOR_YELLOW, default_bg)
        curses.init_pair(4, curses.COLOR_BLACK, curses.COLOR_WHITE)
        curses.init_pair(5, curses.COLOR_GREEN, default_bg)
        curses.init_pair(6, curses.COLOR_YELLOW, default_bg)
        curses.init_pair(7, curses.COLOR_BLUE, default_bg)
        curses.init_pair(8, curses.COLOR_WHITE, default_bg)
        self.stdscr.keypad(True)
        self.reload()

        pending_g = False
        while True:
            self.stdscr.erase()
            height, width = self.stdscr.getmaxyx()
            if height < 8 or width < 40:
                self.stdscr.addnstr(0, 0, "Terminal too small for codex-dash", max(1, width - 1))
                self.stdscr.refresh()
                key = self.stdscr.getch()
                if key in (ord("q"), 27):
                    return
                continue

            detail_height = min(10, max(5, height // 3))
            list_height = max(4, height - detail_height - 3)
            self.draw_header(width)
            self.draw_rows(1, 0, list_height, width)
            self.draw_detail(1 + list_height, 0, detail_height, width)
            self.draw_help(height - 1, width)
            self.stdscr.refresh()

            key = self.stdscr.getch()
            if pending_g:
                if key == ord("g"):
                    self.cursor = 0
                pending_g = False
                continue

            if key in (ord("q"), 27):
                return
            if key in (ord("j"), curses.KEY_DOWN):
                self.move(1)
            elif key in (ord("k"), curses.KEY_UP):
                self.move(-1)
            elif key in (ord("h"), curses.KEY_LEFT):
                self.move(-5)
            elif key in (ord("l"), curses.KEY_RIGHT):
                self.move(5)
            elif key == ord("H") and self.visible:
                self.cursor = self.top
            elif key == ord("M") and self.visible:
                self.cursor = min(len(self.visible) - 1, self.top + max(0, list_height // 2))
            elif key == ord("L") and self.visible:
                self.cursor = min(len(self.visible) - 1, self.top + max(0, list_height - 4))
            elif key == ord("0"):
                self.cursor = 0
            elif key == ord("$"):
                self.cursor = max(0, len(self.visible) - 1)
            elif key == ord("g"):
                pending_g = True
            elif key == ord("G"):
                self.cursor = max(0, len(self.visible) - 1)
            elif key in (curses.KEY_NPAGE, 6):
                self.move(list_height)
            elif key in (curses.KEY_PPAGE, 2):
                self.move(-list_height)
            elif key == 4:
                self.move(max(1, list_height // 2))
            elif key == 21:
                self.move(-max(1, list_height // 2))
            elif key == 5:
                self.move(1)
            elif key == 25:
                self.move(-1)
            elif key == ord("/"):
                self.prompt_search()
            elif key == ord("n"):
                self.cycle_search(1)
            elif key == ord("N"):
                self.cycle_search(-1)
            elif key == ord("x"):
                self.query = ""
                self.cursor = 0
                self.top = 0
                self.apply_filter()
                self.message = "Filter cleared"
            elif key == ord("r"):
                try:
                    export_state(argparse.Namespace(limit=self.args.limit))
                    self.reload(quiet=True)
                    self.message = f"Refreshed {len(self.sessions)} sessions"
                except Exception as exc:
                    self.message = f"Refresh failed: {exc}"
            elif key in (ord("\n"), ord("\r"), curses.KEY_ENTER):
                self.resume_current()
                if self.resume_id:
                    return


class AnsiDashboardApp:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.query = ""
        self.last_search = ""
        self.message = ""
        self.cursor = 0
        self.top = 0
        self.focus = "sessions"
        self.show_help = False
        self.search_mode = False
        self.search_buffer = ""
        self.input_mode: str | None = None
        self.input_buffer = ""
        self.input_data: dict[str, str] = {}
        self.project_filter = "all"
        self.status_filter = "all"
        self.sort_mode = "updated"
        self.resume_id: str | None = None
        self.attach_id: str | None = None
        self.spinner_index = 0
        self.auto_refresh_interval = max(0, int(getattr(args, "auto_refresh", 10) or 0))
        self.last_auto_refresh = 0.0
        self.refresh_process: subprocess.Popen[str] | None = None
        self.refresh_result: tuple[bool, str] | None = None
        self.refresh_old_ids: list[str | None] = []
        self.sessions: list[dict[str, Any]] = []
        self.visible: list[dict[str, Any]] = []
        self.manifest: dict[str, Any] = {"projects": []}
        self.machines: dict[str, dict[str, Any]] = {}
        self.names: dict[str, str] = {}
        self.sub_names: dict[tuple[str, str], str] = {}

    def reload(self, quiet: bool = False) -> None:
        self.manifest = load_manifest()
        self.machines = machine_freshness()
        self.sessions = all_sessions()
        self.names, self.sub_names = manifest_names(self.manifest)
        self.apply_filter()
        if not quiet:
            self.message = f"Loaded {len(self.sessions)} sessions"

    def auto_refresh(self, force: bool = False) -> None:
        if self.auto_refresh_interval <= 0 and not force:
            return
        now = time.time()
        if not force and now - self.last_auto_refresh < self.auto_refresh_interval:
            return
        self.last_auto_refresh = now
        self.start_background_refresh(force=force)

    def start_background_refresh(self, force: bool = False) -> None:
        if self.refresh_process and self.refresh_process.poll() is None:
            return
        self.refresh_result = None
        self.refresh_old_ids = [session.get("id") for session in self.sessions[:5]]
        limit = int(getattr(self.args, "limit", 500) or 500)
        cmd = [sys.executable, str(Path(__file__).resolve()), "refresh", "--quiet", "--limit", str(limit)]
        creationflags = subprocess.CREATE_NO_WINDOW if platform.system().lower() == "windows" else 0
        try:
            self.refresh_process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                creationflags=creationflags,
            )
        except Exception as exc:
            self.refresh_process = None
            self.refresh_result = (False, str(exc))
            return
        self.message = "Refreshing local sessions in background" if not force else "Loading cached data; refreshing local sessions"

    def finish_background_refresh(self) -> None:
        if not self.refresh_process:
            return
        if self.refresh_process.poll() is None:
            return
        stderr = ""
        try:
            _, stderr = self.refresh_process.communicate(timeout=0)
        except Exception:
            pass
        self.refresh_result = (self.refresh_process.returncode == 0, stderr.strip())
        self.refresh_process = None
        ok, error = self.refresh_result
        self.refresh_result = None
        if not ok:
            self.message = f"Auto-refresh failed: {short_title(error, 90)}"
            return
        self.reload(quiet=True)
        new_ids = [session.get("id") for session in self.sessions[:5]]
        if new_ids != self.refresh_old_ids:
            self.message = f"Auto-refreshed {len(self.sessions)} sessions"
        else:
            self.message = f"Refreshed {len(self.sessions)} sessions"

    def apply_filter(self) -> None:
        rows = filter_sessions(self.sessions, self.query)
        if self.project_filter != "all":
            rows = [row for row in rows if row.get("project_id", "uncategorized") == self.project_filter]
        if self.status_filter != "all":
            rows = [row for row in rows if session_status(row, self.machines) == self.status_filter]
        if self.sort_mode == "project":
            rows.sort(
                key=lambda row: (
                    str(row.get("project_id", "")),
                    str(row.get("subproject_id", "")),
                    -int(row.get("updated_at") or 0),
                )
            )
        elif self.sort_mode == "status":
            rank = {"local-active": 0, "remote-active": 1, "recent": 2, "stale": 3}
            rows.sort(key=lambda row: (rank.get(session_status(row, self.machines), 9), -int(row.get("updated_at") or 0)))
        else:
            rows.sort(key=lambda row: int(row.get("updated_at") or 0), reverse=True)
        self.visible = rows
        self.cursor = min(self.cursor, max(0, len(self.visible) - 1))
        self.top = min(self.top, max(0, len(self.visible) - 1))

    def move(self, delta: int) -> None:
        if not self.visible:
            return
        self.cursor = min(max(self.cursor + delta, 0), len(self.visible) - 1)

    def ensure_cursor_visible(self, height: int) -> None:
        if self.cursor < self.top:
            self.top = self.cursor
        if self.cursor >= self.top + height:
            self.top = self.cursor - height + 1
        self.top = max(0, min(self.top, max(0, len(self.visible) - height)))

    def current(self) -> dict[str, Any] | None:
        if not self.visible:
            return None
        return self.visible[self.cursor]

    @staticmethod
    def term_size() -> tuple[int, int]:
        try:
            size = os.get_terminal_size()
            return size.lines, size.columns
        except OSError:
            return 30, 120

    @staticmethod
    def fit(text: str, width: int) -> str:
        text = " ".join(str(text).split())
        if width <= 0:
            return ""
        if len(text) > width:
            return text[: max(0, width - 1)] + "..."
        return text.ljust(width)

    @staticmethod
    def color(text: str, code: str) -> str:
        return f"\x1b[{code}m{text}\x1b[0m"

    @staticmethod
    def strip_ansi(text: str) -> str:
        return re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", text)

    @classmethod
    def visible_len(cls, text: str) -> int:
        return len(cls.strip_ansi(text))

    @classmethod
    def fit_ansi(cls, text: str, width: int) -> str:
        if width <= 0:
            return ""
        visible = cls.visible_len(text)
        if visible <= width:
            return text + " " * (width - visible)
        plain = cls.strip_ansi(text)
        return cls.fit(plain, width)

    @classmethod
    def wrap_ansi(cls, text: str, width: int) -> list[str]:
        plain = cls.strip_ansi(text)
        wrapped = textwrap.wrap(plain, width=max(1, width), break_long_words=False, break_on_hyphens=False)
        return wrapped or [""]

    @staticmethod
    def plain_box(width: int, title: str = "") -> str:
        if width < 4:
            return "─" * width
        line = "╭" + "─" * (width - 2) + "╮"
        if title and width > len(title) + 6:
            label = f" {title} "
            line = line[:2] + label + line[2 + len(label) :]
        return line

    @staticmethod
    def bottom_box(width: int) -> str:
        if width < 4:
            return "─" * width
        return "╰" + "─" * (width - 2) + "╯"

    def pane_row(self, content: str, width: int, border: str = "38;2;76;154;180") -> str:
        if width < 4:
            return self.fit_ansi(content, width)
        return self.color("│", border) + " " + self.fit_ansi(content, width - 4) + " " + self.color("│", border)

    def pane_wrapped_rows(self, label: str, value: str, width: int, border: str = "38;2;76;154;180") -> list[str]:
        inner_width = max(1, width - 4)
        label_plain = self.strip_ansi(label)
        label_width = min(max(len(label_plain), 9), max(9, inner_width // 3))
        value_width = max(1, inner_width - label_width)
        rows = []
        for index, chunk in enumerate(self.wrap_ansi(value, value_width)):
            prefix = label if index == 0 else " " * label_width
            rows.append(self.pane_row(self.fit_ansi(prefix, label_width) + chunk, width, border))
        return rows

    def status_color(self, status: str) -> str:
        if status.endswith("active"):
            return "38;2;92;214;144;1"
        if status == "recent":
            return "38;2;245;194;96"
        if status == "stale":
            return "38;2;150;158;171"
        return "38;2;220;224;232"

    def activity_color(self, state: str) -> str:
        if state == "working":
            return "38;2;92;214;144;1"
        if state == "waiting":
            return "38;2;126;203;255;1"
        if state == "done":
            return "38;2;150;158;171;2"
        if state == "closed":
            return "38;2;245;108;108;2"
        return "38;2;150;158;171"

    def status_badge(self, status: str, session: dict[str, Any] | None = None) -> str:
        origin = str((session or {}).get("launch_origin") or "")
        if origin in {"tmux", "ssh", "remote"}:
            return self.color(origin, self.status_color(status))
        label = {
            "local-active": " local",
            "remote-active": " remote",
            "recent": "● recent",
            "stale": "○ stale",
        }.get(status, status)
        return self.color(label, self.status_color(status))

    def draw_box_line(self, width: int, title: str = "") -> str:
        return self.color(self.plain_box(width, title), "38;2;76;154;180")

    def pane_color(self, pane: str) -> str:
        return "38;2;126;203;255;1" if self.focus == pane else "38;2;76;154;180"

    def pane_top(self, width: int, title: str, pane: str) -> str:
        return self.color(self.plain_box(width, title), self.pane_color(pane))

    def pane_bottom(self, width: int, pane: str) -> str:
        return self.color(self.bottom_box(width), self.pane_color(pane))

    def cycle_focus(self, delta: int) -> None:
        panes = ["projects", "sessions"]
        index = panes.index(self.focus) if self.focus in panes else 1
        self.focus = panes[(index + delta) % len(panes)]
        self.message = f"Focus: {self.focus}"

    def move_focus(self, pane: str) -> None:
        if pane in {"projects", "sessions"}:
            self.focus = pane
            self.message = f"Focus: {self.focus}"

    def vertical_move(self, delta: int) -> None:
        if self.focus == "projects":
            self.cycle_project(delta)
        else:
            self.move(delta)

    def project_counts(self) -> list[tuple[str, str, int]]:
        rows = filter_sessions(self.sessions, self.query)
        if self.status_filter != "all":
            rows = [row for row in rows if session_status(row, self.machines) == self.status_filter]
        counts: dict[str, int] = {}
        for session in rows:
            project = session.get("project_id", "uncategorized")
            counts[project] = counts.get(project, 0) + 1
        ordered = [("all", "All Projects", len(rows))]
        for project_id in sorted(counts):
            ordered.append((project_id, self.names.get(project_id, project_id), counts[project_id]))
        return ordered

    def cycle_project(self, delta: int) -> None:
        projects = self.project_counts()
        ids = [project_id for project_id, _, _ in projects]
        if self.project_filter not in ids:
            self.project_filter = "all"
        index = ids.index(self.project_filter)
        self.project_filter = ids[(index + delta) % len(ids)]
        self.cursor = 0
        self.top = 0
        self.apply_filter()
        label = next(label for project_id, label, _ in projects if project_id == self.project_filter)
        self.message = f"Project: {label}"

    def cycle_status(self, delta: int) -> None:
        statuses = ["all", "local-active", "remote-active", "recent", "stale"]
        index = statuses.index(self.status_filter) if self.status_filter in statuses else 0
        self.status_filter = statuses[(index + delta) % len(statuses)]
        self.cursor = 0
        self.top = 0
        self.apply_filter()
        self.message = f"Status: {self.status_filter}"

    def cycle_sort(self) -> None:
        modes = ["updated", "project", "status"]
        index = modes.index(self.sort_mode) if self.sort_mode in modes else 0
        self.sort_mode = modes[(index + 1) % len(modes)]
        self.cursor = 0
        self.top = 0
        self.apply_filter()
        self.message = f"Sort: {self.sort_mode}"

    def filter_chips(self) -> str:
        project = "all" if self.project_filter == "all" else self.names.get(self.project_filter, self.project_filter)
        status = self.status_filter
        search = self.query or "none"
        return (
            self.color(f" 󰏗 {project} ", "30;48;2;126;203;255")
            + " "
            + self.color(f" 󰈙 {status} ", "30;48;2;245;194;96")
            + " "
            + self.color(f" 󰒺 {self.sort_mode} ", "30;48;2;174;141;255")
            + " "
            + self.color(f"  {search} ", "30;48;2;92;214;144")
        )

    def usage_status_segments(self) -> str:
        local_machine = current_machine_payload(self.machines)
        usage = local_machine.get("latest_usage") if isinstance(local_machine.get("latest_usage"), dict) else {}
        if not usage:
            usage = latest_usage_snapshot([session for session in self.sessions if session.get("machine_id") == machine_id()])
        account = current_account_label(self.machines)
        token = token_summary_label(usage)
        rate = rate_summary_label(usage)
        parts = [self.color(f" account {short_title(account, 26)}", "38;2;245;194;96")]
        if token:
            parts.append(self.color(f" tokens {token}", "38;2;126;203;255"))
        if rate:
            rate_color = "38;2;245;108;108;1" if usage.get("rate_limit_reached_type") else "38;2;92;214;144"
            parts.append(self.color(f" limits {rate}", rate_color))
        return " ".join(parts)

    @staticmethod
    def project_icon(project_id: str, label: str) -> str:
        text = f"{project_id} {label}".lower()
        if project_id == "all":
            return "󰏗"
        if "spinach" in text or "optimal" in text:
            return "󰐱"
        if "home" in text or "config" in text or "system" in text:
            return "󱂵"
        if "diamond" in text:
            return "󰎤"
        if "zebar" in text or "glaze" in text:
            return "󰖲"
        if "sioyek" in text or "pdf" in text:
            return "󰈦"
        icons = ["󰉋", "󰆧", "󰙅", "󰚩", "󰊢", "󰇘", "󰧮", "󰙨", "󰆼"]
        return icons[sum(ord(ch) for ch in project_id or label) % len(icons)]

    def status_bar(self, width: int) -> str:
        if self.input_mode:
            labels = {
                "create_id": "new project id",
                "create_name": "project name",
                "create_context": "project context",
                "assign_project": "assign project id",
            }
            label = labels.get(self.input_mode, self.input_mode)
            prompt = self.color(f" {label} ", "38;2;24;31;44;48;2;245;194;96;1")
            hint = "Enter accept  Esc cancel"
            value = self.input_buffer or hint
            return self.fit_ansi(prompt + self.color(" " + value, "38;2;238;241;245;48;2;24;31;44"), width)
        if self.search_mode:
            prompt = self.color(" / ", "38;2;24;31;44;48;2;92;214;144;1")
            value = self.color(self.search_buffer or "type to filter, Enter accept, Esc cancel", "38;2;238;241;245;48;2;24;31;44")
            return self.fit_ansi(prompt + value, width)
        selected = f"{self.cursor + 1}/{len(self.visible)}" if self.visible else "0/0"
        project = "all" if self.project_filter == "all" else self.names.get(self.project_filter, self.project_filter)
        message = self.message or "ready"
        segments = [
            self.color(f" 󰌌 {self.focus} ", "38;2;24;31;44;48;2;126;203;255;1"),
            self.color(f" 󰈙 {selected} ", "38;2;24;31;44;48;2;92;214;144;1"),
            self.color(f" 󰏗 {short_title(project, 18)} ", "38;2;24;31;44;48;2;245;194;96;1"),
            self.color(f"  {short_title(self.query or 'none', 18)} ", "38;2;24;31;44;48;2;174;141;255;1"),
        ]
        hints = self.color(" ? help  h/l panels  j/k move  / search  c new  p assign  o attach  Enter resume  q quit ", "38;2;238;241;245;48;2;24;31;44")
        left = " ".join(segments)
        usage = self.usage_status_segments()
        body = left + " " + self.color(f" {short_title(message, 34)} ", "38;2;220;224;232;48;2;36;46;64") + " " + hints + usage
        return self.fit_ansi(body, width)

    def render_sidebar(self, height: int, width: int) -> list[str]:
        lines = [self.pane_top(width, "󰏗 Projects", "projects")]
        projects = self.project_counts()
        for project_id, label, count in projects[: max(0, height - 2)]:
            active = project_id == self.project_filter
            icon = "󰄲" if active else "󰄱"
            text = f"{icon} {label}"
            text = f"{self.project_icon(project_id, label)} {label}"
            count_text = str(count)
            usable = max(1, width - len(count_text) - 6)
            line = self.fit(text, usable) + self.color(count_text.rjust(len(count_text)), "38;2;150;158;171")
            if active:
                lines.append(self.pane_row(self.color(self.fit_ansi(line, width - 4), "30;48;2;126;203;255;1"), width))
            else:
                color = "38;2;126;203;255" if project_id == "all" else "38;2;220;224;232"
                lines.append(self.pane_row(self.color(line, color), width))
        lines.extend(self.pane_row("", width) for _ in range(max(0, height - len(lines) - 1)))
        lines.append(self.pane_bottom(width, "projects"))
        return lines[:height]

    def render_session_list(self, height: int, width: int) -> list[str]:
        inner_width = max(1, width - 4)
        list_rows = max(1, height - 4)
        self.ensure_cursor_visible(list_rows)
        lines = [self.pane_top(width, "󰈙 Sessions", "sessions")]
        title_width = max(12, inner_width - 78)
        heading = f"{'#':>3} {'state':12} {'activity':12} {'tokens':9} {'scope':20} {'updated':16} {'title':{title_width}.{title_width}}"
        lines.append(self.pane_row(self.color(heading, "38;2;126;203;255;1"), width))

        if not self.visible:
            msg = "No sessions match. Press x to clear filters, / to search, or [ ] to change project."
            lines.append(self.pane_row(self.color(msg, "38;2;126;203;255"), width))
        else:
            for offset, session in enumerate(self.visible[self.top : self.top + list_rows]):
                index = self.top + offset
                status = session_status(session, self.machines)
                activity_state = session_activity_state(session, self.machines)
                activity = session_activity_label(session, self.machines, self.spinner_index)
                tokens = token_summary_label(session) or "-"
                muted = status == "stale"
                text_color = "38;2;120;128;140;2" if muted else "38;2;238;241;245"
                meta_color = "38;2;105;112;124;2" if muted else "38;2;150;158;171"
                scope_color = "38;2;130;137;148;2" if muted else "38;2;220;224;232"
                where = f"{session.get('project_id', 'uncategorized')}/{session.get('subproject_id', 'default')}"
                title = short_title(str(session.get("generated_title") or session.get("summary") or "Untitled session"), title_width)
                row = (
                    self.color(f"{index + 1:>3}", meta_color)
                    + " "
                    + self.fit_ansi(self.status_badge(status, session), 12)
                    + " "
                    + self.fit_ansi(self.color(activity, self.activity_color(activity_state)), 12)
                    + " "
                    + self.color(f"{tokens:9.9}", meta_color)
                    + " "
                    + self.color(f"{where:20.20}", scope_color)
                    + " "
                    + self.color(f"{format_ts(session.get('updated_at')):16}", meta_color)
                    + " "
                    + self.color(f"{title:{title_width}.{title_width}}", text_color)
                )
                if index == self.cursor:
                    lines.append(self.pane_row(self.color("▌ " + self.fit_ansi(row, inner_width - 2), "30;48;2;126;203;255;1"), width))
                else:
                    lines.append(self.pane_row(row, width))
        lines.extend(self.pane_row("", width) for _ in range(max(0, height - len(lines) - 1)))
        scroll = f"{self.cursor + 1 if self.visible else 0}/{len(self.visible)}"
        footer = "╰" + "─" * max(0, width - len(scroll) - 4) + f" {scroll} ╯"
        lines.append(self.color(self.fit(footer, width), self.pane_color("sessions")))
        return lines[:height]

    def render_detail(self, height: int, width: int) -> list[str]:
        inner_width = max(1, width - 4)
        lines = [self.pane_top(width, "󰋼 Details", "details")]
        if self.focus == "projects":
            project_id = self.project_filter
            if project_id == "all":
                lines.append(self.pane_row(self.color("󰏗 All Projects", "38;2;126;203;255;1"), width))
                lines.extend(self.pane_wrapped_rows(self.color("󰍔 summary".ljust(10), "38;2;150;158;171"), generated_project_summary("all", self.sessions, inner_width) or "Select a project to see its context and active Codex work.", width))
                lines.extend(self.pane_wrapped_rows(self.color("󰈙 sessions".ljust(10), "38;2;150;158;171"), f"{len(self.sessions)} total sessions across {max(0, len(self.project_counts()) - 1)} projects.", width))
            else:
                name = self.names.get(project_id, project_id)
                context = project_context_summary(project_id, inner_width) or "No Markdown context yet. Press c to create/update project context."
                summary = generated_project_summary(project_id, self.sessions, inner_width) or "No sessions currently match this project."
                count = sum(1 for session in self.sessions if session.get("project_id") == project_id)
                lines.append(self.pane_row(self.color(f"󰏗 {name}", "38;2;126;203;255;1"), width))
                lines.extend(self.pane_wrapped_rows(self.color("󰎞 context".ljust(10), "38;2;150;158;171"), context, width))
                lines.extend(self.pane_wrapped_rows(self.color("󰍔 summary".ljust(10), "38;2;150;158;171"), summary, width))
                lines.extend(self.pane_wrapped_rows(self.color("󰈙 sessions".ljust(10), "38;2;150;158;171"), f"{count} sessions assigned or inferred for this project.", width))
            lines = lines[: max(1, height - 1)]
            lines.extend(self.pane_row("", width) for _ in range(max(0, height - len(lines) - 1)))
            lines.append(self.pane_bottom(width, "details"))
            return lines[:height]

        session = self.current()
        if not session:
            lines.append(self.pane_row("No selected session", width))
        else:
            project = session.get("project_id", "uncategorized")
            subproject = session.get("subproject_id", "default")
            status = session_status(session, self.machines)
            activity = session_activity_label(session, self.machines, self.spinner_index)
            token_label = token_summary_label(session) or "none"
            rate_label = rate_summary_label(session) or "none"
            account_label = str(session.get("account_label") or session.get("account_email") or current_account_label(self.machines))
            lines.append(self.pane_row(self.color(f"󰏗 {self.names.get(project, project)}  󰝰 {self.sub_names.get((project, subproject), subproject)}", "38;2;126;203;255;1"), width))
            fields = [
                ("󰌷 id", str(session.get("id"))),
                ("󰄬 status", f"{self.strip_ansi(self.status_badge(status, session))}  {session.get('machine_id', '?')}  {format_ts(session.get('updated_at'))}"),
                ("activity", activity),
                ("account", account_label),
                ("tokens", f"{token_label}; last {compact_number(session.get('last_tokens')) or '0'}; in {compact_number(session.get('input_tokens')) or '0'} cached {compact_number(session.get('cached_input_tokens')) or '0'} out {compact_number(session.get('output_tokens')) or '0'} reason {compact_number(session.get('reasoning_output_tokens')) or '0'}"),
                ("limits", rate_label),
                ("origin", launch_label(session)),
                ("attach", str(session.get("attach_command") or "")),
                ("󰉋 cwd", str(session.get("cwd") or "")),
                (" branch", str(session.get("git_branch") or "")),
                ("󰚩 model", f"{session.get('model') or ''} {session.get('reasoning_effort') or ''}".rstrip()),
                ("󰍔 summary", str(session.get("generated_summary") or session_summary(session, inner_width - 10))),
            ]
            for label, value in fields:
                lines.extend(self.pane_wrapped_rows(self.color(label.ljust(10), "38;2;150;158;171"), value, width))
            context = project_context_summary(str(project), inner_width)
            if context:
                lines.extend(self.pane_wrapped_rows(self.color("󰎞 context".ljust(10), "38;2;150;158;171"), context, width))
            project_summary = generated_project_summary(str(project), self.sessions, inner_width)
            sub_summary = generated_subproject_summary(str(project), str(subproject), self.sessions, inner_width)
            if project_summary:
                lines.extend(self.pane_wrapped_rows(self.color("󰏗 project".ljust(10), "38;2;150;158;171"), project_summary, width))
            if sub_summary:
                lines.extend(self.pane_wrapped_rows(self.color("󰝰 instance".ljust(10), "38;2;150;158;171"), sub_summary, width))
        lines = lines[: max(1, height - 1)]
        lines.extend(self.pane_row("", width) for _ in range(max(0, height - len(lines) - 1)))
        lines.append(self.pane_bottom(width, "details"))
        return lines[:height]

    def render(self) -> str:
        if self.show_help:
            return self.render_help()

        height, width = self.term_size()
        width = max(50, width)
        height = max(12, height)
        detail_height = min(10, max(6, height // 3))
        body_height = max(5, height - detail_height - 3)
        sidebar_width = 28 if width >= 96 else 0
        gap = 1 if sidebar_width else 0
        list_width = width - sidebar_width - gap
        title = self.color(" 󰚩 Codex Dash ", "38;2;238;241;245;48;2;24;31;44;1")
        counts = self.color(f" 󰈙 {len(self.visible)}/{len(self.sessions)} ", "38;2;24;31;44;48;2;126;203;255;1")
        machine = self.color(f"  {machine_id()} ", "38;2;24;31;44;48;2;92;214;144;1")
        chips = self.filter_chips()
        header = title + " " + counts + " " + machine + " " + chips
        rows: list[str] = ["\x1b[?25l\x1b[H" + self.fit_ansi(header, width)]

        list_lines = self.render_session_list(body_height, list_width)
        if sidebar_width:
            sidebar_lines = self.render_sidebar(body_height, sidebar_width)
            for left, right in zip(sidebar_lines, list_lines):
                rows.append(left + " " + right)
        else:
            rows.extend(list_lines)

        rows.extend(self.render_detail(detail_height, width))
        rows.append(self.status_bar(width))
        return "\n".join(rows[:height]) + "\x1b[J"

    def render_help(self) -> str:
        height, width = self.term_size()
        width = max(50, width)
        height = max(12, height)
        body_width = min(width, 96)
        left = 0
        rows = ["\x1b[?25l\x1b[H"]
        top = self.color(self.plain_box(body_width, "? Key Bindings"), "38;2;126;203;255;1")
        pad = " " * left
        rows.append(pad + top)
        current = ""
        for group, keys, description in KEY_BINDINGS:
            if len(rows) >= height - 2:
                break
            if group != current:
                if current and len(rows) < height - 2:
                    rows.append(pad + self.pane_row("", body_width, "38;2;126;203;255"))
                current = group
                rows.append(pad + self.pane_row(self.color(group, "38;2;245;194;96;1"), body_width, "38;2;126;203;255"))
            content = self.color(f"{keys:<18}", "38;2;126;203;255;1") + " " + description
            rows.append(pad + self.pane_row(content, body_width, "38;2;126;203;255"))
        rows.extend(pad + self.pane_row("", body_width, "38;2;126;203;255") for _ in range(max(0, height - len(rows) - 2)))
        rows.append(pad + self.color(self.bottom_box(body_width), "38;2;126;203;255;1"))
        rows.append(self.color(self.fit(" Press ? or Esc to return. q quits. Outside the UI: codex-dash keys", width), "38;2;24;31;44;48;2;126;203;255"))
        return "\n".join(rows[:height]) + "\x1b[J"

    def prompt_search(self) -> None:
        self.search_mode = True
        self.search_buffer = self.query
        self.message = "Search mode"

    def accept_search(self) -> None:
        self.query = self.search_buffer.strip()
        self.last_search = self.query
        self.cursor = 0
        self.top = 0
        self.search_mode = False
        self.apply_filter()
        self.message = f"Filter: {self.query}" if self.query else "Filter cleared"

    def cancel_search(self) -> None:
        self.search_mode = False
        self.search_buffer = ""
        self.message = "Search cancelled"

    def prompt_create_project(self) -> None:
        self.input_mode = "create_id"
        self.input_buffer = ""
        self.input_data = {}
        self.message = "Create project"

    def assign_current_to_project(self) -> None:
        session = self.current()
        if not session:
            self.message = "No session selected"
            return
        project_id = self.project_filter
        if project_id != "all":
            self.finish_assign(project_id)
            return
        self.input_mode = "assign_project"
        self.input_buffer = ""
        self.input_data = {"session_id": session["id"]}
        self.message = "Assign selected session"

    def open_current_attach(self) -> None:
        session = self.current()
        if not session:
            self.message = "No session selected"
            return
        if not session.get("attach_command"):
            self.message = "No attach command recorded; Enter resumes locally"
            return
        self.attach_id = str(session["id"])

    def finish_assign(self, project_id: str) -> None:
        session = self.current()
        if not session:
            self.message = "No session selected"
            return
        project_id = slugify(project_id)
        if not project_id:
            self.message = "Assignment cancelled"
            return
        manifest = load_manifest()
        if not any(str(project.get("id")) == project_id for project in manifest.get("projects", [])):
            append_project_manifest(project_id, project_id)
            ensure_project_context(project_id, project_id)
        data = load_assignments()
        data.setdefault("sessions", {})[session["id"]] = {
            "project_id": project_id,
            "subproject_id": session.get("subproject_id") or "default",
            "assigned_at": now_utc(),
        }
        save_assignments(data)
        self.reload(quiet=True)
        self.project_filter = project_id
        self.apply_filter()
        self.message = f"Assigned session to {project_id}"

    def cancel_input(self) -> None:
        self.input_mode = None
        self.input_buffer = ""
        self.input_data = {}
        self.message = "Input cancelled"

    def accept_input(self) -> None:
        value = self.input_buffer.strip()
        mode = self.input_mode
        if mode == "create_id":
            if not value:
                self.cancel_input()
                return
            self.input_data["project_id"] = slugify(value)
            self.input_mode = "create_name"
            self.input_buffer = value
            self.message = "Project name"
            return
        if mode == "create_name":
            project_id = self.input_data.get("project_id", "project")
            self.input_data["name"] = value or project_id
            self.input_mode = "create_context"
            self.input_buffer = ""
            self.message = "Project context"
            return
        if mode == "create_context":
            project_id = self.input_data.get("project_id", "project")
            name = self.input_data.get("name") or project_id
            append_project_manifest(project_id, name)
            ensure_project_context(project_id, name, value)
            self.input_mode = None
            self.input_buffer = ""
            self.input_data = {}
            self.reload(quiet=True)
            self.project_filter = project_id
            self.cursor = 0
            self.top = 0
            self.apply_filter()
            self.message = f"Created project: {name}"
            return
        if mode == "assign_project":
            self.input_mode = None
            self.input_buffer = ""
            self.input_data = {}
            self.finish_assign(value)
            return

    def handle_input_key(self, key: str) -> bool:
        if not self.input_mode:
            return False
        if key == "\x1b":
            self.cancel_input()
        elif key in ("\r", "\n"):
            self.accept_input()
        elif key in ("\b", "\x7f"):
            self.input_buffer = self.input_buffer[:-1]
        elif len(key) == 1 and key >= " ":
            self.input_buffer += key
        return True

    def cycle_search(self, delta: int) -> None:
        if not self.last_search:
            self.message = "No search yet"
            return
        if self.query != self.last_search:
            self.query = self.last_search
            self.apply_filter()
        self.move(delta)

    def layout_metrics(self) -> dict[str, int]:
        height, width = self.term_size()
        width = max(50, width)
        height = max(12, height)
        detail_height = min(10, max(6, height // 3))
        body_height = max(5, height - detail_height - 3)
        sidebar_width = 28 if width >= 96 else 0
        gap = 1 if sidebar_width else 0
        list_width = width - sidebar_width - gap
        list_rows = max(1, body_height - 4)
        return {
            "height": height,
            "width": width,
            "detail_height": detail_height,
            "body_height": body_height,
            "sidebar_width": sidebar_width,
            "gap": gap,
            "list_width": list_width,
            "list_rows": list_rows,
            "body_y": 2,
            "session_row_y": 4,
            "detail_y": 2 + body_height,
        }

    def handle_mouse(self, x: int, y: int, button: int, released: bool = False) -> None:
        if released:
            return
        metrics = self.layout_metrics()
        sidebar_width = metrics["sidebar_width"]
        body_y = metrics["body_y"]
        body_height = metrics["body_height"]
        session_row_y = metrics["session_row_y"]
        list_rows = metrics["list_rows"]
        list_x = sidebar_width + metrics["gap"] + 1 if sidebar_width else 1

        if sidebar_width and 1 <= x <= sidebar_width and body_y <= y < body_y + body_height:
            self.focus = "projects"
            project_index = y - body_y - 1
            projects = self.project_counts()
            if 0 <= project_index < len(projects):
                self.project_filter = projects[project_index][0]
                self.cursor = 0
                self.top = 0
                self.apply_filter()
                self.message = f"Project: {projects[project_index][1]}"
            return

        if x >= list_x and session_row_y <= y < session_row_y + list_rows:
            self.focus = "sessions"
            row_index = self.top + (y - session_row_y)
            if 0 <= row_index < len(self.visible):
                self.cursor = row_index
                self.message = "Selected session"
            return

        if y >= metrics["detail_y"]:
            self.message = "Details are read-only. Use Tab for Projects/Sessions."

    def read_key(self, timeout: float = 1.0) -> str:
        win_key = self.read_windows_console_input(timeout)
        if win_key:
            return win_key
        import msvcrt

        deadline = time.time() + timeout
        while not msvcrt.kbhit():
            if time.time() >= deadline:
                return "tick"
            time.sleep(0.05)

        key = msvcrt.getwch()
        if key == "\x1b":
            seq = ""
            deadline = time.time() + 0.05
            while time.time() < deadline:
                if msvcrt.kbhit():
                    seq += msvcrt.getwch()
                    deadline = time.time() + 0.01
                elif seq:
                    break
            if seq.startswith("[<"):
                match = re.match(r"\[<(\d+);(\d+);(\d+)([mM])", seq)
                if match:
                    button, x, y, suffix = match.groups()
                    return f"mouse:{button}:{x}:{y}:{suffix == 'm'}"
            if seq in ("[A", "OA"):
                return "up"
            if seq in ("[B", "OB"):
                return "down"
            if seq in ("[D", "OD"):
                return "left"
            if seq in ("[C", "OC"):
                return "right"
            if seq in ("[5~",):
                return "pageup"
            if seq in ("[6~",):
                return "pagedown"
            return "\x1b" if not seq else "\x1b" + seq
        if key in ("\x00", "\xe0"):
            code = msvcrt.getwch()
            return {
                "H": "up",
                "P": "down",
                "K": "left",
                "M": "right",
                "I": "pageup",
                "Q": "pagedown",
            }.get(code, "")
        return key

    @staticmethod
    def read_windows_console_input(timeout: float = 1.0) -> str:
        if platform.system().lower() != "windows":
            return ""
        try:
            import ctypes
            from ctypes import wintypes
        except Exception:
            return ""

        class COORD(ctypes.Structure):
            _fields_ = [("X", ctypes.c_short), ("Y", ctypes.c_short)]

        class KEY_EVENT_RECORD(ctypes.Structure):
            _fields_ = [
                ("bKeyDown", wintypes.BOOL),
                ("wRepeatCount", wintypes.WORD),
                ("wVirtualKeyCode", wintypes.WORD),
                ("wVirtualScanCode", wintypes.WORD),
                ("UnicodeChar", wintypes.WCHAR),
                ("dwControlKeyState", wintypes.DWORD),
            ]

        class MOUSE_EVENT_RECORD(ctypes.Structure):
            _fields_ = [
                ("dwMousePosition", COORD),
                ("dwButtonState", wintypes.DWORD),
                ("dwControlKeyState", wintypes.DWORD),
                ("dwEventFlags", wintypes.DWORD),
            ]

        class EVENT_UNION(ctypes.Union):
            _fields_ = [("KeyEvent", KEY_EVENT_RECORD), ("MouseEvent", MOUSE_EVENT_RECORD)]

        class INPUT_RECORD(ctypes.Structure):
            _fields_ = [("EventType", wintypes.WORD), ("Event", EVENT_UNION)]

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-10)
        record = INPUT_RECORD()
        peek = INPUT_RECORD()
        read = wintypes.DWORD()
        available = wintypes.DWORD()
        deadline = time.time() + timeout
        while True:
            if not kernel32.PeekConsoleInputW(handle, ctypes.byref(peek), 1, ctypes.byref(available)):
                return ""
            if available.value == 0:
                if time.time() >= deadline:
                    return "tick"
                time.sleep(0.05)
                continue
            if not kernel32.ReadConsoleInputW(handle, ctypes.byref(record), 1, ctypes.byref(read)):
                return ""
            if record.EventType == 0x0001:
                event = record.Event.KeyEvent
                if not event.bKeyDown:
                    continue
                nav_key = {
                    0x26: "up",
                    0x28: "down",
                    0x25: "left",
                    0x27: "right",
                    0x21: "pageup",
                    0x22: "pagedown",
                    0x09: "\t",
                    0x1B: "\x1b",
                }.get(event.wVirtualKeyCode, "")
                if nav_key:
                    return nav_key
                char = event.UnicodeChar
                if char:
                    return char
                return ""
            if record.EventType == 0x0002:
                event = record.Event.MouseEvent
                if event.dwButtonState:
                    return f"mouse:0:{int(event.dwMousePosition.X) + 1}:{int(event.dwMousePosition.Y) + 1}:False"

    @staticmethod
    def enable_windows_console_input() -> int | None:
        if platform.system().lower() != "windows":
            return None
        try:
            import ctypes
        except Exception:
            return None
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-10)
        mode = ctypes.c_uint()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return None
        original = int(mode.value)
        enable_mouse_input = 0x0010
        enable_window_input = 0x0008
        enable_extended_flags = 0x0080
        enable_line_input = 0x0002
        enable_echo_input = 0x0004
        enable_quick_edit = 0x0040
        new_mode = original | enable_mouse_input | enable_window_input | enable_extended_flags
        new_mode &= ~enable_quick_edit
        new_mode &= ~enable_line_input
        new_mode &= ~enable_echo_input
        kernel32.SetConsoleMode(handle, new_mode)
        return original

    @staticmethod
    def restore_windows_console_input(mode: int | None) -> None:
        if mode is None or platform.system().lower() != "windows":
            return
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleMode(ctypes.windll.kernel32.GetStdHandle(-10), mode)
        except Exception:
            pass

    def run(self) -> None:
        if platform.system().lower() == "windows":
            os.system("")
        self.reload()
        self.start_background_refresh(force=True)
        pending_g = False
        original_input_mode = self.enable_windows_console_input()
        print("\x1b[?1049h\x1b[?1000h\x1b[?1002h\x1b[?1006h\x1b[2J\x1b[H", end="")
        try:
            while True:
                self.finish_background_refresh()
                height, _ = self.term_size()
                detail_height = min(9, max(5, height // 3))
                list_height = max(5, height - detail_height - 6)
                print(self.render(), end="", flush=True)
                key = self.read_key(timeout=0.2)
                if key == "tick":
                    self.spinner_index = (self.spinner_index + 1) % len(SPINNER_FRAMES)
                    if not self.search_mode and not self.input_mode and not self.show_help:
                        self.auto_refresh()
                    continue
                if key.startswith("mouse:"):
                    _, button, x, y, released = key.split(":")
                    if self.show_help:
                        self.show_help = False
                        continue
                    self.handle_mouse(int(x), int(y), int(button), released == "True")
                    continue
                if self.handle_input_key(key):
                    continue
                if self.search_mode:
                    if key in ("\x1b",):
                        self.cancel_search()
                    elif key in ("\r", "\n"):
                        self.accept_search()
                    elif key in ("\b", "\x7f"):
                        self.search_buffer = self.search_buffer[:-1]
                    elif len(key) == 1 and key >= " ":
                        self.search_buffer += key
                    continue
                if self.show_help:
                    if key == "q":
                        return
                    self.show_help = False
                    continue
                if pending_g:
                    if key == "g":
                        self.cursor = 0
                    pending_g = False
                    continue
                if key in ("q", "\x1b"):
                    return
                if key == "?":
                    self.show_help = True
                if key in ("j", "down"):
                    self.vertical_move(1)
                elif key in ("k", "up"):
                    self.vertical_move(-1)
                elif key == "\t":
                    self.cycle_focus(1)
                elif key == "\x1b[Z":
                    self.cycle_focus(-1)
                elif key in ("h", "left"):
                    self.move_focus("projects")
                elif key in ("l", "right"):
                    self.move_focus("sessions")
                elif key == "H" and self.visible:
                    self.cursor = self.top
                elif key == "M" and self.visible:
                    self.cursor = min(len(self.visible) - 1, self.top + max(0, list_height // 2))
                elif key == "L" and self.visible:
                    self.cursor = min(len(self.visible) - 1, self.top + max(0, list_height - 4))
                elif key in ("0", "g"):
                    pending_g = key == "g"
                    if key == "0":
                        self.cursor = 0
                elif key in ("$", "G"):
                    self.cursor = max(0, len(self.visible) - 1)
                elif key in ("pagedown", "\x06"):
                    self.move(list_height)
                elif key in ("pageup", "\x02"):
                    self.move(-list_height)
                elif key == "\x04":
                    self.move(max(1, list_height // 2))
                elif key == "\x15":
                    self.move(-max(1, list_height // 2))
                elif key == "\x05":
                    self.move(1)
                elif key == "\x19":
                    self.move(-1)
                elif key == "/":
                    self.prompt_search()
                elif key == "n":
                    self.cycle_search(1)
                elif key == "N":
                    self.cycle_search(-1)
                elif key == "]":
                    self.cycle_project(1)
                elif key == "[":
                    self.cycle_project(-1)
                elif key == "s":
                    self.cycle_status(1)
                elif key == "S":
                    self.cycle_sort()
                elif key == "c":
                    self.prompt_create_project()
                elif key == "p":
                    self.assign_current_to_project()
                elif key == "o":
                    self.open_current_attach()
                    if self.attach_id:
                        return
                elif key == "a":
                    self.project_filter = "all"
                    self.status_filter = "all"
                    self.cursor = 0
                    self.top = 0
                    self.apply_filter()
                    self.message = "Filters: all projects, all statuses"
                elif key == "x":
                    self.query = ""
                    self.project_filter = "all"
                    self.status_filter = "all"
                    self.cursor = 0
                    self.top = 0
                    self.apply_filter()
                    self.message = "Filters cleared"
                elif key == "r":
                    self.start_background_refresh(force=True)
                elif key in ("\r", "\n"):
                    session = self.current()
                    if session:
                        self.resume_id = session["id"]
                        return
        finally:
            print("\x1b[?1006l\x1b[?1002l\x1b[?1000l\x1b[?1049l\x1b[?25h", end="", flush=True)
            self.restore_windows_console_input(original_input_mode)


def run_ansi_dashboard(args: argparse.Namespace) -> None:
    app = AnsiDashboardApp(args)
    app.run()
    if app.attach_id:
        args.session_id = app.attach_id
        command_attach(args)
    if app.resume_id:
        args.session_id = app.resume_id
        command_resume(args)


def command_dashboard(args: argparse.Namespace) -> None:
    if args.plain:
        refresh_export_quiet(int(getattr(args, "limit", 500) or 500))
        args.per_group = args.per_group or 20
        command_list(args)
        return
    try:
        import curses
    except ImportError as exc:
        if platform.system().lower() == "windows":
            run_ansi_dashboard(args)
            return
        print(f"Terminal UI is unavailable: {exc}")
        command_list(args)
        return

    app: DashboardApp | None = None

    def run_app(stdscr: Any) -> None:
        nonlocal app
        app = DashboardApp(stdscr, args)
        app.run()

    try:
        curses.wrapper(run_app)
    except Exception as exc:
        if getattr(args, "tui", False):
            raise
        print(f"Terminal UI is unavailable, falling back to plain output: {exc}", file=sys.stderr)
        args.per_group = args.per_group or 20
        command_list(args)
        return
    if app and app.resume_id:
        args.session_id = app.resume_id
        command_resume(args)


def find_session_by_prefix(session_id: str) -> dict[str, Any]:
    target = None
    for session in all_sessions():
        if session["id"].startswith(session_id):
            if target is not None:
                raise SystemExit(f"Ambiguous session prefix: {session_id}")
            target = session
    if target is None:
        raise SystemExit(f"Session not found in board export: {session_id}")
    return target


def run_shell_command(command: str) -> int:
    if platform.system().lower() == "windows":
        return subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command]).returncode
    return subprocess.run(command, shell=True).returncode


def command_attach(args: argparse.Namespace) -> None:
    target = find_session_by_prefix(args.session_id)
    attach_command = str(target.get("attach_command") or "").strip()
    if not attach_command:
        print("No attach command is recorded for this session.")
        print("Use `codex-dash resume <id>` for local resume, or launch future sessions with `codex-dash launch --attach-command ... -- ...`.")
        raise SystemExit(2)
    print(f"Attaching: {attach_command}")
    raise SystemExit(run_shell_command(attach_command))


def command_resume(args: argparse.Namespace) -> None:
    target = find_session_by_prefix(args.session_id)

    cwd = target.get("cwd") or str(Path.home())
    cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command"]
    codex_cmd = f"codex resume --cd {json.dumps(cwd)} {json.dumps(target['id'])}"
    print(f"Running: {codex_cmd}")
    raise SystemExit(subprocess.run(cmd + [codex_cmd]).returncode)


def newest_thread_after(known_ids: set[str], started_at: int) -> dict[str, Any] | None:
    candidates = []
    for thread in read_threads(limit=10):
        thread_id = str(thread.get("id") or "")
        if not thread_id or thread_id in known_ids:
            continue
        created_at = int(thread.get("created_at") or 0)
        updated_at = int(thread.get("updated_at") or 0)
        if updated_at >= started_at - 5 or created_at >= started_at - 5:
            candidates.append(thread)
    if not candidates:
        return None
    candidates.sort(key=lambda row: int(row.get("updated_at") or row.get("created_at") or 0), reverse=True)
    return candidates[0]


def record_launch_metadata(thread: dict[str, Any], metadata: dict[str, str], command: list[str]) -> bool:
    if not thread.get("id"):
        return False
    launches = load_launches()
    sessions = launches.setdefault("sessions", {})
    sessions[str(thread["id"])] = {
        **metadata,
        "recorded_at": now_utc(),
        "host_machine_id": machine_id(),
        "command": command,
    }
    save_launches(launches)
    print(f"Recorded launch metadata for {thread['id']}: {launch_label(sessions[str(thread['id'])])}", flush=True)
    return True


def command_launch(args: argparse.Namespace) -> None:
    command = list(getattr(args, "command", []) or [])
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        raise SystemExit("Usage: codex-dash launch [--origin ssh|tmux|local] [--attach-command CMD] -- codex")
    known_ids = {str(thread.get("id")) for thread in read_threads(limit=1000) if thread.get("id")}
    started_at = int(time.time())
    metadata = detect_launch_origin(args)
    env = os.environ.copy()
    env.update(
        {
            "CODEX_DASH_ORIGIN": metadata["launch_origin"],
            "CODEX_DASH_ORIGIN_HINT": metadata["origin_hint"],
            "CODEX_DASH_TMUX_SESSION": metadata["tmux_session"],
            "CODEX_DASH_ATTACH_COMMAND": metadata["attach_command"],
        }
    )
    print(f"Launching: {' '.join(command)}", flush=True)
    process = subprocess.Popen(command, env=env)
    recorded = False
    while process.poll() is None:
        if not recorded:
            thread = newest_thread_after(known_ids, started_at)
            if thread:
                try:
                    recorded = record_launch_metadata(thread, metadata, command)
                except Exception as exc:
                    print(f"Warning: could not record launch metadata: {exc}", file=sys.stderr, flush=True)
                    recorded = True
        time.sleep(1)
    if not recorded:
        thread = newest_thread_after(known_ids, started_at)
        if thread:
            try:
                record_launch_metadata(thread, metadata, command)
            except Exception as exc:
                print(f"Warning: could not record launch metadata: {exc}", file=sys.stderr, flush=True)
    raise SystemExit(process.returncode)


def command_sync(args: argparse.Namespace) -> None:
    raise SystemExit(0 if sync_state_once(args) else 1)


def command_watch(args: argparse.Namespace) -> None:
    interval = max(1.0, float(getattr(args, "interval", 1.0) or 1.0))
    debounce = max(0.1, float(getattr(args, "debounce", 0.75) or 0.75))
    heartbeat = max(2.0, float(getattr(args, "heartbeat", 5.0) or 5.0))
    sync_interval = max(0.0, float(getattr(args, "sync_interval", 10.0) or 0.0))
    quiet = bool(getattr(args, "quiet", False))
    targets = [str(target).strip() for target in getattr(args, "targets", []) if str(target).strip()]
    configured_peers = sync_peers_from_args(args)

    last_signature: tuple[int, int, int, int, int] | None = None
    pending_since: float | None = None
    last_refresh = 0.0
    last_sync = 0.0

    def refresh(reason: str) -> None:
        nonlocal last_refresh
        ok, error = refresh_export_quiet(int(getattr(args, "limit", 500) or 500))
        last_refresh = time.time()
        if not quiet:
            if ok:
                print(f"{dt.datetime.now().strftime('%H:%M:%S')} refreshed ({reason})", flush=True)
            else:
                print(f"{dt.datetime.now().strftime('%H:%M:%S')} refresh failed: {error}", file=sys.stderr, flush=True)

    refresh("startup")
    last_signature = codex_state_signature()

    while True:
        now = time.time()
        signature = codex_state_signature()
        if signature != last_signature:
            last_signature = signature
            pending_since = now

        if pending_since is not None and now - pending_since >= debounce:
            refresh("codex state changed")
            pending_since = None

        if now - last_refresh >= heartbeat:
            refresh("heartbeat")

        if configured_peers and sync_interval > 0 and now - last_sync >= sync_interval:
            sync_args = argparse.Namespace(
                targets=targets,
                direction=getattr(args, "direction", "both"),
                remote_board_path=getattr(args, "remote_board_path", "~/.codex/instance-board"),
                remote_codex_home=getattr(args, "remote_codex_home", ""),
                local_board_path=getattr(args, "local_board_path", ""),
                local_codex_home=getattr(args, "local_codex_home", ""),
                skip_local_refresh=True,
                skip_remote_refresh=getattr(args, "skip_remote_refresh", False),
                limit=getattr(args, "limit", 500),
                quiet=quiet,
            )
            synced = sync_state_once(sync_args)
            last_sync = now
            if not quiet:
                peer_labels = [peer["target"] for peer in configured_peers]
                label = "synced" if synced else "sync had errors"
                print(f"{dt.datetime.now().strftime('%H:%M:%S')} {label}: {', '.join(peer_labels)}", flush=True)

        time.sleep(interval)


def command_where(_: argparse.Namespace) -> None:
    print(f"BOARD_HOME={BOARD_HOME}")
    print(f"CODEX_HOME={CODEX_HOME}")
    print(f"MANIFEST={MANIFEST_PATH}")
    print(f"PEERS={PEERS_PATH}")
    print(f"MACHINE_ID={machine_id()}")
    account = load_current_account()
    print(f"ACCOUNT={account.get('label')}")
    print(f"AUTH_MODE={account.get('auth_mode')}")
    if account.get("plan_type"):
        print(f"PLAN={account.get('plan_type')}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="codex-board",
        epilog="Inside the dashboard, press ? for key bindings. Outside it, run: codex-dash keys",
    )
    parser.add_argument("--plain", action="store_true", help="Print the non-interactive dashboard")
    parser.add_argument("--tui", action="store_true", help="Force the interactive terminal dashboard")
    parser.add_argument("--per-group", type=int, default=20, help="Plain output sessions per subproject")
    parser.add_argument("--limit", type=int, default=500, help="Refresh limit used by the dashboard")
    parser.add_argument("--auto-refresh", type=int, default=10, help="TUI auto-refresh interval in seconds; 0 disables it")
    sub = parser.add_subparsers(dest="command")

    dashboard = sub.add_parser("dashboard", help="Open the interactive terminal dashboard")
    dashboard.add_argument("--plain", action="store_true", help="Print the non-interactive dashboard")
    dashboard.add_argument("--tui", action="store_true", help="Force the interactive terminal dashboard")
    dashboard.add_argument("--per-group", type=int, default=20, help="Plain output sessions per subproject")
    dashboard.add_argument("--limit", type=int, default=500, help="Refresh limit")
    dashboard.add_argument("--auto-refresh", type=int, default=10, help="TUI auto-refresh interval in seconds; 0 disables it")
    dashboard.set_defaults(func=command_dashboard)

    refresh = sub.add_parser("refresh", help="Export this machine's Codex session index and heartbeat")
    refresh.add_argument("--limit", type=int, default=500, help="Maximum local Codex threads to export")
    refresh.add_argument("--quiet", action="store_true", help="Suppress export summary output")
    refresh.set_defaults(func=export_state)

    listing = sub.add_parser("list", help="List projects, subprojects, and sessions")
    listing.add_argument("--per-group", type=int, default=10, help="Maximum sessions to show per subproject")
    listing.set_defaults(func=command_list)

    keys = sub.add_parser("keys", help="Print dashboard key bindings")
    keys.set_defaults(func=command_keys)

    project = sub.add_parser("project", help="Manage dashboard projects")
    project_sub = project.add_subparsers(dest="project_command")
    project.set_defaults(func=command_project)
    project_list = project_sub.add_parser("list", help="List projects")
    project_list.set_defaults(func=command_project)
    project_add = project_sub.add_parser("add", help="Create a project and context file")
    project_add.add_argument("id")
    project_add.add_argument("--name", default="")
    project_add.add_argument("--root", default="")
    project_add.add_argument("--context", default="")
    project_add.set_defaults(func=command_project)

    assign = sub.add_parser("assign", help="Assign a session to a project")
    assign.add_argument("session_id", help="Full session id or unique prefix")
    assign.add_argument("project_id")
    assign.add_argument("--subproject-id", default="default")
    assign.add_argument("--name", default="", help="Name to use if the project needs to be created")
    assign.set_defaults(func=command_assign)

    pick = sub.add_parser("pick", help="Interactively pick a session to resume")
    pick.add_argument("--limit", type=int, default=30, help="Maximum sessions to show")
    pick.set_defaults(func=command_pick)

    resume = sub.add_parser("resume", help="Resume a session by full id or unique prefix")
    resume.add_argument("session_id")
    resume.set_defaults(func=command_resume)

    attach = sub.add_parser("attach", help="Run the recorded attach command for a tmux/SSH session")
    attach.add_argument("session_id")
    attach.set_defaults(func=command_attach)

    launch = sub.add_parser("launch", help="Launch a Codex command and record origin/attach metadata when it exits")
    launch.add_argument("--origin", choices=["local", "ssh", "tmux", "remote"], default="")
    launch.add_argument("--origin-hint", default="", help="Machine, SSH client, or other source label")
    launch.add_argument("--tmux-session", default="", help="tmux session/window/pane label to show in the dashboard")
    launch.add_argument("--attach-command", default="", help="Command used later by `codex-dash attach` or TUI key `o`")
    launch.add_argument("command", nargs=argparse.REMAINDER, help="Command to run, usually after --")
    launch.set_defaults(func=command_launch)

    sync = sub.add_parser("sync", help="Refresh and sync pooled board JSON with other machines")
    sync.add_argument("targets", nargs="*", help="SSH targets to sync with; defaults to peers.json")
    sync.add_argument("--direction", choices=["push", "pull", "both"], default="both")
    sync.add_argument("--remote-board-path", default="~/.codex/instance-board")
    sync.add_argument("--remote-codex-home", default="", help="CODEX_HOME to use during remote refresh")
    sync.add_argument("--local-board-path", default="", help="Local board path to read/write during sync")
    sync.add_argument("--local-codex-home", default="", help="Local CODEX_HOME to use during sync refresh")
    sync.add_argument("--skip-local-refresh", action="store_true")
    sync.add_argument("--skip-remote-refresh", action="store_true")
    sync.add_argument("--limit", type=int, default=500)
    sync.add_argument("--quiet", action="store_true")
    sync.set_defaults(func=command_sync)

    watch = sub.add_parser("watch", help="Watch local Codex state and optionally sync remote board JSON")
    watch.add_argument("--interval", type=float, default=1.0, help="Polling interval for local Codex state")
    watch.add_argument("--debounce", type=float, default=0.75, help="Delay after a file change before refreshing")
    watch.add_argument("--heartbeat", type=float, default=5.0, help="Refresh at least this often, even without file changes")
    watch.add_argument("--limit", type=int, default=500)
    watch.add_argument("--sync-target", dest="targets", action="append", default=[], help="SSH target to sync; repeat for multiple machines")
    watch.add_argument("--sync-interval", type=float, default=10.0, help="Seconds between sync attempts when targets are configured")
    watch.add_argument("--direction", choices=["push", "pull", "both"], default="both")
    watch.add_argument("--remote-board-path", default="~/.codex/instance-board")
    watch.add_argument("--remote-codex-home", default="", help="CODEX_HOME to use during remote refresh")
    watch.add_argument("--local-board-path", default="", help="Local board path to read/write during sync")
    watch.add_argument("--local-codex-home", default="", help="Local CODEX_HOME to use during sync refresh")
    watch.add_argument("--skip-remote-refresh", action="store_true")
    watch.add_argument("--quiet", action="store_true")
    watch.set_defaults(func=command_watch)

    where = sub.add_parser("where", help="Print paths and machine identity")
    where.set_defaults(func=command_where)

    parser.set_defaults(func=command_dashboard)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
