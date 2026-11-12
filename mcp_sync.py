#!/usr/bin/env python3
"""Your MCP servers on the box: the definitions travel, credentials never do.

  mcp_sync.py sync [--agents "claude codex"] [--quiet]   register them on the box (agent_setup.py runs it)
  mcp_sync.py status [--agents "claude codex"]           which servers go to the box, and why others don't

What travels: the servers you added for every project on the laptop: Claude Code's
user scope (`claude mcp add --scope user`, top-level "mcpServers" in ~/.claude.json)
and Codex's [mcp_servers.*] in ~/.codex/config.toml. Project servers (.mcp.json)
come with git, plugins bring their own, and claude.ai connectors come with your
Claude account. Local-scope servers (one laptop folder only) stay on the laptop.

Credentials never travel. A header or environment value that looks like a secret
is read on the box from Bitwarden instead, as CLI__MCP_<SERVER>_<NAME> (a header
helper or a wrapper fetches it into memory when the server starts); until that
secret exists, the server stays laptop-only and the sync says which name to add.
Sign-ins (OAuth) are per box: run /mcp in a box session once; agentbox forwards
the callback port so the browser on your laptop reaches the box. Servers that
point at this laptop (localhost) or whose command or files the box doesn't have
are reported, not copied. Only standard-library Python 3.8+.
"""
import json
import math
import os
import re
import subprocess
import sys

import projects
from projects import BOX, BOX_HOME

HOME = os.path.expanduser("~")
CLAUDE_JSON = os.path.join(os.environ.get("CLAUDE_CONFIG_DIR", HOME), ".claude.json")
CODEX_TOML = os.path.join(os.environ.get("CODEX_HOME", os.path.join(HOME, ".codex")), "config.toml")
RESERVED = {"playwright"}   # the box sets up its own browser tools (BOX_PLAYWRIGHT; agentbox-browser)
# Words in a header or variable name that mean it holds a credential (whole words, so
# KEYBOARD_LAYOUT or BYPASS_CACHE don't count).
SECRET_WORDS = {"auth", "authorization", "token", "key", "apikey", "secret", "password", "passwd", "pass", "pwd",
                "cookie", "credential", "credentials", "bearer", "signature", "private", "pat", "sid"}
# How common services' keys start (GitHub, Slack, OpenAI/Anthropic, AWS, Linear, Notion, JWTs, Stripe).
KEY_PREFIX = re.compile(r"^(sk-|sk_|pk_|rk_|ghp_|gho_|ghs_|github_pat_|glpat-|xox[abpr]-|AKIA|lin_api_|ntn_|secret_|eyJ)")
LOCAL_URL = re.compile(r"^\w+://(localhost|127\.\d+\.\d+\.\d+|0\.0\.0\.0|\[::1\])(:\d+)?(/|$)", re.I)


# Reasons a server stays on the laptop that nothing can change (box doctor doesn't flag them).
BY_DESIGN = ("agentbox sets up its own on the box (BOX_PLAYWRIGHT=1, the project browser of box browser)", "points at this laptop (localhost)")


def secret_name(server, field):
    """The Bitwarden secret (without CLI__) holding one value of one server."""
    return "MCP_" + re.sub(r"[^A-Za-z0-9]", "_", f"{server}_{field}").upper()


def name_words(name):
    """KEY_ID, X-Api-Key, apiKey -> {"key", "id"}, {"x", "api", "key"}, {"api", "key"}."""
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", str(name))
    return {w.lower() for w in re.split(r"[^A-Za-z0-9]+", spaced) if w}


def random_looking(v):
    """A value that reads like a key: a known key prefix, or long and high-entropy with
    no spaces, and not a flag, path or plain URL."""
    v = str(v)
    if KEY_PREFIX.match(v):
        return True
    if len(v) < 20 or " " in v or v.startswith(("-", "/", "~", "http://", "https://", "@")):
        return False
    counts = {c: v.count(c) for c in set(v)}
    entropy = -sum(n / len(v) * math.log2(n / len(v)) for n in counts.values())
    return entropy >= 3.5 and bool(re.search(r"\d", v)) and bool(re.search(r"[A-Za-z]", v))


def looks_secret(name, value):
    """Whether a header or variable may hold a credential."""
    if value is None or str(value) == "":
        return False
    v = str(value)
    if "${" in v or re.match(r"(bearer|basic|token)\s", v, re.I) or re.search(r"://[^/@\s]+:[^/@\s]+@", v):
        return True
    if name_words(name) & SECRET_WORDS:
        return not re.fullmatch(r"(true|false|yes|no|on|off|\d{1,6})", v, re.I) and not v.startswith(("/", "~"))
    return random_looking(v)


def url_problem(url):
    """Why a server's URL can't go to the box, or None."""
    url = url or ""
    if LOCAL_URL.match(url):
        return BY_DESIGN[1]
    if re.match(r"^\w+://[^/@]+@", url):
        return "its URL holds a user name or password"
    rest = url.split("://", 1)[-1]
    path, _, query = rest.partition("?")
    for part in query.split("&"):
        k, _, v = part.partition("=")
        if v and looks_secret(k, v):
            return f"its URL holds a credential ({k}=...); give the server a header instead"
    if any(random_looking(seg) for seg in path.split("/")[1:]):
        return "its URL holds what looks like a key in the path"
    return None


def secret_args(server, args):
    """{index: Bitwarden name} for arguments that hold a credential: the value after a
    secret-named flag (--api-key VALUE), a --flag=VALUE, or a key-looking argument."""
    found = {}
    for i, a in enumerate(args):
        a = str(a)
        flag, eq, val = a.partition("=")
        if a.startswith("-") and eq and name_words(flag) & SECRET_WORDS and val:
            found[i] = secret_name(server, flag.lstrip("-"))
        elif i and str(args[i - 1]).startswith("-") and "=" not in str(args[i - 1]) \
                and name_words(args[i - 1]) & SECRET_WORDS and not a.startswith("-"):
            found[i] = secret_name(server, str(args[i - 1]).lstrip("-"))
        elif random_looking(a):
            found[i] = secret_name(server, f"arg{i}")
    return found


def box_path(value):
    """A laptop home path as it would be on the box."""
    return BOX_HOME + value[len(HOME):] if isinstance(value, str) and (value == HOME or value.startswith(HOME + "/")) else value


# --- Codex's config.toml: just enough TOML for [mcp_servers.*] (Python 3.8+ has no tomllib) ---

def _toml_value(text, i):
    """Parse one TOML value at text[i]; returns (value, next index)."""
    while text[i] in " \t":
        i += 1
    c = text[i]
    if text.startswith(('"""', "'''"), i):
        raise ValueError("multi-line strings aren't supported")
    if c == '"':
        j = i + 1
        while text[j] != '"':
            j += 2 if text[j] == "\\" else 1
        return json.loads(text[i:j + 1]), j + 1
    if c == "'":
        j = text.index("'", i + 1)
        return text[i + 1:j], j + 1
    if c in "[{":
        close, out, i = ("]" if c == "[" else "}"), ([] if c == "[" else {}), i + 1
        while True:
            while text[i] in " \t\r\n,":
                i += 1
            if text[i] == "#":
                i = text.index("\n", i)
                continue
            if text[i] == close:
                return out, i + 1
            if close == "]":
                v, i = _toml_value(text, i)
                out.append(v)
            else:
                key, i = _toml_key(text, i)
                v, i = _toml_value(text, i)
                _set(out, key, v)
    m = re.compile(r"true|false|[+-]?[\d_]+(\.[\d_]+)?([eE][+-]?\d+)?").match(text, i)
    if not m:
        raise ValueError(f"unsupported TOML value at {text[i:i + 20]!r}")
    s = m.group(0)
    return (s == "true" if s in ("true", "false") else float(s.replace("_", "")) if m.group(1) or m.group(2)
            else int(s.replace("_", ""))), m.end()


def _toml_key(text, i):
    """A (dotted) key followed by '='; returns (parts, index after '=')."""
    parts = []
    while True:
        while text[i] in " \t":
            i += 1
        if text[i] in "\"'":
            k, i = _toml_value(text, i)
        else:
            m = re.compile(r"[A-Za-z0-9_-]+").match(text, i)
            k, i = m.group(0), m.end()
        parts.append(k)
        while text[i] in " \t":
            i += 1
        if text[i] == ".":
            i += 1
            continue
        if text[i] != "=":
            raise ValueError("expected '='")
        return parts, i + 1


def _header(line):
    """The dotted name of a [table] header."""
    parts, inner = [], line.strip()[1:-1]
    for m in re.finditer(r'\s*("(?:\\.|[^"\\])*"|\'[^\']*\'|[A-Za-z0-9_-]+)\s*(\.|$)', inner):
        tok = m.group(1)
        parts.append(json.loads(tok) if tok[0] == '"' else tok.strip("'"))
    return parts


def _set(table, keys, value):
    for k in keys[:-1]:
        table = table.setdefault(k, {})
    table[keys[-1]] = value


def codex_servers(path=CODEX_TOML):
    """{name: config} from the [mcp_servers.*] tables ({} without the file). A server
    whose table this reader can't follow is None, so it's reported, not half-copied."""
    try:
        with open(path) as fh:
            text = fh.read()
    except OSError:
        return {}
    try:
        import tomllib   # Python 3.11+
    except ImportError:
        return parse_mcp_tables(text)
    try:
        return tomllib.loads(text).get("mcp_servers", {})
    except ValueError:
        return {}


def parse_mcp_tables(text):
    """The [mcp_servers.*] tables of a config.toml, for Pythons without tomllib: tables,
    dotted keys, strings, numbers, booleans, arrays and inline tables."""
    servers, name, table = {}, None, None
    lines = (text + "\n").split("\n")
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        i += 1
        if not line or line.startswith("#"):
            continue
        if line.startswith("["):
            parts = [] if line.startswith("[[") else _header(line[:line.rindex("]") + 1])
            name = parts[1] if len(parts) >= 2 and parts[0] == "mcp_servers" else None
            table = None
            if name is not None and servers.get(name, {}) is not None:
                table = servers.setdefault(name, {})
                for k in parts[2:]:
                    table = table.setdefault(k, {})
            continue
        if name is None and not line.startswith("mcp_servers."):
            continue
        # A value may span lines (arrays, inline tables): parse from here on.
        rest = "\n".join(lines[i - 1:])
        try:
            key, j = _toml_key(rest, 0)
            value, j = _toml_value(rest, j)
        except (ValueError, IndexError, AttributeError):
            if name is not None:
                servers[name], table = None, None
            continue
        i += rest.count("\n", 0, j)
        if name is None:   # mcp_servers.x.url = ... outside any table
            if servers.get(key[1], {}) is not None and len(key) >= 3:
                _set(servers.setdefault(key[1], {}), key[2:], value)
        elif table is not None:
            _set(table, key, value)
    return servers


def claude_servers(path=CLAUDE_JSON):
    """User-scope servers: the top-level "mcpServers" of ~/.claude.json."""
    try:
        with open(path) as fh:
            return json.load(fh).get("mcpServers", {}) or {}
    except (OSError, ValueError):
        return {}


def local_scope_servers(path=CLAUDE_JSON):
    """{name: [folders]} of local-scope servers (one laptop folder each)."""
    try:
        with open(path) as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    out = {}
    for folder, p in (data.get("projects") or {}).items():
        for name in (p.get("mcpServers") or {}):
            out.setdefault(name, []).append(folder)
    return out


# --- What each server becomes on the box ---

ARG_SECRET = "@@agentbox-secret:{}@@"   # replaced by `agentbox-mcp run` when the server starts


def _command(server, key, cfg, out, secrets):
    """A stdio server: the command by name (the box finds it), laptop paths moved to the
    box home, secret variables and arguments from Bitwarden through `agentbox-mcp run`."""
    command = cfg.get("command") or ""
    out["command"] = os.path.basename(command) if "/" in command else command
    args = [box_path(a) for a in cfg.get("args") or []]
    hidden_args = {}
    for i, name in secret_args(server, args).items():
        flag, eq, _ = str(args[i]).partition("=")
        args[i] = (flag + "=" if eq and flag.startswith("-") else "") + ARG_SECRET.format(name)
        hidden_args[name] = {"secret": name}
    env = dict(cfg.get("env") or {})
    hidden = {k: secret_name(server, k) for k, v in env.items() if looks_secret(k, v)}
    for k in hidden:
        env.pop(k)
    for k in cfg.get("env_vars") or []:   # Codex: inherited variable names
        name = k.get("name") if isinstance(k, dict) else k
        hidden[name] = secret_name(server, name)
    out.pop("env_vars", None)
    if env:
        out["env"] = {k: box_path(v) for k, v in env.items()}
    else:
        out.pop("env", None)
    out["args"] = args
    if hidden or hidden_args:
        refs = {"env": {k: {"secret": s} for k, s in hidden.items()}, "args": hidden_args}
        secrets[key] = {part: r for part, r in refs.items() if r}
        out["args"] = ["run", key, "--", out["command"], *args]
        out["command"] = "agentbox-mcp"
    if cfg.get("cwd"):
        out["cwd"] = box_path(cfg["cwd"])


def claude_def(name, cfg):
    """(definition for the box or None, {key: Bitwarden refs}, note): the note says why a
    server stays on the laptop, or what to know about one that goes."""
    key, secrets = f"claude:{name}", {}
    if name in RESERVED:
        return None, {}, BY_DESIGN[0]
    out = {k: v for k, v in cfg.items()}
    kind = cfg.get("type") or ("stdio" if cfg.get("command") else "http")
    if kind in ("http", "sse"):
        problem = url_problem(cfg.get("url"))
        if problem:
            return None, {}, problem
        if cfg.get("headersHelper"):
            return None, {}, "it gets its headers from a command on this laptop (headersHelper)"
        headers = dict(cfg.get("headers") or {})
        hidden = {h: secret_name(name, h) for h, v in headers.items() if looks_secret(h, v)}
        for h in hidden:
            headers.pop(h)
        if headers:
            out["headers"] = headers
        else:
            out.pop("headers", None)
        if hidden:
            secrets[key] = {"headers": {h: {"secret": s} for h, s in hidden.items()}}
            out["headersHelper"] = f"agentbox-mcp headers {key}"
        oauth = cfg.get("oauth") or {}
        if any(looks_secret(k, v) for k, v in oauth.items() if k not in ("clientId", "callbackPort")):
            return None, {}, "its OAuth settings hold a client secret"
        if oauth.get("callbackPort"):   # a fixed port of its own wins over the box's forwarded one
            return out, secrets, (f"its sign-in calls back on its own port {oauth['callbackPort']}, which tabs don't "
                                  f"forward: sign in from a box session with that port forwarded "
                                  f"(ssh -L {oauth['callbackPort']}:localhost:{oauth['callbackPort']} BOX)")
    elif kind == "stdio":
        _command(name, key, cfg, out, secrets)
    else:
        return None, {}, f"transport {kind!r} isn't supported on the box"
    return out, secrets, None


def codex_def(name, cfg):
    """The same for a Codex [mcp_servers.NAME] table (None when it couldn't be read)."""
    key, secrets = f"codex:{name}", {}
    if cfg is None:
        return None, {}, "agentbox couldn't read its table in ~/.codex/config.toml"
    if name in RESERVED:
        return None, {}, BY_DESIGN[0]
    out = {k: v for k, v in cfg.items()}
    if cfg.get("url"):
        problem = url_problem(cfg["url"])
        if problem:
            return None, {}, problem
        if cfg.get("http_headers_helper"):
            return None, {}, "it gets its headers from a command on this laptop (http_headers_helper)"
        headers = dict(cfg.get("http_headers") or {})
        refs = {h: {"secret": secret_name(name, h)} for h, v in headers.items() if looks_secret(h, v)}
        for h in refs:
            headers.pop(h)
        # Values Codex reads from variables on the laptop come from Bitwarden on the box.
        for h, var in (cfg.get("env_http_headers") or {}).items():
            refs[h] = {"secret": secret_name(name, var)}
        if cfg.get("bearer_token_env_var"):
            refs["Authorization"] = {"secret": secret_name(name, cfg["bearer_token_env_var"]), "prefix": "Bearer "}
        for k in ("env_http_headers", "bearer_token_env_var"):
            out.pop(k, None)
        if headers:
            out["http_headers"] = headers
        else:
            out.pop("http_headers", None)
        if refs:
            secrets[key] = {"headers": refs}
            out["http_headers_helper"] = f"agentbox-mcp headers {key}"
    elif cfg.get("command"):
        _command(name, key, cfg, out, secrets)
    else:
        return None, {}, "it has neither a url nor a command"
    return out, secrets, None


def plan(agents):
    """{"claude": {name: def}, "codex": {...}, "secrets": {...}, "skipped": [(agent, name, why)],
    "notes": [(agent, name, note)]}."""
    result = {"claude": {}, "codex": {}, "secrets": {}, "skipped": [], "notes": []}
    sources = [("claude", claude_servers(), claude_def)]
    if "codex" in agents.split():
        sources.append(("codex", codex_servers(), codex_def))
    for agent, servers, convert in sources:
        for name, cfg in sorted(servers.items()):
            d, secrets, why = convert(name, cfg)
            if d is None:
                result["skipped"].append((agent, name, why))
            else:
                result[agent][name] = d
                result["secrets"].update(secrets)
                if why:
                    result["notes"].append((agent, name, why))
    return result


def sync(agents="claude", quiet=False):
    p = plan(agents)
    payload = {"claude": p["claude"], "codex": p["codex"] if "codex" in agents.split() else None,
               "secrets": p["secrets"]}
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", BOX, "agentbox-mcp apply"],
                       input=json.dumps(payload), capture_output=True, text=True)
    if r.returncode == 127:   # a box set up before agentbox brought MCP servers
        if not quiet:
            print(f"mcp servers: {BOX} gets your MCP servers after one `up.sh` (or the next box open)")
        return
    if r.returncode:
        print(f"mcp servers: couldn't update {BOX}: {(r.stderr or r.stdout).strip()[-300:]}")
        return
    for line in r.stdout.splitlines():
        note = line.startswith("note: ")
        if line.strip() and not (note and quiet):   # attach/new: only what changed
            print(f"mcp servers on {BOX}: {line[6:] if note else line}")
    if not quiet:   # attach/new stay quiet about these; open, up.sh and box sync say it
        for agent, name, why in p["skipped"]:
            print(f"mcp servers: {agent} {name} stays on the laptop: {why}")
        for agent, name, note in p["notes"]:
            print(f"mcp servers: {agent} {name}: {note.replace('BOX', BOX)}")


def status(agents="claude"):
    p = plan(agents)
    for agent in ("claude", "codex"):
        for name, d in p[agent].items():
            refs = p["secrets"].get(f"{agent}:{name}")
            needs = sorted({r["secret"] for part in (refs or {}).values() for r in part.values()})
            print(f"{agent} {name}: goes to the box" + (f" (from Bitwarden: {', '.join('CLI__' + n for n in needs)})" if needs else ""))
    for agent, name, why in p["skipped"]:
        print(f"{agent} {name}: stays on the laptop: {why}")
    user = set(claude_servers())
    for name, folders in sorted(local_scope_servers().items()):
        if name in user:
            continue
        print(f"claude {name}: local scope ({len(folders)} folder(s)) stays on the laptop; "
              f"`claude mcp add --scope user` makes it travel")


def main(argv):
    if not argv or argv[0] not in ("sync", "status"):
        print(__doc__.strip())
        sys.exit(0 if argv[:1] in (["-h"], ["--help"]) else 1)
    agents = argv[argv.index("--agents") + 1] if "--agents" in argv else "claude"
    if argv[0] == "sync":
        sync(agents, quiet="--quiet" in argv)
    else:
        status(agents)


if __name__ == "__main__":
    main(sys.argv[1:])
