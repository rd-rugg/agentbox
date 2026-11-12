#!/usr/bin/env python3
"""The pool of boxes (agentbox, agentbox-2, ...): where projects live and where new ones go.

State is kept as labels on the Hetzner resources, so any laptop (or a future
controller) sees the same thing and nothing has to run permanently:
  server / snapshot  app=<box>  p.<project>=1  sessions=<n> (snapshots)
  firewall <pool>    lock=<host>.<expiry> while a laptop creates or moves boxes
Pool membership follows from the name: <pool> and <pool>-<n>.

  pool.py boxes                  box<TAB>state<TAB>type<TAB>eur_per_hour<TAB>projects
                                 (state: up, down, off, starting, stopping, closing; box_state)
  pool.py state BOX              that box's state (none if it has no server or snapshot)
  pool.py where PROJECT          the box a project is placed on (its label; empty if none)
  pool.py place PROJECT BOX      record the placement (label on the server, or its snapshot)
  pool.py plan PROJECT [--box B] where it goes: action=use|open|create|full|error, box=, reason= lines
  pool.py move-plan PROJECT TO FILES  a move: action=copy|place|error, open=, create=, reason=
                                 (FILES: yes|no|unreachable|unknown, the project's folder on its box)
  pool.py migrate                label projects from before pools onto the first box, once
  pool.py min-ram BOX            GB of RAM BOX's server type needs (BOX_MIN_RAM, or its projects' ram_gb/size)
  pool.py next                   the first unused box name within BOX_MAX_BOXES (empty if full)
  pool.py registered             project<TAB>box<TAB>up|down for every registered project
  pool.py reopen                 boxes `box open` should bring up (had sessions when closed)
  pool.py idle                   box<TAB>idle hours, for every running box
  pool.py lock | unlock          lease lock for creating/moving boxes (10 minutes)
Settings come from the environment (lib.sh exports them from box.conf).
Standard-library Python 3.8+.

A project is on box X only if X (its server, or its snapshot when closed) carries the
label p.<project>; a registered project without one isn't placed. plan_place and
plan_move decide, without side effects; `box` asks before anything that bills and then
carries the plan out.
"""
import json
import os
import re
import shlex
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import projects  # noqa: E402

POOL = os.environ.get("BOX_POOL", "agentbox")
MAX_BOXES = int(os.environ.get("BOX_MAX_BOXES", "1") or 1)
POLICY = os.environ.get("BOX_POLICY", "pack")
PROJECT_GB = float(os.environ.get("BOX_PROJECT_RAM", "1") or 1)
SESSION_GB = float(os.environ.get("BOX_SESSION_RAM", "0.5") or 0.5)
SIZE_GB = {"small": 1, "medium": 2, "large": 4}
RESERVE_MB = 512   # the system, tmux, Docker and the agent CLIs' own daemons
T3_MB = 512        # T3 Code's server (BOX_T3=1): about 470 MB resident while idle

MEMBER = re.compile(rf"^{re.escape(POOL)}(-\d+)?$")
_PLACED = None   # p.<project> labels on any server or snapshot, in every pool (members() fills it)
LOCK_SECONDS = 1800  # long enough for creating a box or moving a project


def hcloud(*args):
    out = subprocess.run(["hcloud", *args], capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip() or f"hcloud {' '.join(args)} failed")
    return out.stdout


def hjson(*args):
    return json.loads(hcloud(*args, "-o", "json") or "[]")


def key(project):
    """Label key for a project: p.<name>, within Hetzner's label rules."""
    k = re.sub(r"[^a-z0-9_.-]", "-", project.split("@")[0].split("/")[0].lower()).strip("-_.")
    return "p." + k[:61]


def box_names():
    return [POOL] + [f"{POOL}-{i}" for i in range(2, MAX_BOXES + 1)]


def box_state(status, closing=False):
    """A box's state from its server's Hetzner status: "up" only while it runs. Every
    other existing server still bills: "closing" while box close works on it (down.sh labels
    the server closing=1 until it's deleted; a snapshot of it being made, or Hetzner
    deleting it, says the same), "off" when it was switched off without being closed,
    "starting"/"stopping" in between. "down" is a closed box: a snapshot, no server."""
    if closing or status == "deleting":
        return "closing"
    if status == "running":
        return "up"
    if status in ("initializing", "starting"):
        return "starting"
    return status or "unknown"


def members():
    """Every box of the pool: existing servers (state from their status, box_state) and
    closed ones (their newest snapshot)."""
    with ThreadPoolExecutor(max_workers=2) as ex:
        servers = ex.submit(hjson, "server", "list")
        images = ex.submit(hjson, "image", "list", "--type", "snapshot")
        servers, images = servers.result(), images.result()
    global _PLACED
    _PLACED = {k for x in servers + images for k in (x.get("labels") or {}) if k.startswith("p.")}
    boxes = {}
    # Servers a snapshot is being made of right now: box close in progress.
    saving = {(i.get("created_from") or {}).get("id") for i in images if i.get("status") == "creating"}
    for s in servers:
        if not MEMBER.match(s["name"]):
            continue
        t = s["server_type"]
        loc = s.get("location", {}).get("name")
        price = next((float(p["price_hourly"]["gross"]) for p in t["prices"] if p["location"] == loc), 0.0)
        closing = s["id"] in saving or (s.get("labels") or {}).get("closing") == "1"
        boxes[s["name"]] = {"box": s["name"], "state": box_state(s.get("status"), closing), "server": True,
                            "type": t["name"], "eur_h": price,
                            "labels": s.get("labels", {}), "id": s["id"]}
    for img in sorted(images, key=lambda i: i["id"]):
        name = img.get("labels", {}).get("app", "")
        if MEMBER.match(name) and name not in boxes and img.get("status") != "creating":
            boxes[name] = {"box": name, "state": "down", "server": False, "type": "-", "eur_h": 0.0,
                           "labels": img["labels"], "id": img["id"]}
    return boxes


def configured_pool():
    """The pool box.conf sets up (BOX_POOL or BOX_NAME there, else agentbox). Projects
    registered before pools belong to it; a one-off BOX_NAME=other in the environment
    (another pool) doesn't get them."""
    path = os.environ.get("BOX_CONF") or os.path.join(
        os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "agentbox", "box.conf")
    conf = {}
    try:
        for line in open(path):
            m = re.match(r"\s*(?:export\s+)?(BOX_NAME|BOX_POOL)=(.*)", line)
            if m:
                conf[m.group(1)] = m.group(2).strip().strip("'\"")
    except OSError:
        pass
    return conf.get("BOX_POOL") or re.sub(r"-\d+$", "", conf.get("BOX_NAME") or "agentbox")


def projects_on(box):
    return sorted(k[2:] for k in box["labels"] if k.startswith("p."))


def where(project, boxes=None):
    """The box a project lives on: the one carrying its placement label, or None."""
    boxes = boxes or members()
    k = key(project)
    return next((b for b in boxes.values() if k in b["labels"]), None)


# --- projects from before pools ------------------------------------------------------
# Before pools a project lived on the one box, with no label: registered on the laptop, or
# only a folder in ~/work there. The first runs of this version label them onto the
# configured pool's first box, in two steps recorded in a marker so each runs once:
#   registry  the laptop's registered projects (also onto a closed box's snapshot)
#   folders   the folders in ~/work on the first box (once it's up and answers)
# A project registered or cloned later always gets its label from placement, so neither
# step ever places a new project implicitly. With no first box, there is nothing to do.
def migrated_marker():
    return os.path.join(projects.CONF, f"placement-labels.{POOL}")


def first_box_folders(box_name):
    """Project folders in ~/work on a running box (not the renamed copies box move leaves),
    or None if it doesn't answer."""
    try:
        out = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", box_name,
                              "cd ~/work 2>/dev/null && for d in */; do echo \"${d%/}\"; done"],
                             capture_output=True, text=True, timeout=40)
    except subprocess.TimeoutExpired:
        return None
    if out.returncode != 0:
        return None
    return [d for d in out.stdout.split() if d != "*" and ".moved-to-" not in d]


def migrate(boxes=None):
    """Label the pre-pool projects onto the first box; returns their names."""
    marker = migrated_marker()
    try:
        with open(marker) as fh:
            done = {line.strip() for line in fh if line.strip()}
    except OSError:
        done = set()
    if POOL != configured_pool() or {"registry", "folders"} <= done:
        return []
    boxes = boxes if boxes is not None else members()
    if _PLACED is None:
        members()
    first, moved = boxes.get(POOL), []

    def label(names):
        for name in names:
            k = key(name)
            if k not in _PLACED and k not in first["labels"]:
                set_label(first, k, "1")
                first["labels"][k] = "1"
                moved.append(name)
    if not first:
        done |= {"registry", "folders"}
    else:
        if "registry" not in done:
            label(sorted(projects.load_registry()))
            done.add("registry")
        if "folders" not in done and first["state"] == "up":
            folders = first_box_folders(POOL)
            if folders is not None:
                label(sorted(folders))
                done.add("folders")
    os.makedirs(os.path.dirname(marker), exist_ok=True)
    with open(marker, "w") as fh:
        fh.write("".join(f"{s}\n" for s in sorted(done)))
    return moved


def projects_on(box):
    return sorted(k[2:] for k in box["labels"] if k.startswith("p."))


def where(project, boxes=None):
    """The box a project lives on: the one carrying its placement label, or None."""
    boxes = boxes or members()
    k = key(project)
    return next((b for b in boxes.values() if k in b["labels"]), None)


def set_label(box, label, value=None):
    kind = "server" if box.get("server") else "image"   # a server in any state carries the labels
    if value is None:
        hcloud(kind, "remove-label", str(box["id"]), label)
    else:
        hcloud(kind, "add-label", "--overwrite", str(box["id"]), f"{label}={value}")


def load(box_name):
    try:
        out = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", box_name, "box --load"],
                             capture_output=True, text=True, timeout=40)
        return json.loads(out.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError, subprocess.TimeoutExpired):
        return None


def loads(names):
    with ThreadPoolExecutor(max_workers=8) as pool:
        return dict(zip(names, pool.map(load, names)))


def declared_gb(project):
    """The project's agentbox.json "ram_gb" or "size" in GB, or None."""
    path = projects.load_registry().get(project)
    m = projects.manifest(path) if path else {}
    gb = m.get("ram_gb") or SIZE_GB.get(m.get("size", ""))
    return float(gb) if gb else None


def need_mb(project):
    """Memory a project needs: agentbox.json "ram_gb" or "size", else BOX_PROJECT_RAM; plus one session."""
    return ((declared_gb(project) or PROJECT_GB) + SESSION_GB) * 1024


def reserve_mb():
    """Memory a box keeps for itself before any project: more where T3 Code's server runs."""
    return RESERVE_MB + (T3_MB if os.environ.get("BOX_T3") == "1" else 0)


def room_mb(load, placed_mb):
    """Memory a running box can still promise to a new project: its RAM minus the
    system's share and what the projects already on it need (placed_mb, one entry each,
    one session included), minus extra sessions beyond one per project; never more than
    what is free right now. Free memory alone isn't enough: a box whose agents are
    idle looks empty although its projects will need their memory again."""
    sessions = int(load.get("sessions") or 0)
    extra = max(0, sessions - len(placed_mb)) * SESSION_GB * 1024
    promised = load["mem_total_mb"] - reserve_mb() - sum(placed_mb) - extra
    return min(promised, load["mem_avail_mb"])


def min_ram_gb(box_name):
    """RAM the box's server type needs: BOX_MIN_RAM, or more when the projects placed on it
    declare "ram_gb" or "size" in agentbox.json (added up), so a reopen never lands on a
    smaller type than they need."""
    base = float(os.environ.get("BOX_MIN_RAM", "4") or 4)
    box = members().get(box_name)
    return max(base, sum(declared_gb(p) or 0 for p in (projects_on(box) if box else [])))


def next_box(boxes):
    return next((n for n in box_names() if n not in boxes), "")


def gb(mb):
    return f"{mb / 1024:.1f}"


def plan_place(project, boxes, loads, needs, need, max_boxes=MAX_BOXES, next_name="", policy=POLICY,
               on="", first=POOL):
    """Where PROJECT goes, decided without side effects.
      boxes: {box: {"state": "up"|"down"|"off"|..., "projects": [names]}} (box_state)
      loads: {box: `box --load` dict, or None if it couldn't be read} for running boxes
      needs: {project: MB} for every project placed on a box (its estimate, one session included)
      need:  MB this project needs; next_name: the box that may be created ("" at the limit)
      on:    --box BOX, the user's own choice (no room check)
    Returns {"action": use|open|create|full|error, "box": name, "reasons": [lines]}.
    A running box takes a new project only if the estimates of everything on it, plus this
    one, fit (free memory now isn't capacity: idle projects need theirs again)."""
    def out(action, box="", *reasons):
        return {"action": action, "box": box, "reasons": list(reasons)}
    placed = next((n for n, b in boxes.items() if project in b["projects"]), "")
    if on:
        if on not in boxes and on != next_name:
            return out("error", on, f"{on} isn't a box of the pool (box ls lists them).")
        if placed and placed != on:
            return out("error", placed, f"{project} is on {placed}; move it with: box move {project} --to {on}")
        if on not in boxes:
            return out("create", on, f"{project} goes on {on} (--box), which doesn't exist yet.")
        state = boxes[on]["state"]
        return out("use" if state == "up" else "open", on, f"{project} goes on {on} (--box).")
    if placed:
        if boxes[placed]["state"] == "up":
            return out("use", placed)
        return out("open", placed, f"{project} is on {placed}, which is closed "
                   f"(box move {project} --to BOX puts it on a running box instead).")
    if max_boxes <= 1:
        if first not in boxes:
            return out("create", first, f"{project} goes on {first}, which doesn't exist yet.")
        state = boxes[first]["state"]
        return out("use" if state == "up" else "open", first,
                   *([] if state == "up" else [f"{project} goes on {first}, which is closed."]))
    numbers, fits = [], []
    for n in sorted(b for b, x in boxes.items() if x["state"] == "up"):
        load = loads.get(n)
        if not load or "mem_total_mb" not in load:
            numbers.append(f"{n}: its load couldn't be read")
            continue
        placed_mb = [needs.get(p, 0) for p in boxes[n]["projects"]]
        capacity = load["mem_total_mb"] - reserve_mb()
        promised = capacity - room_mb(dict(load, mem_avail_mb=float("inf")), placed_mb)
        room = room_mb(load, placed_mb)
        numbers.append(f"{n}: {gb(promised)} GB promised of {gb(capacity)}, {gb(load['mem_avail_mb'])} GB free now")
        if room >= need:
            fits.append((room, n))
    if fits:
        fits.sort(reverse=(policy == "spread"))
        return out("use", fits[0][1], f"{project} ({gb(need)} GB) goes on {fits[0][1]}, which has room:", *numbers)
    why = [f"No running box has room for {project} ({gb(need)} GB):", *numbers,
           f"(--box BOX puts it on one of them anyway)"] if numbers else []
    # A box switched off is billed already: powering it on costs nothing more, so it comes
    # before a closed one. Boxes starting, stopping or closing aren't chosen.
    down = sorted((b["state"] != "off", len(b["projects"]), n) for n, b in boxes.items()
                  if b["state"] in ("off", "down"))
    if down:
        _, count, name = down[0]
        why_open = (f"{name} is switched off (billed already), with {count} project(s)." if boxes[name]["state"] == "off"
                    else f"{name} is closed, with {count} project(s).")
        return out("open", name, *why, why_open)
    if next_name:
        return out("create", next_name, *why, f"The pool may grow to {max_boxes} boxes: {next_name} would be new.")
    return out("full", "", *why, f"All {max_boxes} boxes are up and none has room; close or move a project, "
               f"raise BOX_MAX_BOXES, or choose one with --box BOX.")


def plan_move(project, source, target, files, registered, next_name="", to_new=False):
    """A move of PROJECT, decided without side effects.
      source: {"box", "state"} it's on, or None (not placed); target: {"box", "state"} or None
      (not a box of the pool; with next_name it's created); files: its folder on the source,
      yes|no|unreachable|unknown (unknown: the source is closed); registered: on this laptop;
      to_new: --to new (next_name is then the box to create, empty at the pool's limit).
    Returns {"action": copy|place|error, "open": [boxes], "create": box, "reasons": [lines]}:
    copy carries files, conversations and sessions; place labels it on the target and clones
    it there (nothing to carry). Only the boxes the plan needs are opened."""
    def out(action, open_=(), create="", *reasons):
        return {"action": action, "open": list(open_), "create": create, "reasons": list(reasons)}
    if target is None and not next_name:
        return out("error", (), "", f"The pool is at its limit of {MAX_BOXES} boxes (BOX_MAX_BOXES)." if to_new
                   else "That isn't a box of the pool (box ls lists them).")
    to = target["box"] if target else next_name
    create = "" if target else next_name
    to_open = [to] if target and target["state"] != "up" else []
    if source and source["box"] == to:
        return out("error", (), "", f"{project} is already on {to}.")
    nothing = f"{project} has no files on {source['box']} (never cloned there)" if source else f"{project} isn't on any box"
    if source is None or files == "no":
        if not registered:
            return out("error", (), "", f"{nothing} and isn't registered on this laptop, so there's nothing to copy "
                       f"or clone. Start it on {to} with: box new OWNER/REPO {project} --box {to}")
        return out("place", to_open, create, f"{nothing}: it's placed on {to} and cloned there.")
    if files == "unreachable":
        return out("error", (), "", f"Couldn't reach {source['box']} to check for {project}'s files; nothing was moved.")
    if source["state"] != "up":
        to_open = to_open + [source["box"]]
    return out("copy", to_open, create, f"{project}'s files, conversations and sessions go from {source['box']} to {to}.")


def gather(project, on=""):
    """The inputs plan_place needs, read from Hetzner and the running boxes (the loads and
    estimates only when they decide: not for a placed project, --box or a single box)."""
    boxes = members()
    view = {n: {"state": b["state"], "projects": projects_on(b)} for n, b in boxes.items()}
    placed = any(key(project)[2:] in v["projects"] for v in view.values())
    if placed or on or MAX_BOXES <= 1:
        return boxes, view, {}, {}
    up = [n for n, v in view.items() if v["state"] == "up"]
    # Labels hold sanitized names (p.myapp); the registry has the names as registered (MyApp).
    names = {key(n)[2:]: n for n in projects.load_registry()}
    needs = {p: need_mb(names.get(p, p)) for v in view.values() for p in v["projects"]}
    return boxes, view, loads(up), needs


def print_plan(plan):
    for k in ("action", "box", "create"):
        if k in plan:
            print(f"{k}={plan[k]}")
    if "open" in plan:
        print("open=" + " ".join(plan["open"]))
    for r in plan["reasons"]:
        print(f"reason={r}")


def firewall_labels():
    try:
        return hjson("firewall", "describe", POOL).get("labels", {})
    except (RuntimeError, AttributeError):
        return {}


def lock():
    try:
        hcloud("firewall", "describe", POOL)
    except RuntimeError:
        hcloud("firewall", "create", "--name", POOL, "--label", f"app={POOL}")
    now = int(time.time())
    current = firewall_labels().get("lock", "")
    m = re.match(r"(.+)\.(\d+)$", current)
    me = os.environ.get("AGENTBOX_LOCK_ID") or f"{socket.gethostname().lower()[:30]}-{os.getpid()}"
    me = re.sub(r"[^a-z0-9-]", "-", me.lower())[:50]
    if m and int(m.group(2)) > now and m.group(1) != me:
        sys.exit(f"The pool is locked by {m.group(1)} for {int(m.group(2)) - now}s more (creating or moving a box).")
    hcloud("firewall", "add-label", "--overwrite", POOL, f"lock={me}.{now + LOCK_SECONDS}")


def unlock():
    if "lock" in firewall_labels():
        hcloud("firewall", "remove-label", POOL, "lock")


def idle():
    for name, l in loads([b["box"] for b in members().values() if b["state"] == "up"]).items():
        if l and "idle_s" in l:   # a box whose box --load doesn't report it isn't counted as idle
            print(f"{name}\t{l['idle_s'] / 3600:.1f}")


def main(argv):
    cmd, args = (argv[0], argv[1:]) if argv else ("boxes", [])
    # Every placement read sees the pre-pool projects labelled (a no-op once it ran).
    if cmd in ("boxes", "where", "place", "plan", "move-plan", "registered", "reopen", "min-ram"):
        migrate()
    if cmd == "boxes":
        for b in sorted(members().values(), key=lambda b: b["box"]):
            print("\t".join([b["box"], b["state"], b["type"], f"{b['eur_h']:.4f}", " ".join(projects_on(b))]))
    elif cmd == "state":
        b = members().get(args[0])
        print(b["state"] if b else "none")
    elif cmd == "where":
        b = where(args[0])
        print(b["box"] if b else "")
    elif cmd == "place":
        boxes = members()
        if args[1] not in boxes:
            sys.exit(f"{args[1]} isn't in the pool")
        old = where(args[0], boxes)
        if old and old["box"] != args[1]:
            set_label(old, key(args[0]))
        set_label(boxes[args[1]], key(args[0]), "1")
    elif cmd == "plan":
        on = args[args.index("--box") + 1] if "--box" in args else ""
        boxes, view, loads_, needs = gather(args[0], on)
        project = key(args[0])[2:]
        print_plan(plan_place(project, view, loads_, needs, need_mb(args[0]), next_name=next_box(boxes), on=on))
    elif cmd == "move-plan":
        project, to, files = key(args[0])[2:], args[1], args[2]
        boxes = members()
        src = where(project, boxes)
        new = next_box(boxes) if to == "new" else ""
        print_plan(plan_move(project, src and {"box": src["box"], "state": src["state"]},
                             boxes.get(to) and {"box": to, "state": boxes[to]["state"]}, files,
                             args[0] in projects.load_registry(), new, to_new=(to == "new")))
    elif cmd == "migrate":
        for name in migrate():
            print(f"{name}: from before pools, now labelled onto {POOL}")
    elif cmd == "min-ram":
        print(f"{min_ram_gb(args[0]):g}")
    elif cmd == "next":
        print(next_box(members()))
    elif cmd == "registered":
        boxes = members()
        for p in projects.load_registry():
            b = where(p, boxes)
            print(f"{p}\t{b['box'] if b else ''}\t{b['state'] if b else ''}")
    elif cmd == "reopen":
        boxes = members()
        down = [b for b in boxes.values() if b["state"] == "down"]
        chosen = [b["box"] for b in down if int(b["labels"].get("sessions", "0") or 0) > 0]
        # A box switched off (billed, not running) comes back too.
        chosen += [b["box"] for b in boxes.values() if b["state"] == "off"]
        # Nothing to reopen and nothing running: `box open` means the first box.
        if not chosen and not any(b["state"] == "up" for b in boxes.values()) and POOL in boxes:
            chosen = [POOL]
        print("\n".join(sorted(chosen)))
    elif cmd == "lock":
        lock()
    elif cmd == "unlock":
        unlock()
    elif cmd == "idle":
        idle()
    else:
        print(__doc__.strip())


if __name__ == "__main__":
    main(sys.argv[1:])
