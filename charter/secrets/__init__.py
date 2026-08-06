"""Provider-agnostic secret management for the umbrella.

A small abstraction over *vault providers* (plain-file today; more providers
can be added later) so agents — and, in future, personas — can store and
**use** secrets without the plaintext ever passing through the model.

Design goals:

- Many vaults, many providers, in parallel. A *vault* is a named provider
  instance; a *provider* is an implementation type.
- Secrets are consumed, not exposed: values are injected into subprocesses
  (``secret exec``) or materialized to 0600 files (``secret cp``); plaintext is
  only ever printed on an explicit, human, interactive ``--reveal``.
- Ready for personas: every vault carries an optional ``persona`` tag.
"""

from .base import (  # noqa: F401
    ProviderUnavailable,
    SecretNotFound,
    VaultError,
    VaultNotConfigured,
    VaultProvider,
    redact,
)
