"""Charter-keyword lint: a persona's `delegate-when` must still advertise the task it owns.

**This is a lint, not a routing eval.** It checks that the *advertisement* exists — that
the owner's `delegate-when` contains a keyword the router could match on. It cannot see
whether anybody ever answered the ad.

That distinction was learned the hard way. Under its old name (`test_routing_eval.py`)
this file carried the case::

    ("review my NX monorepo code", "nx-code-reviewer", ["review", "code"]),

and passed green every day for the two weeks `nx-code-reviewer` was dispatched **zero**
times while 42 review-shaped tasks went to a generic sub-agent. A green keyword lint said
nothing about routing, but was read as if it did.

Whether routing actually happens is measured, not asserted: the committed dispatch tally
(`edm/dispatch.py`) counts real dispatches, and `edm persona stats` flags a persona that
has never been dispatched. Keep the two jobs separate — this file guards the wording of a
charter; the tally guards the behaviour.
"""
import unittest

from charter import persona

# (task phrase, owning persona, any-of these keywords must appear in its delegate-when)
GOLDEN = [
    ("deploy to kubernetes / fix a CI pipeline", "devops", ["kubernetes", "k8s", "ci", "deploy"]),
    ("write e2e tests / verify a bug report", "qa", ["test", "e2e", "bug", "verif"]),
    ("prod is slow, investigate the latency", "performance-investigator", ["latency", "root", "slow", "prod", "rca"]),
    ("implement a NestJS controller/service", "nestjs-developer", ["nestjs", "controller", "service"]),
    ("keycloak tenant authz migration question", "platform-and-authz-master", ["keycloak", "migration", "tenant", "authz"]),
    ("spin up a mailbox and send an email", "agentic-email-manager", ["mailbox", "inbox", "email"]),
    ("a public API route / OpenAPI doc change", "public-api-master", ["public-api", "public api", "apisix", "openapi"]),
    ("why is this billing endpoint 403", "billing-master", ["billing", "subscription", "plan", "payment"]),
]


class TestCharterKeywords(unittest.TestCase):
    def test_owner_advertises_the_task(self):
        names = set(persona.list_personas())
        for phrase, owner, keywords in GOLDEN:
            if owner not in names:
                continue  # not present in this checkout — skip
            dw = (persona.load(owner)["meta"].get("delegate-when") or "").lower()
            self.assertTrue(
                any(k in dw for k in keywords),
                f"'{owner}' delegate-when advertises none of {keywords} for task '{phrase}': {dw!r}",
            )

    def test_golden_does_not_name_a_removed_persona(self):
        """A case for a deleted persona silently skips — which is how the nx-code-reviewer
        blind spot survived. Fail loudly instead."""
        names = set(persona.list_personas())
        missing = sorted({owner for _, owner, _ in GOLDEN} - names)
        self.assertEqual(missing, [],
                         f"GOLDEN names persona(s) that no longer exist: {missing} — "
                         f"remove the case or restore the persona.")


if __name__ == "__main__":
    unittest.main()
