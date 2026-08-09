"""The native 1Password provider — charter creates and manages the items.

Nothing here shells out. The provider's `runner` is replaced with a recorder, so these
tests assert the *contract* charter has with `op`: which argv it builds, and — the part
that matters most — that a secret value never appears in any of them.

None of this proves the commands work against a live 1Password account. It cannot: no
account is configured on the machine this was written on. What it proves is that the
commands are shaped correctly and that the security contract holds.
"""

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace

from charter.secrets import onepassword as _op_mod
from charter.secrets.base import SecretNotFound, VaultError
from charter.secrets.onepassword import OnePasswordProvider

SECRET = "s3cr3t-value-do-not-leak"


class _Recorder:
    """Stands in for `util.run`, recording calls and replaying canned results."""

    def __init__(self, results=None):
        self.calls: list[dict] = []
        self.results = list(results or [])

    def __call__(self, argv, input=None, check=False, **kw):
        self.calls.append({"argv": list(argv), "input": input})
        if self.results:
            rc, out = self.results.pop(0)
        else:
            rc, out = 0, ""
        return SimpleNamespace(returncode=rc, stdout=out, stderr="")

    @property
    def all_argv(self) -> str:
        return " ".join(" ".join(c["argv"]) for c in self.calls)


def _items(*titles) -> str:
    return json.dumps([{"title": t, "id": f"id-{t}"} for t in titles])


class _OpOnPath(unittest.TestCase):
    """`op` is not installed on CI, and the provider checks PATH before running.

    Stubbing that keeps these tests about charter's contract with `op` rather than
    about whether this particular machine happens to have it — the suite must give the
    same answer on a laptop with 1Password installed and on a bare runner.
    """

    def setUp(self) -> None:
        super().setUp()
        self._which = _op_mod.shutil.which
        _op_mod.shutil.which = lambda name: "/usr/local/bin/op" if name == "op" else None
        self.addCleanup(lambda: setattr(_op_mod.shutil, "which", self._which))


def _provider(rec, **cfg):
    p = OnePasswordProvider("devops", {"op-vault": "Engineering", **cfg})
    p.runner = rec
    p._exists_shortcut = None
    return p


class NeverOnArgv(_OpOnPath):
    """1Password's own help: "Command arguments get logged in your command history,
    and can be visible to other processes on your machine. If you're assigning
    sensitive values, use a JSON template instead." So charter uses stdin."""

    def test_creating_a_secret_never_puts_the_value_in_argv(self):
        rec = _Recorder([(0, _items()), (0, "")])          # list (empty) → create
        _provider(rec).set("TOKEN", SECRET)
        self.assertNotIn(SECRET, rec.all_argv)

    def test_creating_a_secret_sends_the_value_on_stdin(self):
        rec = _Recorder([(0, _items()), (0, "")])
        _provider(rec).set("TOKEN", SECRET)
        payload = rec.calls[-1]["input"]
        self.assertIsNotNone(payload, "the template must be piped, not passed as a flag")
        self.assertIn(SECRET, payload)

    def test_updating_a_secret_never_puts_the_value_in_argv(self):
        rec = _Recorder([(0, _items("charter-devops-TOKEN")), (0, "")])
        _provider(rec).set("TOKEN", SECRET)
        self.assertNotIn(SECRET, rec.all_argv)
        self.assertIn(SECRET, rec.calls[-1]["input"])

    def test_a_read_passes_only_a_uri(self):
        rec = _Recorder([(0, SECRET)])
        _provider(rec).get("TOKEN")
        self.assertNotIn(SECRET, rec.all_argv)


class Commands(_OpOnPath):
    def test_create_uses_the_stdin_template_form(self):
        rec = _Recorder([(0, _items()), (0, "")])
        _provider(rec).set("TOKEN", SECRET)
        argv = rec.calls[-1]["argv"]
        self.assertEqual(argv[:4], ["op", "item", "create", "-"])
        self.assertIn("--vault", argv)
        self.assertIn("Engineering", argv)

    def test_an_existing_item_is_edited_not_duplicated(self):
        rec = _Recorder([(0, _items("charter-devops-TOKEN")), (0, "")])
        _provider(rec).set("TOKEN", SECRET)
        self.assertEqual(rec.calls[-1]["argv"][1:3], ["item", "edit"])

    def test_read_builds_the_op_uri_from_vault_and_key(self):
        rec = _Recorder([(0, SECRET)])
        _provider(rec).get("TOKEN")
        self.assertIn("op://Engineering/charter-devops-TOKEN/password",
                      rec.calls[0]["argv"])

    def test_read_strips_a_trailing_newline(self):
        rec = _Recorder([(0, SECRET + "\n")])
        self.assertEqual(_provider(rec).get("TOKEN"), SECRET)

    def test_delete_removes_the_item(self):
        rec = _Recorder([(0, _items("charter-devops-TOKEN")), (0, "")])
        _provider(rec).delete("TOKEN")
        self.assertEqual(rec.calls[-1]["argv"][1:3], ["item", "delete"])

    def test_deleting_an_absent_key_raises_not_found(self):
        rec = _Recorder([(0, _items())])
        with self.assertRaises(SecretNotFound):
            _provider(rec).delete("NOPE")

    def test_a_failed_read_is_a_miss_not_a_crash(self):
        rec = _Recorder([(1, "")])
        with self.assertRaises(SecretNotFound):
            _provider(rec).get("TOKEN")


class Isolation(_OpOnPath):
    """A 1Password vault is usually shared with humans who put their own items in it."""

    def test_keys_lists_only_items_charter_created(self):
        rec = _Recorder([(0, _items("charter-devops-TOKEN", "charter-devops-KUBECONFIG"))])
        self.assertEqual(_provider(rec).keys(), ["KUBECONFIG", "TOKEN"])

    def test_listing_filters_by_this_vaults_tag(self):
        rec = _Recorder([(0, _items())])
        _provider(rec).keys()
        argv = rec.calls[0]["argv"]
        self.assertIn("--tags", argv)
        self.assertIn("charter:devops", argv)

    def test_an_item_from_another_charter_vault_is_not_listed(self):
        """The tag scopes the query, but a shared tag prefix must not leak either."""
        rec = _Recorder([(0, _items("charter-devops-TOKEN", "charter-qa-TOKEN"))])
        self.assertEqual(_provider(rec).keys(), ["TOKEN"])


class Configuration(_OpOnPath):
    def test_a_missing_op_vault_is_a_clear_error(self):
        p = OnePasswordProvider("devops", {})
        p.runner = _Recorder()
        with self.assertRaises(VaultError) as cm:
            p.keys()
        self.assertIn("op-vault", str(cm.exception))

    def test_an_account_is_pinned_when_configured(self):
        """Signed into several accounts, an unqualified vault name resolves against
        whichever is default — a quiet way to write into the wrong company's vault."""
        rec = _Recorder([(0, _items())])
        _provider(rec, account="acme.1password.com").keys()
        argv = rec.calls[0]["argv"]
        self.assertIn("--account", argv)
        self.assertIn("acme.1password.com", argv)

    def test_no_account_flag_when_unconfigured(self):
        rec = _Recorder([(0, _items())])
        _provider(rec).keys()
        self.assertNotIn("--account", rec.calls[0]["argv"])


class ErrorsWithholdOutput(_OpOnPath):
    """`op`'s stderr can echo what it was given, and on a read path its stdout IS the
    secret. Failures report the exit status and nothing else."""

    def test_a_failed_write_does_not_echo_the_value(self):
        rec = _Recorder([(0, _items()), (1, SECRET)])
        with self.assertRaises(VaultError) as cm:
            _provider(rec).set("TOKEN", SECRET)
        self.assertNotIn(SECRET, str(cm.exception))

    def test_a_failed_read_does_not_echo_the_value(self):
        rec = _Recorder([(1, SECRET)])
        with self.assertRaises(SecretNotFound) as cm:
            _provider(rec).get("TOKEN")
        self.assertNotIn(SECRET, str(cm.exception))


class Health(_OpOnPath):
    def test_health_never_reads_a_value(self):
        rec = _Recorder([(0, _items("charter-devops-TOKEN"))])
        ok, detail = _provider(rec).health()
        self.assertTrue(ok)
        self.assertNotIn("read", rec.all_argv)
        self.assertIn("1", detail)

    def test_health_reports_a_broken_vault_without_raising(self):
        rec = _Recorder([(1, "")])
        ok, _detail = _provider(rec).health()
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()


class MissingCli(unittest.TestCase):
    """No stub here on purpose: this is the one case that asserts the PATH guard."""

    def test_a_missing_op_binary_says_so_and_how_to_fix_it(self):
        orig = _op_mod.shutil.which
        _op_mod.shutil.which = lambda _n: None
        try:
            p = OnePasswordProvider("devops", {"op-vault": "Eng"})
            p.runner = _Recorder()
            with self.assertRaises(VaultError) as cm:
                p.keys()
            self.assertIn("op", str(cm.exception))
            self.assertIn("PATH", str(cm.exception))
        finally:
            _op_mod.shutil.which = orig

    def test_health_reports_a_missing_cli_rather_than_raising(self):
        orig = _op_mod.shutil.which
        _op_mod.shutil.which = lambda _n: None
        try:
            ok, detail = OnePasswordProvider("devops", {"op-vault": "Eng"}).health()
            self.assertFalse(ok)
            self.assertIn("PATH", detail)
        finally:
            _op_mod.shutil.which = orig


class WriteFailuresNamePermissions(_OpOnPath):
    """Verified against a real account: a service-account token with read access to a
    vault but not write fails with 1Password error (101). The first version of this
    message told the reader to check that `op` was signed in and the vault existed —
    both were true, so it pointed away from the actual cause."""

    def test_a_write_failure_mentions_permissions(self):
        rec = _Recorder([(0, _items()), (1, "")])
        with self.assertRaises(VaultError) as cm:
            _provider(rec).set("TOKEN", SECRET)
        self.assertIn("WRITE", str(cm.exception).upper())

    def test_a_delete_failure_mentions_permissions(self):
        rec = _Recorder([(0, _items("charter-devops-TOKEN")), (1, "")])
        with self.assertRaises(VaultError) as cm:
            _provider(rec).delete("TOKEN")
        self.assertIn("WRITE", str(cm.exception).upper())

    def test_a_read_failure_does_not_blame_permissions(self):
        """A miss is far more often a typo'd key than a rights problem."""
        rec = _Recorder([(1, "")])
        with self.assertRaises(SecretNotFound) as cm:
            _provider(rec).get("TOKEN")
        self.assertNotIn("WRITE", str(cm.exception).upper())
