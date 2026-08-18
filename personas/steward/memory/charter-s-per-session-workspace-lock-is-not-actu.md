# charter's per-session workspace lock is NOT actually immutable: this ses

_2026-08-18 14:05 · persistent_

charter's per-session workspace lock is NOT actually immutable: this session locked 'showcase' via 'charter workspace use', and both .charter/sessions/<sid>.lock and .workspace were later found holding a workspace never selected this session (mtime unchanged at the original write time — so the rewriter preserves or restores mtime). Filed as issue #254. The reason it looks like a harmless flicker: resolve()'s cwd rung outranks the session pointer, so the status line reads correctly whenever cwd is inside workspaces/<ws>/..., and only shows the wrong task from the plane root or outside the plane. When a workspace looks wrong, instrument the rungs (workspace.from_path / _session_file / _read / declared_default) rather than trusting 'charter workspace current', which can answer from a different rung than the status line does.
