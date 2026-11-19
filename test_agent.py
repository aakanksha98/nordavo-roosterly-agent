"""Offline tests: retries, resume, handoff, FAQ reply, close, redaction. No live API."""

import re
import unittest

import run_agent as agent


def scripted(responses, slept):
    calls = {"n": 0}

    def transport(method, url, headers, body):
        calls["n"] += 1
        return responses[calls["n"] - 1]

    client = agent.Client("https://example.test", "k", "a@b.c", transport=transport, sleep=slept.append, min_interval=0)
    return client, calls


class ClientTests(unittest.TestCase):
    def test_503_backoff_then_ok(self):
        slept = []
        client, calls = scripted([
            (503, {}, b"{}"), (503, {}, b"{}"), (200, {}, b'{"ok": true}'),
        ], slept)
        self.assertEqual(client.request("GET", "/status"), {"ok": True})
        self.assertEqual(calls["n"], 3)
        self.assertEqual([x for x in slept if x], [0.4, 0.8])

    def test_429_waits_retry_after(self):
        slept = []
        client, calls = scripted([
            (429, {"Retry-After": "2"}, b"{}"), (200, {}, b'{"ok": true}'),
        ], slept)
        client.request("GET", "/status")
        self.assertEqual(calls["n"], 2)
        self.assertIn(2.0, slept)

    def test_409_does_not_retry(self):
        slept = []
        client, calls = scripted([(409, {}, b'{"error":"already_handled"}')], slept)
        with self.assertRaises(agent.AlreadyHandled):
            client.request("POST", "/tickets/T1/close", {})
        self.assertEqual(calls["n"], 1)

    def test_422_does_not_retry(self):
        client, calls = scripted([(422, {}, b'{"error":"validation_failed"}')], [])
        with self.assertRaises(agent.ValidationFailed):
            client.request("POST", "/tickets/T1/reply", {})
        self.assertEqual(calls["n"], 1)

    def test_resume_skips_handled_id(self):
        sent = []
        lines = agent.handle_all(
            [{"id": "T1", "subject": "x", "body": "y"}, {"id": "T2", "subject": "", "body": ""}],
            {"T1"}, lambda tid, decision: sent.append(tid),
        )
        self.assertEqual(sent, ["T2"])
        self.assertIn("T2", lines[0])


class RoutingTests(unittest.TestCase):
    def test_refund_always_handoff(self):
        decision = agent.decide("Terugbetaling", "Kunnen we die maand terugkrijgen? IBAN NL91ABNA0417164300")
        self.assertEqual(decision["action"], "handoff")
        self.assertEqual((decision["language"], decision["category"], decision["priority"]), ("nl", "billing", "P2"))
        self.assertNotIn("NL91", decision["handoff_note"])
        self.assertNotIn("NL91", decision["reason"])

    def test_faq_reply_uses_only_faq_anchors(self):
        decision = agent.decide("Wachtwoord", "Ik ben mijn wachtwoord vergeten")
        self.assertEqual(decision["action"], "reply")
        self.assertEqual(decision["language"], "nl")
        self.assertIn("F01", decision["reason"])
        self.assertIn("app.roosterly.nl/login", decision["draft_reply"])
        for fid, body in agent.FAQ.items():
            tokens = re.findall(r"https?://\S+|[\w.-]+\.[a-z]{2,}[^\s),]*", body)
            tokens += re.findall(r'"([^"]+)"', body) + re.findall(r"€\d+", body)
            for lang in ("nl", "en", "de"):
                reply = agent.reply_text(fid, lang)
                for token in tokens:
                    self.assertIn(token.rstrip(".,;:"), reply, f"{fid} {lang} missing {token}")

    def test_empty_and_spam_close(self):
        self.assertEqual(agent.decide("", "  ")["action"], "close")
        self.assertEqual(agent.decide("Winner", "You have won a bitcoin lottery prize")["action"], "close")

    def test_payment_redaction(self):
        raw = "pay NL91ABNA0417164300 or 4111 1111 1111 1111 today"
        cleaned = agent.redact(raw)
        self.assertNotIn("NL91", cleaned)
        self.assertNotIn("4111", cleaned)
        logged = agent.format_log("T9", agent.decide("Refund", raw))
        self.assertNotIn("NL91", logged)
        self.assertNotIn("4111", logged)


if __name__ == "__main__":
    unittest.main()
