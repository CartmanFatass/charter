import os
import unittest
from unittest import mock

from tests._isolation import PersonaIso
from charter import trace, persona


class TestTrace(PersonaIso):
    def test_record_and_read_in_order(self):
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_SESSION_ID": "s1"}):
            trace.record("deny", reason="leak")
            trace.record("allow", persona="devops", tool="kubectl")
            ev = trace.read("s1")
        self.assertEqual([e["event"] for e in ev], ["deny", "allow"])
        self.assertEqual(ev[1]["tool"], "kubectl")

    def test_none_fields_are_dropped(self):
        trace.record("allow", session="s", persona="qa", tool=None)
        ev = trace.read("s")
        self.assertNotIn("tool", ev[0])
        self.assertEqual(ev[0]["persona"], "qa")

    def test_session_scoping_and_listing(self):
        trace.record("x", session="a")
        trace.record("y", session="b")
        self.assertEqual([e["event"] for e in trace.read("a")], ["x"])
        self.assertEqual(set(trace.sessions()), {"a", "b"})

    def test_remember_emits_memory_event(self):
        self.make_persona("dev", role="Dev", vault="dev")
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_SESSION_ID": "s2"}):
            persona.remember("dev", "a durable fact", title="t")
            ev = trace.read("s2")
        mem = [e for e in ev if e["event"] == "memory"]
        self.assertTrue(mem)
        self.assertEqual((mem[0]["persona"], mem[0]["kind"], mem[0]["scope"]), ("dev", "persistent", "own"))

    def test_set_active_emits_persona_use(self):
        self.make_persona("dev", role="Dev", vault="dev")
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_SESSION_ID": "s3"}):
            persona.set_active("dev")
            ev = trace.read("s3")
        self.assertTrue(any(e["event"] == "persona-use" and e["persona"] == "dev" for e in ev))

    def test_read_missing_session_is_empty(self):
        self.assertEqual(trace.read("nope"), [])

    def test_for_persona_filters_by_persona(self):
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_SESSION_ID": "s4"}):
            trace.record("note", persona="devops", msg="did X")
            trace.record("allow", persona="devops", tool="kubectl")
            trace.record("allow", persona="qa", tool="glab")
            trace.record("deny", reason="leak")  # no persona
            evs = trace.for_persona("devops")
        self.assertEqual([e["event"] for e in evs], ["note", "allow"])
        self.assertTrue(all(e["persona"] == "devops" for e in evs))


if __name__ == "__main__":
    unittest.main()
