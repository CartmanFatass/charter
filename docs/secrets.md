# Secrets: the vault

`charter` has a small, provider-agnostic secret manager: **vaults**, addressed via
`charter vault …` and `charter secret …` (or, scoped to a role, `charter persona
secret …`). Read this page before storing anything real in one.

## What it actually is — plainly

**The vault is not a password manager, and it is not a secrets manager in the sense
1Password or Vault are.** The built-in `plain-file` provider — the only one implemented
today — stores secrets as **plaintext JSON on disk**, at file mode **0600** (owner
read/write only). There is **no encryption at rest**. Anyone with read access to your
user account, or a backup of your home directory, or a malicious process running as
you, can read the file directly. `charter` does not pretend otherwise: the vault
registry and every vault file live under `.charter/` (gitignored — never committed, never
synced anywhere by `charter` itself).

If you want encryption at rest, use your **OS keychain** (macOS Keychain, a real
password manager, or a proper secrets backend) to hold the credential, and treat
`charter`'s vault as a *thin, disposable staging area* your agent reads from — or wait
for a keychain-backed provider (the `VaultProvider` interface is designed so one can be
added without touching call sites; none ships yet).

## What it genuinely does

What the vault protects against is a different, narrower, and very real threat: an
**AI agent's own conversation** is not a safe place for a credential. Every message an
agent reads, every tool result, every line it prints, can end up in a **transcript** —
saved, logged, reviewed, or (worse) fed back into a later prompt. The vault's actual job
is keeping a secret value **out of the model's context, the terminal transcript, and
shell history**, while still letting an agent *use* the credential:

- **`charter secret exec`** runs a command with secrets injected as environment
  variables or temp files that the agent names only by *key*, never by value, and
  **redacts** every occurrence of the resolved value from the command's captured
  output before anything is printed:

  ```
  charter secret exec devops --env TOKEN=API_TOKEN -- curl -H "Authorization: Bearer $TOKEN" https://…
  ```

- **`charter secret cp`** materializes a secret to a 0600 file (e.g. a kubeconfig) and
  prints only the path, never the contents.
- **`charter secret get`** is masked by default — it prints a byte count and a SHA-256
  fingerprint, never the value.
- **`charter secret get --reveal`** is the one path that *can* print plaintext, and it
  deliberately refuses to do so to a **non-interactive stdout** (the exact channel
  through which a value would leak straight into an agent's context) unless you pass
  `--force` — it's meant for a human at a real terminal, not a script or an agent.
- Values are always **written** via `--stdin` or `--from-file`, never as a bare CLI
  argument — an argument shows up in shell history and `ps` output for any other
  process on the machine to read.
- A Claude Code guard hook denies `--reveal` outright, and denies reading a vault file
  directly (`cat .charter/vaults/…`) — both would print a secret straight into the
  conversation. **A denial here is that guard working, not a bug** — see the README's
  "one credential" section for the same idea applied to git auth.

## Setting one up

```
charter vault add devops --provider plain-file --file .charter/vaults/devops.json --persona devops
charter secret set devops API_TOKEN --stdin
charter secret list devops                 # keys only, never values
charter secret audit devops --days 90       # flag anything old enough to rotate
```

Or via a persona (`charter persona create --with-vault` already does the `vault add`
step for you): once a vault is tagged with `--persona <name>`, `charter persona
secret …` resolves it automatically — no vault name needed on every call:

```
charter vault add devops --provider plain-file --persona devops
charter persona secret set API_TOKEN --stdin        # resolves the active persona's vault
charter persona secret exec --env TOKEN=API_TOKEN -- some-cli
```

## Provider status

| Provider | id | Status |
| --- | --- | --- |
| Plain file (JSON, 0600) | `plain-file` | Implemented — the only provider that ships today. |

The interface (`charter.secrets.base.VaultProvider`) is deliberately small (`get`,
`set`, `delete`, `keys`, `health`) so a keychain- or vault-backed provider can be added
later without touching any call site above it — `charter vault add --provider <x>`
already accepts an unimplemented provider and reports it as "registered for later use"
rather than crashing.
