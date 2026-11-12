#!/usr/bin/env python3
"""Put .env files into Bitwarden Secrets Manager as APP__NAME secrets.

  env_to_bitwarden.py --write [--project dev] [--skip REGEX] APP=PATH[,PATH...] ...
  env_to_bitwarden.py --out FILE [--project dev] [--skip REGEX] APP=PATH[,PATH...] ...

Every variable NAME from the files given for APP becomes the secret APP__NAME
(uppercased, "-" becomes "_"). Later files for the same APP override earlier
ones; --skip leaves out names matching REGEX (e.g. 'PROD|LIVE'). In a production
project (`prod`, `production`, or a name starting or ending with one of them, like
`prod-eu`), the app becomes APP_PROD (secrets APP_PROD__NAME, read with `secrets
app_prod`) unless its name already ends in _prod, so production values never share
a name with development ones; --no-prod-prefix keeps APP__NAME there.

--write creates the secrets in the existing project (default dev) with a
read-write machine-account token from ~/.config/agentbox/bws-admin.env
(BWS_ADMIN_ENV), kept on the laptop only; a name already in the project is
left as it is. --out writes an import file instead, for the web app (Secrets
Manager → Settings → Import data); an import always creates a new project, so
the secrets then have to be moved into yours. Only names are printed, never
values.

Exposure: --write hands each value to Bitwarden's `bws` as a command-line
argument (bws has no other way to take one), so other processes on this machine
can see it in the process list for the moment bws runs. Run it on the laptop
while nothing else untrusted runs there, and use it for the initial import only;
later changes go through the Bitwarden web app. --out doesn't run bws.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
import uuid

LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")


def parse_env(path):
    values = {}
    with open(path, errors="replace") as fh:
        for line in fh:
            m = LINE.match(line)
            if not m:
                continue
            value = m.group(2).strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            elif " #" in value:  # trailing comment on an unquoted value
                value = value.split(" #", 1)[0].rstrip()
            values[m.group(1)] = value
    return values


ADMIN_ENV = os.environ.get("BWS_ADMIN_ENV", os.path.expanduser("~/.config/agentbox/bws-admin.env"))


def hide(text, values):
    """text with every secret value, and each line of a multi-line one, replaced: bws
    error messages can quote the arguments they were given."""
    for v in values:
        for part in sorted({v, *v.splitlines()}, key=len, reverse=True):
            if len(part) >= 6:
                text = text.replace(part, "<value hidden>")
    return re.sub(r"[A-Za-z0-9+/=_-]{24,}", "<hidden>", text)


def bws(env, group, action, *args, values=()):
    """Run `bws GROUP ACTION ARGS` with the admin token; its JSON output, or exit with its
    error, secret values hidden. Positional ARGS come after `--`, so a value that starts
    with "-" (a PEM key) can't be read as an option and echoed back in an error.
    Bitwarden rate-limits bursts, so a failure is retried a few times, slower each time."""
    opts = [a for a in args if isinstance(a, tuple)]
    cmd = ["bws", group, action, "--output", "json", *[x for o in opts for x in o], "--",
           *[a for a in args if not isinstance(a, tuple)]]
    for wait in (5, 15, 30, None):
        r = subprocess.run(cmd, capture_output=True, text=True, env=env)
        if r.returncode == 0:
            return json.loads(r.stdout)
        if wait is None:
            sys.exit(f"bws {group} {action} failed: {hide(r.stderr.strip(), values)[-300:]}\n"
                     "Run it again: names already created are skipped.")
        time.sleep(wait)


def write(secrets, project):
    """Create the secrets in the existing project; names already there stay as they are."""
    if not os.path.exists(ADMIN_ENV):
        sys.exit(f"No read-write token in {ADMIN_ENV} (BWS_ACCESS_TOKEN=..., BWS_SERVER_URL=...).")
    env = dict(os.environ, **parse_env(ADMIN_ENV))
    matches = [p for p in bws(env, "project", "list") if p["name"] == project]
    if len(matches) != 1:
        sys.exit(f"{len(matches)} projects named {project!r} are visible to this token; expected exactly one.")
    pid = matches[0]["id"]
    existing = {s["key"] for s in bws(env, "secret", "list", pid)}
    created, kept = [], []
    for key, value, note in secrets:
        if key in existing:
            kept.append(key)
            continue
        bws(env, "secret", "create", key, value, pid, ("--note", note), values=(value,))
        created.append(key)
    print(f"\nCreated {len(created)} secret(s) in project '{project}'" + (f": {', '.join(created)}" if created else ""))
    if kept:
        print(f"Already there, left as they are: {', '.join(kept)}")


# The same rules as server/secrets (prefix_of, PROD_PROJECTS); the offline tests keep them equal.
PROD_PROJECTS = re.compile(r"^(prod|production)([-_ ].*)?$|^.*[-_ ](prod|production)$", re.I)


def prefix_of(app):
    return app.upper().replace("-", "_") + "__"


def secret_app(app, project, keep_names=False):
    """The app name the secrets are written under: APP_PROD in a production project."""
    if keep_names or not PROD_PROJECTS.match(project) or app.lower().replace("-", "_").endswith("_prod"):
        return app
    return app + "_prod"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    how = ap.add_mutually_exclusive_group(required=True)
    how.add_argument("--write", action="store_true", help="create the secrets in the existing project")
    how.add_argument("--out", help="write an import JSON file instead (chmod 600)")
    ap.add_argument("--project", default="dev", help="Bitwarden project name (default: dev)")
    ap.add_argument("--skip", help="leave out variable names matching this regex")
    ap.add_argument("--no-prod-prefix", action="store_true",
                    help="in a production project, keep APP__NAME instead of APP_PROD__NAME")
    ap.add_argument("apps", nargs="+", metavar="APP=PATH[,PATH...]")
    args = ap.parse_args()

    skip = re.compile(args.skip) if args.skip else None
    secrets = []   # (key, value, note)
    for spec in args.apps:
        app, _, paths = spec.partition("=")
        if not paths:
            sys.exit(f"Expected APP=PATH, got {spec!r}")
        named = secret_app(app, args.project, args.no_prod_prefix)
        if named != app:
            print(f"{app}: {args.project} is a production project, so the secrets are named "
                  f"{prefix_of(named)}NAME (read with: secrets {named.lower()}; --no-prod-prefix keeps {prefix_of(app)}NAME)")
        prefix = prefix_of(named)
        merged, overridden = {}, set()
        for path in paths.split(","):
            for k, v in parse_env(os.path.expanduser(path)).items():
                if k in merged and merged[k] != v:
                    overridden.add(k)
                merged[k] = v
        skipped = sorted(k for k in merged if skip and skip.search(k))
        empty = sorted(k for k, v in merged.items() if v == "" and k not in skipped)
        for k, v in sorted(merged.items()):
            if v != "" and k not in skipped:
                secrets.append((prefix + k, v, f"from {paths}"))
        print(f"{prefix:<22} {len(merged) - len(empty) - len(skipped):>3} secrets  from {paths}")
        if overridden:
            print(f"{'':<22} overridden by a later file: {', '.join(sorted(overridden))}")
        if skipped:
            print(f"{'':<22} skipped (--skip): {', '.join(skipped)}")
        if empty:
            print(f"{'':<22} skipped (empty): {', '.join(empty)}")

    if args.write:
        write(secrets, args.project)
        return
    project_id = str(uuid.uuid4())
    fd = os.open(args.out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump({"projects": [{"id": project_id, "name": args.project}],
                   "secrets": [{"id": str(uuid.uuid4()), "key": k, "value": v, "note": n, "projectIds": [project_id]}
                               for k, v, n in secrets]}, fh, indent=2)
    print(f"\n{len(secrets)} secrets in a new project '{args.project}' written to {args.out}")
    print("Import it under Secrets Manager → Settings → Import data, then delete the file. The import creates")
    print("a new project; move the secrets into yours if you want them there (--write does that directly).")


if __name__ == "__main__":
    main()
