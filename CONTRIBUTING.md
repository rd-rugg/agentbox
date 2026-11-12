# Contributing

Thanks for helping. `AGENTS.md` describes the layout, the rules (money, secrets,
destructive commands) and the checks; it is written for people and coding agents alike.

- **Set up:** clone the repository and run `./install-local.sh`, which puts `box` on your
  PATH from this checkout. You need bash 4+ (on macOS, Homebrew's), python3, and `uvx`
  and node for the checks.
- **Test:** `python3 -m unittest discover -s tests` runs the offline tests. Before a pull
  request, run the whole block under "Checks before committing" in `AGENTS.md`; pull
  requests run a subset of it on GitHub.
- **Test on a real box** for anything that touches the box: a throwaway one works
  (`BOX_NAME=mytest ./setup.sh`, then `BOX_NAME=mytest box close`, and delete its
  snapshot and its firewall, `hcloud firewall delete mytest`); nothing here mocks Hetzner.
- **Behaviour changes** update `README.md`, `box help` and the skills under `skills/`
  when an agent should act differently, in the same pull request.
- **Never** commit secrets, real hostnames, IPs or account details, also not in
  examples or tests.

This repository receives releases from a private development repository. Pull requests
are welcome here: accepted ones are merged back into development and come out in the
next release.

Bug reports: include your OS, `box doctor --json` (it holds no secrets) and the
command's output.
