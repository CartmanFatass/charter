# The plane's own repo should ALWAYS be a clone target, without discover. 

_2026-08-12 15:06 · persistent_

The plane's own repo should ALWAYS be a clone target, without discover. Derive it from the root's origin so 'charter clone <root-repo>' works on a fresh plane, making discover optional rather than a prerequisite. Falls out of ADR 0008: you no longer work in the plane root, so the root repo must be clonable — and on a personal account discover enumerates 63 repos to find the one you actually wanted.
