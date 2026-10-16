"""Audit trail for every agent step, on the Merkle-chained log from
tidewise.engine.merkle.

Each proposal, policy decision, approval and execution is a leaf. A batch is
closed at the end of every agent turn; its Merkle root is chained to the
previous root, so editing or deleting any past record breaks verification,
and any single record can be proven to be in the log with its inclusion
proof — without revealing the others.
"""

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .engine.merkle import MerkleAuditLog


class AuditTrail:
    def __init__(self, path: Optional[Path] = None):
        self.log = MerkleAuditLog(str(path) if path else None)
        self.records: List[Dict[str, Any]] = []

    def record(self, kind: str, actor: str, data: Dict[str, Any]) -> int:
        payload = {"kind": kind, "actor": actor, "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "data": data}
        rid = self.log.append(payload)
        self.records.append({"id": rid, **payload})
        return rid

    def seal(self) -> Optional[Dict[str, Any]]:
        return self.log.flush()

    def receipt(self, record_id: int) -> Optional[Dict[str, Any]]:
        r = self.log.get_receipt(record_id)
        if r:
            r["verified"] = self.log.verify_receipt(r)
        return r

    def verify(self) -> Dict[str, Any]:
        ok, detail = self.log.verify_chain_integrity()
        proofs_ok = all(self.log.verify_receipt(self.log.get_receipt(r["id"]))
                        for r in self.records if self.log.get_receipt(r["id"]))
        return {"chain_intact": ok, "all_inclusion_proofs_valid": proofs_ok, "detail": detail,
                "records": len(self.records), "batches": len(self.log._batches),
                "pending": self.log.pending_count()}

    def batches(self) -> List[Dict[str, Any]]:
        return list(self.log._batches)

    def export(self, path: Path) -> None:
        path.write_text(json.dumps({"records": self.records, "batches": self.batches(), "verification": self.verify()},
                                   indent=2, default=str), encoding="utf-8")
