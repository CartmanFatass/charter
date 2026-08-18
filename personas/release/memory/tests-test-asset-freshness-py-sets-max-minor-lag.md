# tests/test_asset_freshness.py sets MAX_MINOR_LAG = 1, so a capture may l

_2026-08-18 22:57 · persistent_

tests/test_asset_freshness.py sets MAX_MINOR_LAG = 1, so a capture may lag exactly one minor. At 0.44.0 demo.svg and statusline.svg are stamped 0.43.0 in docs/assets/captured.json — passing at exactly the limit. 0.45.0 WILL FAIL that test until those captures are regenerated (docs/assets/README.md) and captured.json updated. Check it before opening a minor-release PR, not after CI goes red.
