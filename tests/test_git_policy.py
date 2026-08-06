"""Golden rule — ONE CREDENTIAL: every git op uses the glab token over HTTPS; no SSH keys,
no commit signing. Two halves: gitpolicy makes it mechanically true (local git config +
SSH→HTTPS rewrites), and the PreToolUse guard denies a deliberate bypass."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from charter import gitpolicy, hooks

_ENV = {"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"}
# built from parts so the literal never appears in a command this repo's own guard scans
SSH_SCP = "git" + "@gitlab.com:"
SSH_URL = "ssh://" + "git" + "@gitlab.com/"


class GitPolicyCase(unittest.TestCase):
    def setUp(self):
        self.repo = Path(tempfile.mkdtemp(prefix="edm-gitpol-"))
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True,
                       capture_output=True, env={**os.environ, **_ENV})
        self.addCleanup(lambda: shutil.rmtree(self.repo, ignore_errors=True))

    def _cfg(self, *args):
        return subprocess.run(["git", "-C", str(self.repo), "config", *args],
                              capture_output=True, text=True).stdout.strip()

    def test_fresh_repo_is_non_compliant(self):
        self.assertTrue(gitpolicy.check(self.repo))

    def test_apply_makes_it_compliant_and_is_idempotent(self):
        changed = gitpolicy.apply(self.repo)
        self.assertTrue(changed)
        self.assertEqual(gitpolicy.check(self.repo), [])
        self.assertEqual(gitpolicy.apply(self.repo), [])   # idempotent: nothing left to change

    def test_applies_token_helper_and_disables_signing(self):
        gitpolicy.apply(self.repo)
        self.assertEqual(self._cfg("--local", "credential.helper"), "!glab auth git-credential")
        self.assertEqual(self._cfg("--local", "commit.gpgsign"), "false")
        self.assertEqual(self._cfg("--local", "tag.gpgsign"), "false")

    def test_ssh_urls_rewrite_to_https(self):
        gitpolicy.apply(self.repo)
        got = subprocess.run(["git", "-C", str(self.repo), "config", "--get-all",
                              f"url.{gitpolicy.HTTPS_BASE}.insteadOf"],
                             capture_output=True, text=True).stdout.split()
        self.assertIn(SSH_SCP, got)
        self.assertIn(SSH_URL, got)

    def test_rewrite_actually_resolves_ssh_remote_over_https(self):
        gitpolicy.apply(self.repo)
        subprocess.run(["git", "-C", str(self.repo), "config",
                        "remote.probe.url", SSH_SCP + "grp/repo.git"], capture_output=True)
        resolved = subprocess.run(["git", "-C", str(self.repo), "ls-remote", "--get-url", "probe"],
                                  capture_output=True, text=True).stdout.strip()
        self.assertTrue(resolved.startswith("https://gitlab.com/"), resolved)

    def test_never_writes_global_config(self):
        """SCOPE INVARIANT — the policy touches the umbrella and its clones, NOTHING outside.
        Point git's global config at a sentinel file and prove apply() leaves it untouched."""
        sentinel = Path(tempfile.mkdtemp(prefix="edm-globalcfg-")) / "gitconfig"
        sentinel.write_text("")
        self.addCleanup(lambda: shutil.rmtree(sentinel.parent, ignore_errors=True))
        old = os.environ.get("GIT_CONFIG_GLOBAL")
        os.environ["GIT_CONFIG_GLOBAL"] = str(sentinel)
        try:
            gitpolicy.apply(self.repo)
        finally:
            os.environ.pop("GIT_CONFIG_GLOBAL", None) if old is None else \
                os.environ.__setitem__("GIT_CONFIG_GLOBAL", old)
        self.assertEqual(sentinel.read_text(), "", "policy leaked into GLOBAL git config")
        # …while the repo's own LOCAL config did get it
        local = subprocess.run(["git", "-C", str(self.repo), "config", "--local", "--list"],
                               capture_output=True, text=True).stdout
        self.assertIn("credential.helper", local)

    def test_source_never_references_global_or_system_scope(self):
        """Belt-and-braces: no code path may widen the scope beyond --local. Inspect real
        string LITERALS via AST — grepping the text would also match the module's own
        docstring warning about --global (prose is not an argument)."""
        import ast
        tree = ast.parse(Path(gitpolicy.__file__).read_text())
        widened = [n.value for n in ast.walk(tree)
                   if isinstance(n, ast.Constant) and isinstance(n.value, str)
                   and n.value in ("--global", "--system")]
        self.assertEqual(widened, [], "gitpolicy must only ever write --local config")

    def test_scope_is_umbrella_and_its_clones_only(self):
        """repos() must enumerate the umbrella + workspace clones — never anything else."""
        root = Path(tempfile.mkdtemp(prefix="edm-scope-"))
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
        ws = root / "workspaces" / "w"
        (ws / "repoA").mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(ws / "repoA")], check=True, capture_output=True)
        outside = root.parent / (root.name + "-outside")   # a sibling repo NOT under the umbrella
        outside.mkdir()
        subprocess.run(["git", "init", "-q", str(outside)], check=True, capture_output=True)
        self.addCleanup(lambda: shutil.rmtree(outside, ignore_errors=True))
        found = gitpolicy.repos(root, root / "workspaces")
        self.assertIn(root, found)
        self.assertIn(ws / "repoA", found)
        self.assertNotIn(outside, found)

    def test_non_compliant_lists_only_drifted_repos_in_scope(self):
        """Clones inside the umbrella are in scope — preflight uses this to catch a repo
        that was cloned manually (not via `edm clone`) and so never got the policy."""
        root = Path(tempfile.mkdtemp(prefix="edm-scan-"))
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
        ws = root / "workspaces" / "w"
        for name in ("compliant", "manual"):
            (ws / name).mkdir(parents=True)
            subprocess.run(["git", "init", "-q", str(ws / name)], check=True, capture_output=True)
        gitpolicy.apply(root)
        gitpolicy.apply(ws / "compliant")
        bad = gitpolicy.non_compliant(root, root / "workspaces")
        self.assertIn(ws / "manual", bad)          # the hand-cloned repo is flagged
        self.assertNotIn(ws / "compliant", bad)
        self.assertNotIn(root, bad)

    def test_local_config_reader_returns_multivalued_keys(self):
        gitpolicy.apply(self.repo)
        cfg = gitpolicy._local_config(self.repo)
        self.assertEqual(cfg["credential.helper"], ["!glab auth git-credential"])
        self.assertEqual(len(cfg[gitpolicy._URL_KEY.lower()]), 2)   # both SSH forms

    def test_non_git_dir_is_a_noop(self):
        plain = Path(tempfile.mkdtemp(prefix="edm-plain-"))
        self.addCleanup(lambda: shutil.rmtree(plain, ignore_errors=True))
        self.assertEqual(gitpolicy.apply(plain), [])


class SingleCredentialGuardCase(unittest.TestCase):
    """The PreToolUse deny — a golden rule every persona (and sub-agent) inherits."""

    def _deny(self, cmd):
        return hooks._single_credential_reason(cmd)

    def test_denies_ssh_gitlab_url_to_git(self):
        self.assertIsNotNone(self._deny(f"git clone {SSH_SCP}easydmarc/x.git"))
        self.assertIsNotNone(self._deny(f"git remote set-url origin {SSH_URL}easydmarc/x.git"))

    def test_denies_git_ssh_command_override(self):
        self.assertIsNotNone(self._deny("GIT_SSH_COMMAND='ssh -i ~/.ssh/id' git push origin main"))

    def test_denies_commit_signing(self):
        self.assertIsNotNone(self._deny("git commit -S -m 'x'"))
        self.assertIsNotNone(self._deny("git commit --gpg-sign -m 'x'"))
        self.assertIsNotNone(self._deny("git -c commit.gpgsign=true commit -m 'x'"))

    def test_denies_ssh_probe_to_gitlab(self):
        self.assertIsNotNone(self._deny("ssh -T " + "git" + "@gitlab.com"))

    def test_allows_normal_git_over_https(self):
        for ok in ("git push origin main", "git pull --ff-only", "git status",
                   "git clone https://gitlab.com/easydmarc/x.git", "git commit -m 'x'"):
            self.assertIsNone(self._deny(ok), ok)

    def test_allows_pickaxe_search_not_mistaken_for_signing(self):
        # `git log -S<string>` is a content search — must NOT be read as commit signing
        self.assertIsNone(self._deny("git log -S'needle' --oneline"))
        self.assertIsNone(self._deny("git log -S needle"))

    def test_allows_ssh_to_other_hosts(self):
        # devops SSH to a server is unrelated to the git credential rule
        self.assertIsNone(self._deny("ssh deploy@prod-box-01 'uptime'"))
        self.assertIsNone(self._deny("ssh -i ~/.ssh/key ubuntu@10.0.0.5"))

    def test_reason_names_the_remedy(self):
        reason = self._deny("git clone " + SSH_SCP + "e/x.git")
        self.assertIn("token", reason.lower())

    # --- regression: prose must not trip the guard (it denied a real commit whose MESSAGE
    # --- merely mentioned the SSH form). Only actual git/ssh invocations are inspected.
    def test_allows_ssh_url_mentioned_inside_a_commit_message(self):
        self.assertIsNone(self._deny(
            f"git commit -m 'docs: {SSH_SCP}x now rewrites to https://gitlab.com/x'"))

    def test_allows_ssh_url_inside_a_non_git_program(self):
        self.assertIsNone(self._deny(
            f"python3 -c \"print('verified: {SSH_SCP}repo resolves over https')\""))

    def test_allows_writing_docs_that_mention_ssh(self):
        self.assertIsNone(self._deny(f"echo 'never use {SSH_SCP}repo' >> docs/access.md"))

    def test_still_denies_the_real_thing_in_a_chained_segment(self):
        self.assertIsNotNone(self._deny(f"cd /tmp && git remote add o {SSH_SCP}e/x.git"))

    def test_allows_git_log_dash_S_pickaxe_in_segments(self):
        self.assertIsNone(self._deny("cd repo && git log -S'token' --oneline"))


class TestPerForgePolicy(unittest.TestCase):
    """Golden rule 0 restated: ONE CREDENTIAL PER FORGE. The invariant was never the
    `glab` binary — it is *the forge's CLI holds the token, git speaks HTTPS, never SSH,
    never signing*. `gh auth git-credential` satisfies it identically."""

    def test_each_forge_contributes_its_own_helper(self):
        from charter import gitpolicy
        from charter.forge.github import GitHubForge
        from charter.forge.gitlab import GitLabForge
        self.assertIn("glab", gitpolicy.policy_for(GitLabForge())["credential.helper"])
        self.assertIn("gh", gitpolicy.policy_for(GitHubForge())["credential.helper"])

    def test_signing_stays_off_for_every_forge(self):
        from charter import gitpolicy
        from charter.forge.github import GitHubForge
        from charter.forge.gitlab import GitLabForge
        for f in (GitLabForge(), GitHubForge()):
            p = gitpolicy.policy_for(f)
            self.assertEqual(p["commit.gpgsign"], "false")
            self.assertEqual(p["tag.gpgsign"], "false")

    def test_no_policy_value_mentions_ssh(self):
        from charter import gitpolicy
        from charter.forge.github import GitHubForge
        from charter.forge.gitlab import GitLabForge
        for f in (GitLabForge(), GitHubForge()):
            for v in gitpolicy.policy_for(f).values():
                self.assertNotIn("ssh", v.lower())


if __name__ == "__main__":
    unittest.main()
