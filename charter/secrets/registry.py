"""The vault registry: which vaults exist, their provider + config + persona.

Persisted at ``.edm/vaults.json`` (0600, gitignored). This file may reference
provider config such as file paths or token locations, so it is treated as
sensitive and never committed.
"""

from __future__ import annotations

import json
import os

from .. import config
from .base import VaultError, VaultNotConfigured, VaultProvider
from .plain_file import PlainFileProvider

#: provider id -> implementation class. Add real providers here as they land.
PROVIDERS: dict[str, type[VaultProvider]] = {
    cls.id: cls
    for cls in (
        PlainFileProvider,
    )
}


def load_registry() -> dict:
    if not config.VAULTS_REGISTRY.exists():
        return {"vaults": {}}
    try:
        doc = json.loads(config.VAULTS_REGISTRY.read_text())
    except json.JSONDecodeError as e:
        raise VaultError(f"vault registry {config.VAULTS_REGISTRY} is corrupt: {e}")
    doc.setdefault("vaults", {})
    return doc


def save_registry(doc: dict) -> None:
    config.EDM_HOME.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(config.VAULTS_REGISTRY), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(doc, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.chmod(config.VAULTS_REGISTRY, 0o600)


def vaults(doc: dict | None = None) -> dict:
    return (doc or load_registry()).get("vaults", {})


def get_vault_config(name: str, doc: dict | None = None) -> dict:
    vc = vaults(doc).get(name)
    if not vc:
        raise VaultNotConfigured(
            f"no vault named '{name}'. Register one with `charter vault add {name} "
            f"--provider plain-file --file <path>`."
        )
    return vc


def provider_for(name: str, doc: dict | None = None) -> VaultProvider:
    vc = get_vault_config(name, doc)
    pid = vc.get("provider")
    cls = PROVIDERS.get(pid)
    if not cls:
        raise VaultError(f"vault '{name}' uses unknown provider '{pid}'")
    return cls(name, vc.get("config", {}))


def add_vault(name: str, provider: str, cfg: dict, persona: str | None = None) -> None:
    if provider not in PROVIDERS:
        raise VaultError(
            f"unknown provider '{provider}'. Available: {', '.join(sorted(PROVIDERS))}"
        )
    doc = load_registry()
    doc["vaults"][name] = {"provider": provider, "persona": persona, "config": cfg}
    save_registry(doc)


def remove_vault(name: str) -> None:
    doc = load_registry()
    if name not in doc["vaults"]:
        raise VaultNotConfigured(f"no vault named '{name}'")
    del doc["vaults"][name]
    save_registry(doc)


def vaults_for_persona(persona: str, doc: dict | None = None) -> list[str]:
    return sorted(n for n, v in vaults(doc).items() if v.get("persona") == persona)
