"""Tests for tidewise.engine: the Merkle-chained log and the rate limiter."""

import unittest

from tidewise.engine.merkle import MerkleLog, entry_hash, merkle_levels, node_hash
from tidewise.engine.ratelimit import RateLimiter


class TestMerkle(unittest.TestCase):
    def test_every_entry_has_a_valid_proof_across_blocks(self):
        log = MerkleLog()
        ids = [log.add({"n": i}) for i in range(7)]       # odd count exercises the carried-up node
        log.seal()
        ids.append(log.add({"n": 99}))
        log.seal()
        for i in ids:
            self.assertTrue(MerkleLog.check_proof(log.proof(i)))
        self.assertTrue(log.check_chain()[0])

    def test_rewriting_a_block_root_breaks_the_chain(self):
        log = MerkleLog()
        log.add({"a": 1}); log.seal()
        log.add({"b": 2}); log.seal()
        log.blocks[0]["root"] = "0" * 64
        self.assertFalse(log.check_chain()[0])

    def test_editing_a_sealed_entry_is_detected(self):
        log = MerkleLog()
        eid = log.add({"pay": 4200}); log.seal()
        log._entries[eid]["pay"] = 42000
        ok, why = log.check_chain()
        self.assertFalse(ok)
        self.assertIn("no longer matches", why)

    def test_forged_proof_fails(self):
        log = MerkleLog()
        eid = log.add({"x": 1}); log.add({"y": 2}); log.seal()
        p = log.proof(eid)
        p["leaf"] = entry_hash({"x": 999})
        self.assertFalse(MerkleLog.check_proof(p))

    def test_leaf_and_node_hashes_are_domain_separated(self):
        a, b = entry_hash({"a": 1}), entry_hash({"b": 2})
        self.assertNotEqual(merkle_levels([a, b])[-1][0], entry_hash({"left": a, "right": b}))
        self.assertEqual(merkle_levels([a, b])[-1][0], node_hash(a, b))


class TestRateLimiter(unittest.TestCase):
    def test_capacity(self):
        rl = RateLimiter(window_s=60)
        self.assertEqual([rl.allow("k", 2) for _ in range(3)], [True, True, False])


if __name__ == "__main__":
    unittest.main()
