# Decision: discover should be optional. On a personal account it enumerat

_2026-08-12 15:06 · persistent_

Decision: discover should be optional. On a personal account it enumerates every repo (63 here) to surface the one that matters, and writes inventory/repos.json into a repo that is not gitignored — a disclosure with little upside. The plane's own repo is knowable without it, from the root's origin remote. Deliberately NOT running discover on this plane.
