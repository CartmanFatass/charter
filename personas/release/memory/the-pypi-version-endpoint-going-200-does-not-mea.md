# The PyPI version endpoint going 200 does NOT mean the install will work:

_2026-08-18 22:57 · persistent_

The PyPI version endpoint going 200 does NOT mean the install will work: at 0.44.0 the version endpoint answered immediately while 'uv tool install --force --refresh charter-cp==0.44.0' still failed with 'no version of charter-cp==0.44.0 ... requirements are unsatisfiable'. --refresh does not help, because the lag is in the simple index, not the local cache. Gate the upgrade on the index itself: poll 'curl -s https://pypi.org/simple/charter-cp/ | grep charter_cp-<X.Y.Z>-py3-none-any.whl' until it appears, then install. It cleared in well under a minute.
