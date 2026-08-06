"""The committed dispatch tally — the instrument that makes a never-used persona visible.

Guards the three properties the store is built on: it records counts+dates and NEVER the
prompt; parallel writers don't clobber each other; and the hook only fires for real
sub-agent dispatches.
"""
from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from tests._isolation import PersonaIso, run_hook
from charter import dispatch, hooks


class TestDispatchStore(PersonaIso):
    def test_record_then_tally(self):
        dispatch.record("devops")
        dispatch.record("devops")
        dispatch.record("qa")
        self.assertEqual(dispatch.tally()["devops"], 2)
        self.assertEqual(dispatch.tally()["qa"], 1)

    def test_never_dispatched_reads_as_zero_not_missing(self):
        dispatch.record("devops")
        self.assertEqual(dispatch.tally()["nx-code-reviewer"], 0)
        self.assertIsNone(dispatch.last_seen("nx-code-reviewer"))

    def test_line_holds_counts_and_dates_only(self):
        """The whole reason this store needs no secret-scan: no prompt ever reaches it."""
        p = dispatch.record("devops")
        row = json.loads(p.read_text().splitlines()[0])
        self.assertEqual(set(row), {"ts", "agent"})
        self.assertEqual(row["agent"], "devops")

    def test_days_window_excludes_older_rows(self):
        old = datetime.now(timezone.utc) - timedelta(days=40)
        dispatch.record("devops", when=old)
        dispatch.record("qa")
        self.assertEqual(dispatch.tally(days=14), {"qa": 1})

    def test_generic_agents_are_tallied_too(self):
        """Dropping generic dispatches would hide the persona-vs-generic ratio — the one
        number that exposed 42 reviews going to general-purpose."""
        for _ in range(3):
            dispatch.record("general-purpose")
        dispatch.record("devops")
        self.assertEqual(dispatch.generic_share(), (3, 4))

    def test_parallel_appends_do_not_clobber(self):
        """A fan-out of sub-agents finishing together must not lose lines."""
        import threading
        threads = [threading.Thread(target=dispatch.record, args=(f"a{i%3}",)) for i in range(30)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sum(dispatch.tally().values()), 30)

    def test_store_dir_is_not_mistaken_for_a_persona(self):
        from charter import persona
        dispatch.record("devops")
        self.assertNotIn(dispatch.DIR_NAME, persona.list_personas())


class TestDispatchHook(PersonaIso):
    def _run(self, payload):
        with mock.patch.object(hooks, "_commit_dispatch"):  # don't touch git in tests
            return run_hook(hooks.posttooluse_dispatch, payload)

    def test_task_dispatch_is_recorded(self):
        self._run({"tool_name": "Task", "tool_input": {"subagent_type": "devops"}})
        self.assertEqual(dispatch.tally()["devops"], 1)

    def test_other_tools_are_ignored(self):
        self._run({"tool_name": "Bash", "tool_input": {"command": "ls"}})
        self.assertEqual(sum(dispatch.tally().values()), 0)

    def test_missing_subagent_type_is_ignored(self):
        self._run({"tool_name": "Task", "tool_input": {"prompt": "do a thing"}})
        self.assertEqual(sum(dispatch.tally().values()), 0)

    def test_hook_never_raises_when_the_store_is_unwritable(self):
        with mock.patch.object(dispatch, "record", side_effect=OSError("boom")):
            self.assertIsNone(self._run({"tool_name": "Task",
                                         "tool_input": {"subagent_type": "devops"}}))


if __name__ == "__main__":
    unittest.main()
