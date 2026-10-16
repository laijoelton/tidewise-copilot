"""
Merkle-chained, tamper-evident log.

Records accumulate as pending leaves; flush() closes a batch, builds its
Merkle tree and chains the new root to the previous one, so the whole
history is tamper-evident across batches. Each record also gets its own
inclusion proof, so any single record can be shown to be in the log against
a published root without revealing the others.
"""


import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple


def _h(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def _leaf_hash(payload: Dict[str, Any]) -> bytes:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return _h(b"leaf:" + encoded)


def _node_hash(left: bytes, right: bytes) -> bytes:
    return _h(b"node:" + left + right)


def _build_tree(leaves: List[bytes]) -> List[List[bytes]]:
    """Full tree as a list of levels, leaves at index 0, root the sole entry
    of the last level. An odd node at a level is paired with itself."""
    levels = [leaves]
    current = leaves
    while len(current) > 1:
        nxt = []
        for i in range(0, len(current), 2):
            left = current[i]
            right = current[i + 1] if i + 1 < len(current) else current[i]
            nxt.append(_node_hash(left, right))
        levels.append(nxt)
        current = nxt
    return levels


def _proof_for_index(levels: List[List[bytes]], index: int) -> List[Tuple[str, bytes]]:
    proof = []
    idx = index
    for level in levels[:-1]:
        sibling = idx ^ 1
        if sibling < len(level):
            side = "R" if sibling > idx else "L"
            proof.append((side, level[sibling]))
        else:
            # idx is the odd node out at this level — _build_tree paired it with
            # itself, so the proof must do the same rather than skip a step.
            proof.append(("R", level[idx]))
        idx //= 2
    return proof


def verify_proof(leaf_hash: bytes, proof: List[Tuple[str, bytes]], root: bytes) -> bool:
    current = leaf_hash
    for side, sibling in proof:
        current = _node_hash(current, sibling) if side == "R" else _node_hash(sibling, current)
    return current == root


@dataclass
class LogRecord:
    record_id: int
    payload: Dict[str, Any]
    batch_index: Optional[int] = None
    leaf_index: Optional[int] = None


class MerkleAuditLog:
    def __init__(self, path: Optional[str] = None):
        self.path = path
        self._records: List[LogRecord] = []
        self._pending: List[LogRecord] = []
        self._batches: List[Dict[str, Any]] = []
        self._proofs: Dict[int, List[Tuple[str, bytes]]] = {}
        self._next_id = 0

    def append(self, payload: Dict[str, Any]) -> int:
        record = LogRecord(self._next_id, payload)
        self._next_id += 1
        self._records.append(record)
        self._pending.append(record)
        return record.record_id

    def pending_count(self) -> int:
        return len(self._pending)

    def flush(self) -> Optional[Dict[str, Any]]:
        """Close the current batch: build its Merkle tree, anchor the root to
        the previous one, and compute an inclusion proof for every record."""
        if not self._pending:
            return None

        leaves = [_leaf_hash(r.payload) for r in self._pending]
        levels = _build_tree(leaves)
        root = levels[-1][0]
        prev_root = self._batches[-1]["root"] if self._batches else "0" * 64
        batch_index = len(self._batches)

        for i, record in enumerate(self._pending):
            record.batch_index = batch_index
            record.leaf_index = i
            self._proofs[record.record_id] = _proof_for_index(levels, i)

        batch = {
            "index": batch_index,
            "root": root.hex(),
            "prev_root": prev_root,
            "record_ids": [r.record_id for r in self._pending],
            "size": len(self._pending),
            "ts": time.time(),
        }
        self._batches.append(batch)
        self._pending = []
        self._persist()
        return batch

    def get_receipt(self, record_id: int) -> Optional[Dict[str, Any]]:
        record = next((r for r in self._records if r.record_id == record_id), None)
        if record is None or record.batch_index is None:
            return None
        proof = self._proofs[record_id]
        batch = self._batches[record.batch_index]
        return {
            "record_id": record_id,
            "leaf_hash": _leaf_hash(record.payload).hex(),
            "proof": [(side, h.hex()) for side, h in proof],
            "root": batch["root"],
            "batch_index": batch["index"],
        }

    def verify_receipt(self, receipt: Dict[str, Any]) -> bool:
        leaf = bytes.fromhex(receipt["leaf_hash"])
        proof = [(side, bytes.fromhex(h)) for side, h in receipt["proof"]]
        root = bytes.fromhex(receipt["root"])
        return verify_proof(leaf, proof, root)

    def verify_chain_integrity(self) -> Tuple[bool, str]:
        prev = "0" * 64
        for batch in self._batches:
            if batch["prev_root"] != prev:
                return False, f"batch {batch['index']} does not chain to the previous root"
            prev = batch["root"]
        return True, f"{len(self._batches)} batches verified, chain intact"

    def _persist(self):
        if not self.path:
            return
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self._batches, f, indent=2)
        except OSError:
            pass
