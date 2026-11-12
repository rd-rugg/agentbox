"""server_types.py prices each type per location: Hetzner charges some types differently in
different locations, and a box is created in one of them."""
import json
import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def stype(name, ram, disk, prices, stock):
    return {"name": name, "cores": 2, "memory": ram, "disk": disk, "cpu_type": "shared", "architecture": "x86",
            "deprecation": None,
            "prices": [{"location": loc, "price_hourly": {"gross": str(h)}, "price_monthly": {"gross": str(h * 624)}}
                       for loc, h in prices.items()],
            "locations": [{"name": loc, "available": True} for loc in stock]}


TYPES = [
    stype("cx23", 4, 40, {"fsn1": 0.0095, "nbg1": 0.0095, "hel1": 0.0095}, []),
    stype("cpx22", 4, 80, {"fsn1": 0.0337, "nbg1": 0.0337, "hel1": 0.0285}, ["fsn1", "nbg1", "hel1"]),
    stype("cpx32", 8, 160, {"fsn1": 0.0615, "nbg1": 0.0615, "hel1": 0.0519}, ["fsn1", "hel1"]),
]


def run(*args, budget="cpx32"):
    env = {**os.environ, "BOX_LOCATIONS": "fsn1 nbg1 hel1", "BOX_MIN_RAM": "4", "BOX_MAX_TYPE": budget, "BOX_TYPE": ""}
    return subprocess.run([sys.executable, os.path.join(ROOT, "server_types.py"), *args], input=json.dumps(TYPES),
                          env=env, capture_output=True, text=True)


class PerLocation(unittest.TestCase):
    def test_candidates_carry_their_own_locations_price_cheapest_first(self):
        lines = run("candidates").stdout.splitlines()
        self.assertEqual([" ".join(line.split()[:2]) for line in lines],
                         ["cpx22 hel1", "cpx22 fsn1", "cpx22 nbg1", "cpx32 hel1", "cpx32 fsn1"])
        self.assertIn("€0.0285/h", lines[0])
        self.assertIn("€0.0337/h", lines[1])

    def test_the_budget_type_is_allowed_everywhere_at_its_own_price(self):
        self.assertIn("cpx32 fsn1", run("candidates").stdout)
        self.assertEqual(run("candidates", budget="cpx22").stdout.split()[::5], ["cpx22", "cpx22", "cpx22"])

    def test_check_quotes_the_locations_price(self):
        r = run("check", "cpx22", "fsn1")
        self.assertEqual(r.stdout.strip(), "4 €0.0337/h (€21.03/month)", r.stderr)
        self.assertIn("above your budget", run("check", "cpx32", "hel1", budget="cpx22").stderr)

    def test_the_table_shows_the_price_where_it_is_in_stock(self):
        rows = {line.split()[0]: line for line in run("table").stdout.splitlines()[1:]}
        self.assertIn("€0.0285", rows["cpx22"])
        self.assertTrue(rows["cx23"].rstrip().endswith("-"))


if __name__ == "__main__":
    unittest.main()
