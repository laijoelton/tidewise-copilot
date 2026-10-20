"""Saved state: the workspace survives a restart (risk R5).

Approvals waiting for the owner, the day's auto-pay total, the reminder log,
the kill switch, the books, the sandbox receipts and the audit log are written
to one snapshot after every change and read back at start-up.

The snapshot is pickled, so it is only ever unpickled after its HMAC-SHA256
tag checks out: a file that was edited, truncated or swapped is refused and
the app starts from the clean demo instead (and says so). Writes are atomic
(temp file, then rename), so a crash mid-write never leaves half a snapshot.

The key comes from TIDEWISE_STATE_KEY, or is generated once into
~/.tidewise/state.key, outside the project folder. Set TIDEWISE_SAVE_STATE=0
to turn saving off.
"""

import hashlib
import hmac
import os
import pickle
import secrets
import threading
from pathlib import Path
from typing import Any, Dict, Optional

MAGIC = b"TIDEWISE-STATE-1\n"
_SCHEMA_FILES = ("models.py", "policy.py", "agent.py", "sandbox.py", "audit.py", "engine/merkle.py")
SKIP = {"llm", "agent", "persist", "use_llm"}       # rebuilt, never saved
_lock = threading.Lock()


def enabled() -> bool:
    return os.environ.get("TIDEWISE_SAVE_STATE", "1") != "0"


def _key(key_file: Optional[Path] = None) -> bytes:
    env = os.environ.get("TIDEWISE_STATE_KEY")
    if env:
        return env.encode()
    key_file = key_file or Path.home() / ".tidewise" / "state.key"
    if not key_file.exists():
        key_file.parent.mkdir(parents=True, exist_ok=True)
        key_file.write_text(secrets.token_hex(32), encoding="utf-8")
        try:
            os.chmod(key_file, 0o600)
        except OSError:
            pass
    return key_file.read_text(encoding="utf-8").strip().encode()


def schema() -> str:
    """Fingerprint of the classes a snapshot holds: a snapshot from other code is not loaded."""
    h = hashlib.sha256()
    here = Path(__file__).resolve().parent
    for name in _SCHEMA_FILES:
        h.update((here / name).read_bytes().replace(b"\r\n", b"\n"))
    return h.hexdigest()[:16]


def save(ws, path: Path, key: Optional[bytes] = None) -> bool:
    """Write the workspace. Returns False (and keeps the old snapshot) on any error."""
    state = {"__schema__": schema(), **{k: v for k, v in ws.__dict__.items() if k not in SKIP}}
    with _lock:
        try:
            body = pickle.dumps(state, protocol=pickle.HIGHEST_PROTOCOL)
        except Exception:                               # e.g. a dict changed mid-request; next save catches up
            return False
        tag = hmac.new(key or _key(), body, hashlib.sha256).hexdigest().encode()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(MAGIC + tag + b"\n" + body)
        os.replace(tmp, path)
        return True


def load(ws, path: Path, key: Optional[bytes] = None) -> Dict[str, Any]:
    """Restore the workspace in place. Never unpickles a file whose tag fails."""
    if not path.exists():
        return {"restored": False, "reason": "no saved state yet"}
    raw = path.read_bytes()
    if not raw.startswith(MAGIC):
        return {"restored": False, "reason": "not a Tidewise state file"}
    tag, _, body = raw[len(MAGIC):].partition(b"\n")
    expected = hmac.new(key or _key(), body, hashlib.sha256).hexdigest().encode()
    if not hmac.compare_digest(tag, expected):
        return {"restored": False, "reason": "integrity check failed: snapshot ignored, starting clean"}
    try:
        state = pickle.loads(body)
    except Exception:
        return {"restored": False, "reason": "snapshot unreadable: starting clean"}
    if state.pop("__schema__", None) != schema():
        return {"restored": False, "reason": "code changed since the snapshot: starting clean"}
    ws.__dict__.update(state)
    from .agent import Agent
    from .llm import LLM
    ws.llm = LLM() if ws.use_llm else None
    ws.agent = Agent(ws)
    return {"restored": True, "approvals_pending": sum(1 for a in ws.approvals.values() if a.get("status") == "pending"),
            "audit_records": len(ws.audit.records)}
