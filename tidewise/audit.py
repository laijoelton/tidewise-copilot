"""Audit trail for every agent step, on the Merkle-chained log in
tidewise.engine.merkle.

Each proposal, policy decision, approval and execution is an entry. A block
is sealed at the end of every agent turn; its Merkle root links to the
previous block's root, so editing or deleting any past record breaks
verification, and any single record can be proven to be in the log with its
inclusion proof — without revealing the others.

Anchoring (risk R6): when a log is persisted, every sealed root is also
appended to an anchor file kept outside the app's folder
(TIDEWISE_ANCHOR_FILE, default ~/.tidewise/audit_anchors.jsonl). verify()
checks the log against it, so rewriting the whole log consistently is caught
unless the anchor file is rewritten too. /api/audit/anchor gives the owner the
latest root fingerprint to keep or send elsewhere.
"""

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from .engine.merkle import MerkleLog


class AuditTrail:
    def __init__(self, path: Optional[Path] = None, anchor: Optional[Path] = None):
        self.log = MerkleLog(str(path) if path else None)
        self.records: List[Dict[str, Any]] = []
        self.log_id = uuid.uuid4().hex[:12]
        if anchor is None and path is not None:
            anchor = Path(os.environ.get("TIDEWISE_ANCHOR_FILE") or Path.home() / ".tidewise" / "audit_anchors.jsonl")
        self.anchor_path = anchor

    def record(self, kind: str, actor: str, data: Dict[str, Any]) -> int:
        payload = {"kind": kind, "actor": actor, "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "data": data}
        rid = self.log.add(payload)
        self.records.append({"id": rid, **payload})
        return rid

    def seal(self) -> Optional[Dict[str, Any]]:
        block = self.log.seal()
        if block and self.anchor_path:
            try:
                self.anchor_path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.anchor_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps({"log": self.log_id, "index": block["index"], "root": block["root"],
                                        "prev_root": block["prev_root"], "sealed_at": block["sealed_at"]}) + "\n")
            except OSError:
                pass
        return block

    def anchors(self) -> List[Dict[str, Any]]:
        if not self.anchor_path or not self.anchor_path.exists():
            return []
        out = []
        for line in self.anchor_path.read_text(encoding="utf-8").splitlines():
            try:
                a = json.loads(line)
            except ValueError:
                continue
            if a.get("log") == self.log_id:
                out.append(a)
        return out

    def check_anchors(self) -> Dict[str, Any]:
        """Every anchored root must still be the root of that block in the log."""
        anchors = self.anchors()
        blocks = self.log.blocks
        bad = [a["index"] for a in anchors
               if a["index"] >= len(blocks) or blocks[a["index"]]["root"] != a["root"]]
        return {"anchored": len(anchors), "anchors_match": not bad, "mismatched_batches": bad,
                "anchor_file": str(self.anchor_path) if self.anchor_path else None}

    def anchor(self) -> Dict[str, Any]:
        """The latest root, for the owner to keep outside the app."""
        last = self.log.blocks[-1] if self.log.blocks else None
        return {"log": self.log_id, "batches": len(self.log.blocks),
                "root": last["root"] if last else None,
                "fingerprint": last["root"][:16] if last else None, **self.check_anchors()}

    def receipt(self, record_id: int) -> Optional[Dict[str, Any]]:
        p = self.log.proof(record_id)
        if not p:
            return None
        return {"record_id": record_id, "leaf_hash": p["leaf"], "proof": p["path"], "root": p["root"],
                "batch_index": p["block"], "verified": MerkleLog.check_proof(p)}

    def verify(self) -> Dict[str, Any]:
        ok, detail = self.log.check_chain()
        proofs = [self.log.proof(r["id"]) for r in self.records]
        proofs_ok = all(MerkleLog.check_proof(p) for p in proofs if p)
        anchors = self.check_anchors()
        return {"chain_intact": ok, "all_inclusion_proofs_valid": proofs_ok, "detail": detail,
                "anchored_batches": anchors["anchored"], "anchors_match": anchors["anchors_match"],
                "records": len(self.records), "batches": len(self.log.blocks), "pending": self.log.unsealed()}

    def batches(self) -> List[Dict[str, Any]]:
        return list(self.log.blocks)

    def export(self, path: Path) -> None:
        path.write_text(json.dumps({"records": self.records, "batches": self.batches(), "verification": self.verify()},
                                   indent=2, default=str), encoding="utf-8")
