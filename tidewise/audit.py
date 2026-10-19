"""Audit trail for every agent step, on the Merkle-chained log in
tidewise.engine.merkle.

Each proposal, policy decision, approval and execution is an entry. A block
is sealed at the end of every agent turn; its Merkle root links to the
previous block's root, so editing or deleting any past record breaks
verification, and any single record can be proven to be in the log with its
inclusion proof — without revealing the others.
"""

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .engine.merkle import MerkleLog


class AuditTrail:
    def __init__(self, path: Optional[Path] = None):
        self.log = MerkleLog(str(path) if path else None)
        self.records: List[Dict[str, Any]] = []

    def record(self, kind: str, actor: str, data: Dict[str, Any]) -> int:
        payload = {"kind": kind, "actor": actor, "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "data": data}
        rid = self.log.add(payload)
        self.records.append({"id": rid, **payload})
        return rid

    def seal(self) -> Optional[Dict[str, Any]]:
        return self.log.seal()

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
        return {"chain_intact": ok, "all_inclusion_proofs_valid": proofs_ok, "detail": detail,
                "records": len(self.records), "batches": len(self.log.blocks), "pending": self.log.unsealed()}

    def batches(self) -> List[Dict[str, Any]]:
        return list(self.log.blocks)

    def export(self, path: Path) -> None:
        path.write_text(json.dumps({"records": self.records, "batches": self.batches(), "verification": self.verify()},
                                   indent=2, default=str), encoding="utf-8")
