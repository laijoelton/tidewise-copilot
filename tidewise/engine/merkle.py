"""Merkle-chained log.

Entries are added one by one and sealed into blocks. Each block stores the
Merkle root of its entries and the root of the block before it, so:

  * changing, removing or reordering any sealed entry changes its block's
    root and breaks the chain from there on;
  * any single entry can be proven to be in a block with a short path of
    sibling hashes, without revealing the other entries.

Hashes are domain-separated (0x00 for entries, 0x01 for inner nodes) so an
inner node can never be passed off as an entry.
"""

import hashlib
import json
import time
from typing import Any, Dict, List, Optional, Tuple

GENESIS = "0" * 64


def _digest(prefix: bytes, data: bytes) -> str:
    return hashlib.sha256(prefix + data).hexdigest()


def entry_hash(entry: Dict[str, Any]) -> str:
    return _digest(b"\x00", json.dumps(entry, sort_keys=True, separators=(",", ":"), default=str).encode())


def node_hash(left: str, right: str) -> str:
    return _digest(b"\x01", bytes.fromhex(left) + bytes.fromhex(right))


def merkle_levels(hashes: List[str]) -> List[List[str]]:
    """All levels bottom-up; an unpaired last hash is carried up unchanged."""
    levels = [list(hashes)]
    while len(levels[-1]) > 1:
        cur, nxt = levels[-1], []
        for i in range(0, len(cur) - 1, 2):
            nxt.append(node_hash(cur[i], cur[i + 1]))
        if len(cur) % 2:
            nxt.append(cur[-1])
        levels.append(nxt)
    return levels


def audit_path(levels: List[List[str]], index: int) -> List[Tuple[str, str]]:
    """Sibling hashes from leaf to root; side 'L' means the sibling is on the left."""
    path = []
    for level in levels[:-1]:
        sib = index ^ 1
        if sib < len(level):
            path.append(("L" if sib < index else "R", level[sib]))
        index //= 2
    return path


def root_from_path(leaf: str, path: List[Tuple[str, str]]) -> str:
    h = leaf
    for side, sib in path:
        h = node_hash(sib, h) if side == "L" else node_hash(h, sib)
    return h


class MerkleLog:
    def __init__(self, path: Optional[str] = None):
        self.path = path
        self.blocks: List[Dict[str, Any]] = []
        self._entries: Dict[int, Dict[str, Any]] = {}
        self._where: Dict[int, Tuple[int, int]] = {}      # entry id -> (block, position)
        self._levels: Dict[int, List[List[str]]] = {}
        self._open: List[int] = []
        self._next = 0

    def add(self, entry: Dict[str, Any]) -> int:
        eid = self._next
        self._next += 1
        self._entries[eid] = entry
        self._open.append(eid)
        return eid

    def unsealed(self) -> int:
        return len(self._open)

    def seal(self) -> Optional[Dict[str, Any]]:
        if not self._open:
            return None
        hashes = [entry_hash(self._entries[e]) for e in self._open]
        levels = merkle_levels(hashes)
        number = len(self.blocks)
        block = {"index": number, "root": levels[-1][0],
                 "prev_root": self.blocks[-1]["root"] if self.blocks else GENESIS,
                 "entry_ids": list(self._open), "size": len(self._open), "sealed_at": time.time()}
        self.blocks.append(block)
        self._levels[number] = levels
        for pos, eid in enumerate(self._open):
            self._where[eid] = (number, pos)
        self._open = []
        self._save()
        return block

    def proof(self, eid: int) -> Optional[Dict[str, Any]]:
        if eid not in self._where:
            return None
        number, pos = self._where[eid]
        return {"entry_id": eid, "leaf": entry_hash(self._entries[eid]),
                "path": audit_path(self._levels[number], pos),
                "root": self.blocks[number]["root"], "block": number}

    @staticmethod
    def check_proof(p: Dict[str, Any]) -> bool:
        return root_from_path(p["leaf"], [tuple(x) for x in p["path"]]) == p["root"]

    def check_chain(self) -> Tuple[bool, str]:
        prev = GENESIS
        for b in self.blocks:
            if b["prev_root"] != prev:
                return False, f"block {b['index']} does not link to the block before it"
            recomputed = merkle_levels([entry_hash(self._entries[e]) for e in b["entry_ids"]])[-1][0]
            if recomputed != b["root"]:
                return False, f"block {b['index']} no longer matches its entries"
            prev = b["root"]
        return True, f"{len(self.blocks)} blocks verified, chain intact"

    def _save(self) -> None:
        if not self.path:
            return
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.blocks, f, indent=2)
        except OSError:
            pass
