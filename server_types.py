#!/usr/bin/env python3
"""Pick Hetzner server types for agentbox. Reads `hcloud server-type list -o json` on stdin.

  server_types.py table                 price table for setup.sh's budget prompt
  server_types.py candidates [MIN_DISK [MAX_DISK]]  "type location disk price" lines to try, cheapest first
                                        (each at its own location's price; ties keep BOX_LOCATIONS order)
  server_types.py price TYPE            "€0.0615/h (€38.36/month)" for one type
  server_types.py check TYPE LOCATION [DISK_GB]  "RAM_GB price" if TYPE is within budget, in stock
                                        there and (with DISK_GB) has at least that disk, else why not

Settings come from the environment: BOX_LOCATIONS, BOX_MIN_RAM, BOX_MAX_TYPE, BOX_TYPE.
"""
import json
import os
import sys

LOCATIONS = os.environ.get("BOX_LOCATIONS", "fsn1 nbg1 hel1").split()
MIN_RAM = float(os.environ.get("BOX_MIN_RAM", "4"))
MAX_TYPE = os.environ.get("BOX_MAX_TYPE", "")
FORCE_TYPE = os.environ.get("BOX_TYPE", "")


def location_name(entry):
    loc = entry.get("location")
    return loc["name"] if isinstance(loc, dict) else entry.get("name", "")


def load():
    """Usable x86 types (snapshots can't move to ARM), with prices in our locations."""
    types = []
    for t in json.load(sys.stdin):
        if t.get("deprecation") or t["architecture"] != "x86":
            continue
        # Hetzner prices some types differently per location (cpx22: hel1 cheaper than fsn1).
        prices = {p["location"]: (float(p["price_hourly"]["gross"]), float(p["price_monthly"]["gross"]))
                  for p in t["prices"] if p["location"] in LOCATIONS}
        if not prices:
            continue
        in_stock = [location_name(l) for l in t.get("locations", [])
                    if l.get("available") and location_name(l) in prices]
        # The type's price: where it can be had now, else anywhere (for the table and the budget).
        hourly, monthly = min(prices[l] for l in in_stock) if in_stock else min(prices.values())
        types.append({
            "name": t["name"],
            "cores": t["cores"],
            "memory": t["memory"],
            "disk": t["disk"],
            "cpu": t["cpu_type"],
            "hourly": hourly,
            "monthly": monthly,
            "prices": prices,
            "in_stock": in_stock,
        })
    return sorted(types, key=lambda t: (t["hourly"], t["name"]))


def budget_at(types, loc):
    """The most an hour may cost in loc: the budget type's own price there (None: no budget)."""
    top = next((t for t in types if t["name"] == MAX_TYPE), None)
    if top is None:
        return None
    return top["prices"][loc][0] if loc in top["prices"] else max(h for h, _ in top["prices"].values())


def at(t, loc):
    """The type as priced in one location."""
    hourly, monthly = t["prices"][loc]
    return {**t, "hourly": hourly, "monthly": monthly}


def fmt_price(t):
    return f"€{t['hourly']:.4f}/h (€{t['monthly']:.2f}/month)"


def main():
    mode = sys.argv[1]
    types = load()

    if mode == "table":
        print(f"  {'type':<7} {'vCPU':>4} {'RAM':>6} {'disk':>7}  {'per hour':>9} {'per month':>10}  in stock now")
        for t in types:
            if t["memory"] < MIN_RAM:
                continue
            stock = " ".join(t["in_stock"]) or "-"
            hourly, monthly = f"€{t['hourly']:.4f}", f"€{t['monthly']:.2f}"
            print(f"  {t['name']:<7} {t['cores']:>4} {t['memory']:>4g}GB {t['disk']:>5}GB"
                  f"  {hourly:>9} {monthly:>10}  {stock}")

    elif mode == "price":
        match = [t for t in types if t["name"] == sys.argv[2]]
        if not match:
            sys.exit(f"Unknown server type: {sys.argv[2]}")
        print(fmt_price(match[0]))

    elif mode == "check":
        name, loc = sys.argv[2], sys.argv[3]
        match = [t for t in types if t["name"] == name]
        if not match:
            sys.exit(f"Unknown server type: {name} (see box prices)")
        t = match[0]
        disk = int(sys.argv[4]) if len(sys.argv) > 4 else 0
        if t["disk"] < disk:
            sys.exit(f"{name} has a {t['disk']} GB disk, smaller than the box's {disk} GB (a disk can't shrink)")
        if loc not in t["in_stock"]:
            sys.exit(f"{name} isn't in stock in {loc} right now" + (f" (it is in {' '.join(t['in_stock'])})" if t["in_stock"] else ""))
        here = at(t, loc)
        if budget_at(types, loc) is not None and here["hourly"] > budget_at(types, loc):
            sys.exit(f"{name} costs {fmt_price(here)} in {loc}, above your budget ({MAX_TYPE}); "
                     "raise it with box budget TYPE first")
        print(f"{t['memory']:g} {fmt_price(here)}")

    elif mode == "candidates":
        min_disk = int(sys.argv[2]) if len(sys.argv) > 2 else 0
        max_disk = int(sys.argv[3]) if len(sys.argv) > 3 else 10**9
        if FORCE_TYPE:
            pool = [t for t in types if t["name"] == FORCE_TYPE]
        else:
            cap = next((t["hourly"] for t in types if t["name"] == MAX_TYPE), None)
            if MAX_TYPE and cap is None:
                sys.exit(f"BOX_MAX_TYPE={MAX_TYPE} is not a known x86 type in {' '.join(LOCATIONS)}")
            pool = [t for t in types if t["memory"] >= MIN_RAM]
        # Every (type, location) in stock at its own price, cheapest first; equal prices keep the
        # user's location order.
        offers = [at(t, loc) | {"loc": loc, "rank": LOCATIONS.index(loc)}
                  for t in pool if min_disk <= t["disk"] <= max_disk
                  for loc in t["in_stock"]]
        offers = [o for o in offers if FORCE_TYPE or budget_at(types, o["loc"]) is None
                  or o["hourly"] <= budget_at(types, o["loc"])]
        for o in sorted(offers, key=lambda o: (o["hourly"], o["rank"], o["name"])):
            print(o["name"], o["loc"], o["disk"], fmt_price(o))


if __name__ == "__main__":
    main()
