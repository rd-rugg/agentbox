# Secrets

Every secret lives in Bitwarden Secrets Manager. On the box, the `secrets` command
reads them into memory; nothing secret is written to the box's disk or its snapshots.
The box's own Bitwarden token is read-only.

## Using them

```bash
secrets myapp -- npm run dev          # run with myapp's variables set
secrets myapp                         # list the names (never the values)
secrets myapp --env-file .env         # for tools that insist on a .env file: written to RAM, linked here
```

The same commands work on your laptop. A project's `.env` files can also be listed in
its `agentbox.json`; the box then writes them into memory from Bitwarden.

## Naming

| Name in Bitwarden | Meaning |
|---|---|
| `MYAPP__DATABASE_URL` | `DATABASE_URL` for the app `myapp` |
| `MYAPP_PROD__DATABASE_URL` | the same for production, reached only with `secrets myapp_prod` |
| `MYAPP__FILE__GOOGLE_CREDENTIALS` | a file (a JSON key, a certificate): written to memory, and the variable holds its path |
| `CLI__NAME` | a token for the tools: `CLI__TS_AUTHKEY` (Tailscale, required), `CLI__GH_TOKEN`, `CLI__CLAUDE_TOKEN_DEFAULT` |

Instead of the `MYAPP__` prefix you can give an app its own Bitwarden project named
`myapp`, holding plain `DATABASE_URL`.

## Keep production apart

Use three Bitwarden projects: `master` for the `CLI__` tool tokens, `dev` for each app's
development values, and `prod` for production values with `_PROD` in their names.
`secrets` reads every project its token can see, so different names are what keep an
agent from getting production values while it thinks it works on development. When the
same name turns up with two different values, `secrets` stops and names them instead of
guessing.

Whether the boxes' token may read `prod` is your decision. If it can, agents on the box
can deploy and check production themselves, and they can also reach production data.

## Moving your .env files into Bitwarden

`./env_to_bitwarden.py --write myapp=path/to/.env` (in the agentbox folder) creates the
secrets as `MYAPP__NAME`. It needs a second, read-write machine-account token in
`~/.config/agentbox/bws-admin.env`, which stays on your laptop. Without it, `--out FILE`
writes a file for Bitwarden's import page instead.

## The commit check

Every repository on the box refuses a commit that contains a `.env` file or any value
stored in Bitwarden. For a real false positive: `git commit --no-verify`.
