from __future__ import annotations

import argparse
import base64
import ctypes
import hashlib
import json
import os
import platform
import queue
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

VERSION = "0.1.0"
CHUNK_BYTES = 256 * 1024
MAX_FILE_BYTES = 256 * 1024 * 1024
MCP_VERSION = "2025-06-18"
ROOT = Path(os.environ.get("COMPUTER_BRIDGE_HOME", Path.home() / ".computer-bridge")).expanduser()
DB_PATH = ROOT / "state.sqlite3"
JOBS_ROOT = ROOT / "jobs"
FILES_ROOT = ROOT / "files"
ACTIVE: dict[str, subprocess.Popen[bytes]] = {}
CANCELLED: set[str] = set()
STOP = threading.Event()


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect_db() -> sqlite3.Connection:
    ROOT.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS jobs (
            job_id TEXT PRIMARY KEY,
            request_id TEXT UNIQUE NOT NULL,
            operation TEXT NOT NULL,
            arguments TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            result TEXT,
            error TEXT,
            pid INTEGER
        );
        CREATE TABLE IF NOT EXISTS uploads (
            upload_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            total_chunks INTEGER NOT NULL,
            expected_sha256 TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS upload_chunks (
            upload_id TEXT NOT NULL REFERENCES uploads(upload_id) ON DELETE CASCADE,
            chunk_index INTEGER NOT NULL,
            data BLOB NOT NULL,
            PRIMARY KEY(upload_id, chunk_index)
        );
        """
    )
    return connection


def json_result(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def valid_identifier(value: str, label: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 128:
        raise ValueError(f"{label} must be a non-empty string of at most 128 characters")
    if any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for character in value):
        raise ValueError(f"{label} contains unsupported characters")
    return value


def local_capabilities() -> dict[str, Any]:
    winapp = shutil.which("winapp") or shutil.which("winapp.exe") or str(Path.home() / ".codex" / "computer-bridge" / "bin" / "winapp.exe")
    if os.name == "nt" and not Path(winapp).is_file():
        winapp = None
    peekaboo = shutil.which("peekaboo")
    if not peekaboo and platform.system() == "Darwin":
        candidate = Path.home() / ".local" / "bin" / "peekaboo"
        peekaboo = str(candidate) if candidate.is_file() else None
    wps = False
    if os.name == "nt":
        process = subprocess.run(["tasklist.exe", "/FO", "CSV", "/NH", "/FI", "IMAGENAME eq wps.exe"], capture_output=True, text=True, check=False)
        wps = "wps.exe" in process.stdout.lower()
    desktop_apps = {
        "windows": {"winapp": winapp, "wps": wps},
        "mac": {"peekaboo": peekaboo, "textedit": platform.system() == "Darwin"},
    }
    interactive_session = True
    if os.name == "nt":
        current_session = windows_session_id(os.getpid())
        interactive_session = bool(current_session) or interactive_worker_available()
    return {
        "version": VERSION,
        "host": platform.node(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "operations": ["system.info", "file.list", "file.read", "file.write", "file.put", "file.get", "python.run", "desktop.inspect", "desktop.screenshot", "desktop.open", "desktop.type", "desktop.click"],
        "desktop": desktop_apps["windows" if os.name == "nt" else "mac"],
        "data_root": str(ROOT),
        "interactive_session": interactive_session,
    }


def peer_call(alias: str, peer_name: str, method: str, params: dict[str, Any]) -> Any:
    alias = valid_identifier(alias, "SSH alias")
    payload = json_result({"method": method, "params": params}) + "\n"
    request_path: Path | None = None
    if os.name == "nt":
        request_id = uuid.uuid4().hex
        local_spool = ROOT / "rpc-out"
        local_spool.mkdir(parents=True, exist_ok=True)
        request_path = local_spool / f"{request_id}.json"
        request_path.write_text(payload, encoding="utf-8")
        remote_name = f"{request_id}.json"
        try:
            transfer = subprocess.run(
                ["scp", "-q", "-B", "-o", "BatchMode=yes", str(request_path), f"{alias}:~/.computer-bridge/rpc-in/{remote_name}"],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=45,
                check=False,
            )
            if transfer.returncode:
                raise RuntimeError(f"SCP to {alias} failed ({transfer.returncode}): {transfer.stderr[-1200:].strip()}")
            remote_command = f"~/.local/bin/computer_bridge rpc --request-file ~/.computer-bridge/rpc-in/{remote_name}"
            with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
                process = subprocess.run(
                    ["ssh", "-n", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "-o", "ServerAliveInterval=10", alias, remote_command],
                    stdin=subprocess.DEVNULL,
                    stdout=stdout_file,
                    stderr=stderr_file,
                    timeout=300,
                    check=False,
                )
                stdout_file.seek(0)
                stderr_file.seek(0)
                process.stdout = stdout_file.read().decode("utf-8", errors="replace")
                process.stderr = stderr_file.read().decode("utf-8", errors="replace")
        finally:
            request_path.unlink(missing_ok=True)
    else:
        remote_command = '"%USERPROFILE%\\.codex\\computer-bridge\\computer_bridge.cmd" rpc' if peer_name == "windows" else "~/.local/bin/computer_bridge rpc"
        process = subprocess.run(
            ["ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "-o", "ServerAliveInterval=10", alias, remote_command],
            input=payload,
            text=True,
            capture_output=True,
            timeout=300,
            check=False,
        )
    if process.returncode:
        raise RuntimeError(f"SSH to {alias} failed ({process.returncode}): {process.stderr[-1200:].strip()}")
    lines = [line for line in process.stdout.splitlines() if line.strip()]
    if not lines:
        raise RuntimeError(f"SSH to {alias} returned no result: {process.stderr[-1200:].strip()}")
    response = json.loads(lines[-1])
    if not response.get("ok"):
        raise RuntimeError(response.get("error", "Remote operation failed"))
    return response.get("result")


def submit(request_id: str, operation: str, arguments: dict[str, Any]) -> dict[str, Any]:
    request_id = valid_identifier(request_id, "request_id")
    if operation not in {"system.info", "file.list", "file.read", "file.write", "file.put", "file.get", "python.run", "desktop.inspect", "desktop.screenshot", "desktop.open", "desktop.type", "desktop.click"}:
        raise ValueError(f"Unsupported operation: {operation}")
    if not isinstance(arguments, dict):
        raise ValueError("arguments must be an object")
    now = utcnow()
    job_id = uuid.uuid4().hex
    encoded_args = json_result(arguments)
    with connect_db() as db:
        existing = db.execute("SELECT * FROM jobs WHERE request_id=?", (request_id,)).fetchone()
        if existing:
            if existing["operation"] != operation or existing["arguments"] != encoded_args:
                raise ValueError("request_id already exists with different operation or arguments")
            return job_public(existing)
        db.execute(
            "INSERT INTO jobs(job_id,request_id,operation,arguments,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
            (job_id, request_id, operation, encoded_args, "queued", now, now),
        )
    return {"job_id": job_id, "request_id": request_id, "operation": operation, "status": "queued", "created_at": now}


def job_public(row: sqlite3.Row) -> dict[str, Any]:
    result = {"job_id": row["job_id"], "request_id": row["request_id"], "operation": row["operation"], "status": row["status"], "created_at": row["created_at"], "updated_at": row["updated_at"]}
    if row["result"] is not None:
        result["result"] = json.loads(row["result"])
    if row["error"]:
        result["error"] = row["error"]
    return result


def get_job(job_id: str) -> dict[str, Any]:
    job_id = valid_identifier(job_id, "job_id")
    with connect_db() as db:
        row = db.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
    if not row:
        raise FileNotFoundError(f"Unknown job_id: {job_id}")
    return job_public(row)


def cancel_job(job_id: str) -> dict[str, Any]:
    job_id = valid_identifier(job_id, "job_id")
    with connect_db() as db:
        row = db.execute("SELECT status FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        if not row:
            raise FileNotFoundError(f"Unknown job_id: {job_id}")
        if row["status"] == "queued":
            db.execute("UPDATE jobs SET status='cancelled',updated_at=? WHERE job_id=?", (utcnow(), job_id))
        elif row["status"] == "running":
            CANCELLED.add(job_id)
            process = ACTIVE.get(job_id)
            if process and process.poll() is None:
                process.terminate()
            db.execute("UPDATE jobs SET status='cancelled',updated_at=? WHERE job_id=?", (utcnow(), job_id))
        return get_job(job_id)


def safe_path(value: str) -> Path:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValueError("path must be a non-empty relative path")
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("path must stay within the computer-bridge files directory")
    base = FILES_ROOT.resolve()
    path = (base / relative).resolve()
    if path != base and base not in path.parents:
        raise ValueError("path must stay within the computer-bridge files directory")
    return path


def perform(operation: str, arguments: dict[str, Any], job_id: str) -> Any:
    job_directory = JOBS_ROOT / job_id
    job_directory.mkdir(parents=True, exist_ok=True)
    if operation == "system.info":
        return local_capabilities()
    if operation == "file.list":
        path = safe_path(arguments.get("path", "."))
        return [{"name": child.name, "directory": child.is_dir(), "size": child.stat().st_size if child.is_file() else None} for child in sorted(path.iterdir(), key=lambda item: item.name)]
    if operation == "file.read":
        path = safe_path(arguments.get("path", ""))
        maximum = int(arguments.get("max_bytes", MAX_FILE_BYTES))
        if maximum < 1 or maximum > MAX_FILE_BYTES:
            raise ValueError(f"max_bytes must be between 1 and {MAX_FILE_BYTES}")
        data = path.read_bytes()
        if len(data) > maximum:
            raise ValueError(f"file exceeds max_bytes ({len(data)} bytes)")
        return {"path": arguments["path"], "size": len(data), "sha256": hashlib.sha256(data).hexdigest(), "data_base64": base64.b64encode(data).decode("ascii")}
    if operation == "file.write":
        path = safe_path(arguments.get("path", ""))
        content = arguments.get("data_base64")
        if not isinstance(content, str):
            raise ValueError("data_base64 is required")
        data = base64.b64decode(content, validate=True)
        if len(data) > MAX_FILE_BYTES:
            raise ValueError(f"file exceeds {MAX_FILE_BYTES} bytes")
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".partial")
        temporary.write_bytes(data)
        os.replace(temporary, path)
        return {"path": arguments["path"], "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    if operation == "file.put":
        upload_id = valid_identifier(arguments.get("upload_id", ""), "upload_id")
        index = int(arguments.get("chunk_index", -1))
        total = int(arguments.get("total_chunks", 0))
        if total < 1 or total > (MAX_FILE_BYTES + CHUNK_BYTES - 1) // CHUNK_BYTES or index < 0 or index >= total:
            raise ValueError("Invalid chunk index or total_chunks")
        content = base64.b64decode(arguments.get("data_base64", ""), validate=True)
        if len(content) > CHUNK_BYTES:
            raise ValueError(f"chunk exceeds {CHUNK_BYTES} bytes")
        name = str(arguments.get("name", ""))
        expected = str(arguments.get("sha256", ""))
        if len(expected) != 64 or any(char not in "0123456789abcdefABCDEF" for char in expected):
            raise ValueError("sha256 must be a 64-character hexadecimal digest")
        with connect_db() as db:
            existing = db.execute("SELECT * FROM uploads WHERE upload_id=?", (upload_id,)).fetchone()
            if existing and (existing["name"] != name or existing["total_chunks"] != total or existing["expected_sha256"] != expected):
                raise ValueError("upload_id already exists with different metadata")
            if not existing:
                db.execute("INSERT INTO uploads VALUES(?,?,?,?,?)", (upload_id, name, total, expected, utcnow()))
            db.execute("INSERT OR IGNORE INTO upload_chunks VALUES(?,?,?)", (upload_id, index, content))
            received = db.execute("SELECT COUNT(*) FROM upload_chunks WHERE upload_id=?", (upload_id,)).fetchone()[0]
            if received < total:
                return {"upload_id": upload_id, "received_chunks": received, "total_chunks": total, "status": "uploading"}
            chunks = db.execute("SELECT data FROM upload_chunks WHERE upload_id=? ORDER BY chunk_index", (upload_id,)).fetchall()
            data = b"".join(bytes(chunk[0]) for chunk in chunks)
            digest = hashlib.sha256(data).hexdigest()
            if digest.lower() != expected.lower():
                db.execute("DELETE FROM uploads WHERE upload_id=?", (upload_id,))
                raise ValueError("SHA-256 mismatch after upload")
            path = safe_path(name)
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(path.name + ".partial")
            temporary.write_bytes(data)
            os.replace(temporary, path)
            db.execute("DELETE FROM uploads WHERE upload_id=?", (upload_id,))
        return {"upload_id": upload_id, "path": name, "size": len(data), "sha256": digest, "status": "complete"}
    if operation == "file.get":
        path = safe_path(arguments.get("path", ""))
        data = path.read_bytes()
        total = max(1, (len(data) + CHUNK_BYTES - 1) // CHUNK_BYTES)
        index = int(arguments.get("chunk_index", 0))
        if index < 0 or index >= total:
            raise ValueError("chunk_index is outside the file")
        start = index * CHUNK_BYTES
        return {"path": arguments["path"], "size": len(data), "sha256": hashlib.sha256(data).hexdigest(), "chunk_index": index, "total_chunks": total, "data_base64": base64.b64encode(data[start:start + CHUNK_BYTES]).decode("ascii")}
    if operation == "python.run":
        script = arguments.get("script")
        if not isinstance(script, str) or len(script.encode("utf-8")) > 1024 * 1024:
            raise ValueError("script must be UTF-8 text no larger than 1 MiB")
        filename = job_directory / "task.py"
        filename.write_text(script, encoding="utf-8")
        timeout = max(1, min(int(arguments.get("timeout_seconds", 300)), 3600))
        process = run_command([sys.executable, str(filename)], job_directory, timeout, job_id)
        result = {"return_code": process.returncode, "stdout": process.stdout[-30000:], "stderr": process.stderr[-30000:], "working_directory": str(job_directory)}
        if process.returncode:
            raise RuntimeError(json_result(result))
        return result
    if operation.startswith("desktop."):
        return desktop(operation, arguments, job_id)
    raise ValueError(f"Unsupported operation: {operation}")


def run_command(command: list[str], cwd: Path, timeout: int, job_id: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.setdefault("PYTHONUTF8", "1")
    process = subprocess.Popen(command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", env=environment)
    ACTIVE[job_id] = process
    deadline = time.monotonic() + timeout
    try:
        while True:
            if job_id in CANCELLED:
                process.terminate()
                process.communicate(timeout=5)
                raise RuntimeError("Job cancelled")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                process.kill()
                stdout, stderr = process.communicate()
                raise subprocess.TimeoutExpired(command, timeout, output=stdout, stderr=stderr)
            try:
                stdout, stderr = process.communicate(timeout=min(0.25, remaining))
                return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
            except subprocess.TimeoutExpired:
                continue
    finally:
        ACTIVE.pop(job_id, None)


def desktop(operation: str, arguments: dict[str, Any], job_id: str) -> dict[str, Any]:
    if os.name == "nt":
        binary = shutil.which("winapp") or shutil.which("winapp.exe") or str(Path.home() / ".codex" / "computer-bridge" / "bin" / "winapp.exe")
        if not Path(binary).is_file():
            binary = ""
        if not binary:
            raise RuntimeError("winapp is unavailable; desktop capabilities are not installed")
        app = str(arguments.get("app", "notepad"))
        screenshot_path = JOBS_ROOT / job_id / "screenshot.png"
        allowed = {"inspect": ["ui", "inspect", "-a", app, "--json"], "screenshot": ["ui", "screenshot", "-a", app, "--output", str(screenshot_path), "--json"]}
        if operation in {"desktop.inspect", "desktop.screenshot"}:
            command = allowed[operation.removeprefix("desktop.")]
            selector = arguments.get("selector")
            if selector and operation == "desktop.inspect":
                command.insert(2, str(selector))
        elif operation == "desktop.open":
            target = str(arguments.get("app", "notepad"))
            executable = shutil.which(target) or (shutil.which(f"{target}.exe") if not target.lower().endswith(".exe") else None)
            if executable:
                flags = getattr(subprocess, "DETACHED_PROCESS", 0x00000008) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
                process = subprocess.Popen([executable], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True, creationflags=flags)
                return {"app": target, "launched": True, "process_id": process.pid}
            try:
                os.startfile(target)
                return {"app": target, "launched": True}
            except OSError as exception:
                raise RuntimeError(f"Could not launch {target}: {exception}") from exception
        elif operation == "desktop.type":
            command = ["ui", "send-keys", str(arguments.get("text", "")), "-a", app, "--json"]
        elif operation == "desktop.click":
            selector = str(arguments.get("selector", ""))
            if not selector:
                raise ValueError("selector is required")
            command = ["ui", "invoke", selector, "-a", app, "--json"]
        else:
            raise ValueError("Unsupported desktop operation")
        process = run_command([binary, *command], JOBS_ROOT / job_id, 45, job_id)
        if process.returncode:
            raise RuntimeError((process.stderr or process.stdout)[-3000:])
        result = {"stdout": process.stdout[-30000:], "stderr": process.stderr[-3000:]}
        if operation == "desktop.screenshot" and screenshot_path.is_file():
            screenshot = screenshot_path.read_bytes()
            result["screenshot_base64"] = base64.b64encode(screenshot).decode("ascii")
            result["screenshot_sha256"] = hashlib.sha256(screenshot).hexdigest()
        return result
    binary = shutil.which("peekaboo")
    if not binary:
        raise RuntimeError("Peekaboo is unavailable")
    app = str(arguments.get("app", "TextEdit"))
    if operation == "desktop.inspect":
        command = [binary, "see", "--app", app, "--json"]
    elif operation == "desktop.screenshot":
        screenshot_path = JOBS_ROOT / job_id / "screenshot.png"
        command = [binary, "see", "--app", app, "--path", str(screenshot_path), "--json"]
    elif operation == "desktop.open":
        command = ["open", "-a", app]
    elif operation == "desktop.type":
        command = [binary, "type", str(arguments.get("text", "")), "--app", app]
    elif operation == "desktop.click":
        command = [binary, "click", str(arguments.get("selector", "")), "--app", app]
    else:
        raise ValueError("Unsupported desktop operation")
    process = run_command(command, JOBS_ROOT / job_id, 45, job_id)
    if process.returncode:
        raise RuntimeError((process.stderr or process.stdout)[-3000:])
    result = {"stdout": process.stdout[-30000:], "stderr": process.stderr[-3000:]}
    if operation == "desktop.screenshot" and screenshot_path.is_file():
        screenshot = screenshot_path.read_bytes()
        result["screenshot_base64"] = base64.b64encode(screenshot).decode("ascii")
        result["screenshot_sha256"] = hashlib.sha256(screenshot).hexdigest()
    return result


def worker_once() -> bool:
    with connect_db() as db:
        row = db.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created_at LIMIT 1").fetchone()
        if not row:
            return False
        cursor = db.execute("UPDATE jobs SET status='running',updated_at=? WHERE job_id=? AND status='queued'", (utcnow(), row["job_id"]))
        if cursor.rowcount != 1:
            return True
    try:
        result = perform(row["operation"], json.loads(row["arguments"]), row["job_id"])
        status, error = "completed", None
    except subprocess.TimeoutExpired as exception:
        result, status, error = None, "failed", f"Timed out after {exception.timeout} seconds"
    except Exception as exception:
        result, status, error = None, "failed", f"{type(exception).__name__}: {exception}"
    with connect_db() as db:
        current = db.execute("SELECT status FROM jobs WHERE job_id=?", (row["job_id"],)).fetchone()
        if current and current["status"] != "cancelled":
            db.execute("UPDATE jobs SET status=?,updated_at=?,result=?,error=? WHERE job_id=?", (status, utcnow(), json_result(result) if result is not None else None, error, row["job_id"]))
    return True


def worker_loop() -> None:
    JOBS_ROOT.mkdir(parents=True, exist_ok=True)
    worker_state = ROOT / "worker-session.json"
    worker_pid = os.getpid()
    if os.name == "nt":
        worker_state.write_text(json_result({"pid": worker_pid, "session_id": windows_session_id(worker_pid), "updated_at": utcnow()}), encoding="utf-8")
    with connect_db() as db:
        db.execute("UPDATE jobs SET status='interrupted',updated_at=?,error='Executor restarted while job was active' WHERE status='running'", (utcnow(),))
    try:
        while not STOP.is_set():
            if not worker_once():
                STOP.wait(0.5)
    finally:
        if os.name == "nt":
            try:
                current = json.loads(worker_state.read_text(encoding="utf-8"))
                if current.get("pid") == worker_pid:
                    worker_state.unlink(missing_ok=True)
            except (OSError, json.JSONDecodeError):
                pass


def interactive_worker_available() -> bool:
    if os.name != "nt":
        return False
    try:
        state = json.loads((ROOT / "worker-session.json").read_text(encoding="utf-8"))
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = (ctypes.c_ulong, ctypes.c_bool, ctypes.c_ulong)
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
        process_handle = kernel32.OpenProcess(0x1000, False, int(state["pid"]))
        if not process_handle:
            return False
        try:
            session_id = windows_session_id(int(state["pid"]))
            return bool(session_id and session_id == int(state["session_id"]))
        finally:
            kernel32.CloseHandle(process_handle)
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        return False


def windows_session_id(process_id: int) -> int | None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.ProcessIdToSessionId.argtypes = (ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong))
    kernel32.ProcessIdToSessionId.restype = ctypes.c_bool
    session_id = ctypes.c_ulong()
    if not kernel32.ProcessIdToSessionId(process_id, ctypes.byref(session_id)):
        return None
    return session_id.value


def rpc(method: str, params: dict[str, Any], peers: dict[str, str]) -> Any:
    target = params.get("target", "local")
    if target != "local":
        alias = peers.get(target)
        if not alias:
            raise ValueError(f"Unknown target: {target}; configured: local, {', '.join(sorted(peers))}")
        remote_params = dict(params)
        remote_params.pop("target", None)
        return peer_call(alias, target, method, remote_params)
    if method == "computer.capabilities":
        return local_capabilities()
    if method == "computer.submit":
        return submit(params.get("request_id", ""), params.get("operation", ""), params.get("arguments", {}))
    if method == "computer.status":
        return get_job(params.get("job_id", ""))
    if method == "computer.cancel":
        return cancel_job(params.get("job_id", ""))
    if method == "computer.file_put":
        return perform("file.put", params, "transfer")
    if method == "computer.file_get":
        return perform("file.get", params, "transfer")
    raise ValueError(f"Unknown RPC method: {method}")


TOOL_DEFINITIONS = [
    {"name": "computer_capabilities", "description": "List available operations and desktop support on the local or named peer computer.", "inputSchema": {"type": "object", "properties": {"target": {"type": "string", "description": "local or a configured peer name"}}, "required": ["target"]}},
    {"name": "computer_submit", "description": "Submit an idempotent file, Python, or desktop job to local or peer computer.", "inputSchema": {"type": "object", "properties": {"target": {"type": "string"}, "request_id": {"type": "string"}, "operation": {"type": "string", "enum": ["system.info", "file.list", "file.read", "file.write", "python.run", "desktop.inspect", "desktop.screenshot", "desktop.open", "desktop.type", "desktop.click"]}, "arguments": {"type": "object"}}, "required": ["target", "request_id", "operation", "arguments"]}},
    {"name": "computer_job_status", "description": "Get the status and result of a submitted job.", "inputSchema": {"type": "object", "properties": {"target": {"type": "string"}, "job_id": {"type": "string"}}, "required": ["target", "job_id"]}},
    {"name": "computer_job_cancel", "description": "Cancel queued work or stop an active subprocess job. Completed application changes are not rolled back.", "inputSchema": {"type": "object", "properties": {"target": {"type": "string"}, "job_id": {"type": "string"}}, "required": ["target", "job_id"]}},
    {"name": "computer_file_put", "description": "Transfer one 256 KiB chunk to a peer file path. Repeat with the same upload_id, name, total_chunks and sha256 until status is complete.", "inputSchema": {"type": "object", "properties": {"target": {"type": "string"}, "upload_id": {"type": "string"}, "name": {"type": "string"}, "total_chunks": {"type": "integer"}, "chunk_index": {"type": "integer"}, "sha256": {"type": "string"}, "data_base64": {"type": "string"}}, "required": ["target", "upload_id", "name", "total_chunks", "chunk_index", "sha256", "data_base64"]}},
    {"name": "computer_file_get", "description": "Read one 256 KiB chunk from a computer-bridge file path; verify the returned SHA-256 after reassembly.", "inputSchema": {"type": "object", "properties": {"target": {"type": "string"}, "path": {"type": "string"}, "chunk_index": {"type": "integer"}}, "required": ["target", "path", "chunk_index"]}},
]


def mcp_server(peers: dict[str, str]) -> None:
    for raw in sys.stdin:
        try:
            request = json.loads(raw)
            method = request.get("method")
            request_id = request.get("id")
            if method == "notifications/initialized" or method == "notifications/cancelled":
                continue
            if method == "initialize":
                result = {"protocolVersion": MCP_VERSION, "capabilities": {"tools": {"listChanged": False}}, "serverInfo": {"name": "computer-bridge", "version": VERSION}}
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": TOOL_DEFINITIONS}
            elif method == "tools/call":
                name = request["params"]["name"]
                arguments = dict(request["params"].get("arguments", {}))
                target = arguments.get("target", "local")
                methods = {"computer_capabilities": "computer.capabilities", "computer_submit": "computer.submit", "computer_job_status": "computer.status", "computer_job_cancel": "computer.cancel", "computer_file_put": "computer.file_put", "computer_file_get": "computer.file_get"}
                if name not in methods:
                    raise ValueError(f"Unknown tool: {name}")
                result = rpc(methods[name], {**arguments, "target": target}, peers)
                result = {"content": [{"type": "text", "text": json_result(result)}], "isError": False}
            else:
                raise ValueError(f"Unsupported MCP method: {method}")
            response = {"jsonrpc": "2.0", "id": request_id, "result": result}
        except Exception as exception:
            response = {"jsonrpc": "2.0", "id": request.get("id") if "request" in locals() else None, "error": {"code": -32000, "message": f"{type(exception).__name__}: {exception}"}}
        sys.stdout.write(json_result(response) + "\n")
        sys.stdout.flush()


def parse_peers(values: list[str]) -> dict[str, str]:
    peers: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise ValueError("Peer format must be name=ssh-alias")
        name, alias = value.split("=", 1)
        peers[valid_identifier(name, "peer name")] = valid_identifier(alias, "SSH alias")
    return peers


def main() -> int:
    parser = argparse.ArgumentParser(description="Two-way SSH computer bridge and stdio MCP server")
    parser.add_argument("command", choices=["mcp", "worker", "rpc", "capabilities"])
    parser.add_argument("--peer", action="append", default=[])
    parser.add_argument("--request-file")
    arguments = parser.parse_args()
    if os.name == "nt":
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    peers = parse_peers(arguments.peer)
    if arguments.command == "mcp":
        mcp_server(peers)
    elif arguments.command == "worker":
        signal.signal(signal.SIGTERM, lambda *_: STOP.set())
        signal.signal(signal.SIGINT, lambda *_: STOP.set())
        worker_loop()
    elif arguments.command == "rpc":
        if arguments.request_file:
            name = valid_identifier(Path(arguments.request_file).name, "request file")
            request_path = (ROOT / "rpc-in" / name).resolve()
            if request_path.parent != (ROOT / "rpc-in").resolve():
                raise ValueError("request file must be in the rpc-in directory")
            message = json.loads(request_path.read_text(encoding="utf-8"))
        else:
            message = json.loads(sys.stdin.readline())
        try:
            print(json_result({"ok": True, "result": rpc(message["method"], message.get("params", {}), peers)}))
        except Exception as exception:
            print(json_result({"ok": False, "error": f"{type(exception).__name__}: {exception}"}))
        finally:
            if arguments.request_file:
                request_path.unlink(missing_ok=True)
    else:
        print(json_result(local_capabilities()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
