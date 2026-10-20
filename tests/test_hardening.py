"""Hardening: mandatory provenance (R3), saved state (R5), anchored audit
roots (R6), owner-token lockout (R1)."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tidewise import store
from tidewise.agent import Workspace
from tidewise.audit import AuditTrail
from tidewise.policy import Action

KEY = b"test-key"


class TestProvenance(unittest.TestCase):
    def test_provenance_is_mandatory(self):
        with self.assertRaises(TypeError):
            Action("pay_bill", {"bill_id": "BILL-774"})

    def test_unknown_provenance_refused(self):
        with self.assertRaises(ValueError):
            Action("pay_bill", {"bill_id": "BILL-774"}, "email")

    def test_external_needs_its_source(self):
        with self.assertRaises(ValueError):
            Action("pay_bill", {"bill_id": "BILL-774"}, "external")
        Action("pay_bill", {"bill_id": "BILL-774"}, "external", source_ref="MSG-01")


class TestSavedState(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "state.bin"
        self.ws = Workspace(use_llm=False)
        self.ws.agent.chat("What should I do this week?")
        self.ws.policy.paused = True

    def tearDown(self):
        self.dir.cleanup()

    def test_restart_keeps_approvals_limits_and_audit(self):
        pending = {a["id"] for a in self.ws.agent.pending()}
        self.assertTrue(pending)
        paid, reminders = self.ws.policy.auto_paid_today, dict(self.ws.policy.reminder_log)
        records = len(self.ws.audit.records)
        self.assertTrue(store.save(self.ws, self.path, KEY))

        fresh = Workspace(use_llm=False)                     # "restart"
        r = store.load(fresh, self.path, KEY)
        self.assertTrue(r["restored"])
        self.assertEqual({a["id"] for a in fresh.agent.pending()}, pending)
        self.assertEqual(fresh.policy.auto_paid_today, paid)
        self.assertEqual(fresh.policy.reminder_log, reminders)
        self.assertTrue(fresh.policy.paused)
        self.assertEqual(len(fresh.audit.records), records)
        v = fresh.audit.verify()
        self.assertTrue(v["chain_intact"] and v["all_inclusion_proofs_valid"])
        self.assertIs(fresh.policy.company, fresh.company)    # shared objects stay shared

    def test_approval_still_works_after_restart(self):
        store.save(self.ws, self.path, KEY)
        fresh = Workspace(use_llm=False)
        store.load(fresh, self.path, KEY)
        a = fresh.agent.pending()[0]
        out = fresh.agent.approve(a["id"], "owner", confirm_amount=a["amount_myr"])
        self.assertNotIn("error", out)

    def test_tampered_snapshot_is_never_loaded(self):
        store.save(self.ws, self.path, KEY)
        raw = bytearray(self.path.read_bytes())
        raw[-10] ^= 0xFF
        self.path.write_bytes(bytes(raw))
        fresh = Workspace(use_llm=False)
        r = store.load(fresh, self.path, KEY)
        self.assertFalse(r["restored"])
        self.assertIn("integrity", r["reason"])
        self.assertFalse(fresh.policy.paused)

    def test_wrong_key_refused(self):
        store.save(self.ws, self.path, KEY)
        self.assertFalse(store.load(Workspace(use_llm=False), self.path, b"other")["restored"])

    def test_snapshot_from_other_code_starts_clean(self):
        store.save(self.ws, self.path, KEY)
        with mock.patch.object(store, "schema", return_value="different"):
            r = store.load(Workspace(use_llm=False), self.path, KEY)
        self.assertFalse(r["restored"])

    def test_missing_file(self):
        self.assertFalse(store.load(Workspace(use_llm=False), self.path, KEY)["restored"])


class TestAnchors(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        d = Path(self.dir.name)
        self.trail = AuditTrail(d / "batches.json", anchor=d / "anchors.jsonl")
        for i in range(3):
            self.trail.record("step", "agent", {"n": i})
            self.trail.seal()

    def tearDown(self):
        self.dir.cleanup()

    def test_every_root_is_anchored(self):
        a = self.trail.anchor()
        self.assertEqual(a["anchored"], 3)
        self.assertTrue(a["anchors_match"])
        self.assertEqual(a["fingerprint"], self.trail.batches()[-1]["root"][:16])

    def test_consistent_rewrite_is_caught_by_the_anchor(self):
        # an operator rewrites a record AND recomputes every root, so the chain still verifies...
        from tidewise.engine.merkle import GENESIS, merkle_levels, entry_hash
        log = self.trail.log
        log._entries[0]["data"]["n"] = 99
        prev = GENESIS
        for b in log.blocks:
            b["root"] = merkle_levels([entry_hash(log._entries[e]) for e in b["entry_ids"]])[-1][0]
            b["prev_root"] = prev
            prev = b["root"]
        self.assertTrue(log.check_chain()[0])
        # ...but the roots kept outside the app no longer match
        v = self.trail.verify()
        self.assertFalse(v["anchors_match"])

    def test_in_memory_log_has_no_anchor_file(self):
        self.assertIsNone(AuditTrail().anchor_path)


class TestOwnerLockout(unittest.TestCase):
    ENV = ("TIDEWISE_LIVE_DATA", "TIDEWISE_SAVE_STATE", "TIDEWISE_ANCHOR_FILE")

    @classmethod
    def setUpClass(cls):
        cls.saved_env = {k: os.environ.get(k) for k in cls.ENV}
        cls.dir = tempfile.TemporaryDirectory()
        os.environ.update({"TIDEWISE_LIVE_DATA": "off", "TIDEWISE_SAVE_STATE": "0",
                           "TIDEWISE_ANCHOR_FILE": str(Path(cls.dir.name) / "anchors.jsonl")})
        from api import main
        cls.main = main

    @classmethod
    def tearDownClass(cls):
        for k, v in cls.saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        cls.dir.cleanup()

    def _req(self, ip):
        from starlette.requests import Request
        return Request({"type": "http", "client": (ip, 1), "headers": [], "method": "POST", "path": "/"})

    def test_lockout_after_five_wrong_tokens(self):
        from fastapi import HTTPException
        m = self.main
        ip = "10.9.9.9"
        for _ in range(m.MAX_BAD_TOKENS):
            with self.assertRaises(HTTPException) as e:
                m.require_owner(self._req(ip), "wrong")
            self.assertEqual(e.exception.status_code, 401)
        with self.assertRaises(HTTPException) as e:            # even the right token is refused while locked
            m.require_owner(self._req(ip), m.OWNER_TOKEN)
        self.assertEqual(e.exception.status_code, 429)
        self.assertEqual(m.require_owner(self._req("10.9.9.10"), m.OWNER_TOKEN), "owner")   # other addresses unaffected
        rejected = [r for r in m.ws.audit.records if r["kind"] == "owner_token_rejected"]
        self.assertGreaterEqual(len(rejected), m.MAX_BAD_TOKENS)

    def test_right_token_clears_earlier_mistakes(self):
        m = self.main
        ip = "10.8.8.8"
        from fastapi import HTTPException
        for _ in range(m.MAX_BAD_TOKENS - 1):
            with self.assertRaises(HTTPException):
                m.require_owner(self._req(ip), "wrong")
        self.assertEqual(m.require_owner(self._req(ip), m.OWNER_TOKEN), "owner")
        with self.assertRaises(HTTPException) as e:
            m.require_owner(self._req(ip), "wrong")
        self.assertEqual(e.exception.status_code, 401)


if __name__ == "__main__":
    unittest.main()
