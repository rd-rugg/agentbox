"""Docs and skills name only credentials and settings agentbox itself provides.

A token or setting that the published markdown names but no code in the repository reads is
somebody's own convention. Such a name belongs in that person's own agent setup, for instance
the box-only overlay ~/.config/agentbox/box/, never in the release."""
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NAME = re.compile(r"\b(?:CLI__[A-Z0-9_]*|[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*_(?:TOKEN|KEY|SECRET|PASSWORD)(?:_[A-Z0-9]+)*)\b")
# Placeholders and names other tools define, shown as examples.
EXAMPLES = {"CLI__NAME", "CLI__MCP_", "CLI__", "CLI__CLAUDE_TOKEN_", "CLI__MCP_CONTEXT7_CONTEXT7_API_KEY",
            "CONTEXT7_API_KEY"}


def tracked():
    """The files a release holds (also when the tests run in a release tree, which isn't a repository)."""
    skip = {".git", "node_modules", "__pycache__", "plans", "video", "build"}
    out = []
    for root, dirs, names in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in skip]
        out += [os.path.relpath(os.path.join(root, n), ROOT) for n in names]
    return out


def read(f):
    try:
        with open(os.path.join(ROOT, f), encoding="utf-8") as fh:
            return fh.read()
    except UnicodeDecodeError:
        return ""


class DocNames(unittest.TestCase):
    def test_every_credential_name_in_the_docs_is_read_by_code(self):
        files = tracked()
        docs = [f for f in files if f.endswith(".md")]
        code = "\n".join(read(f) for f in files if not f.endswith(".md") and not f.startswith("tests/"))
        orphans = set()
        for f in docs:
            for name in NAME.findall(read(f)):
                if name not in EXAMPLES and name not in code:
                    orphans.add(f"{f}: {name}")
        self.assertEqual(sorted(orphans), [], "names no code reads: move them to your own setup, or to EXAMPLES if they are placeholders")


if __name__ == "__main__":
    unittest.main()
