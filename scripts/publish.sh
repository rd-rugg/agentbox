#!/usr/bin/env bash
# Publish the development repository's HEAD to the public repository as one commit.
#
# Day-to-day work happens in a private repository (with history, plans and notes that stay
# private). A release is the files of HEAD less the paths in .publishignore, committed once on
# top of the public branch under the public identity, after these gates:
#   - deny list: nothing in the release (file names, contents, the commit message) matches the
#     private deny list (PUBLISH_DENYLIST, default ~/.config/agentbox/publish-denylist; one
#     case-insensitive string per line, or re:REGEX). Findings show the file and the entry's
#     number, never the text.
#   - secrets: gitleaks over the release tree, with its .gitleaks.toml.
#   - identity: the commit's author and committer are this repository's user.name/user.email,
#     a GitHub noreply address that doesn't match the deny list.
#   - left out: no path .publishignore lists is in the release.
#   - checks: the release tree passes PUBLISH_CHECKS, or the bash block under "Checks before
#     committing" in its AGENTS.md, run in a temporary copy.
#   - public branch: it holds nothing that isn't merged back here yet (else: --pull).
#
# The first publish to an empty public repository starts its branch with one commit and no
# history: the development history (and anything in it) stays private.
#
#   scripts/publish.sh [--dry-run]            show what would be published, the gates and the message
#   scripts/publish.sh --push -m SUMMARY [--yes]   publish as "<version>: SUMMARY" (asks y/N unless --yes)
#   scripts/publish.sh --pull                 merge new public commits (contributions) into this repository
#   scripts/publish.sh --install-hooks        install the pre-push guard: every push to the public
#                                             repository passes the deny list, secrets and identity gates
#
# Settings: PUBLISH_REMOTE (default "public"), PUBLISH_BRANCH ("main"), PUBLISH_DENYLIST,
# PUBLISH_CHECKS, PUBLISH_GITLEAKS (a gitleaks binary; else gitleaks on PATH, else a pinned
# release is downloaded and checked on Linux x86_64).
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"
SCRIPT="$ROOT/scripts/publish.sh"
GATES=(python3 "$ROOT/scripts/publish_gates.py")
REMOTE="${PUBLISH_REMOTE:-public}"
BRANCH="${PUBLISH_BRANCH:-main}"
GITLEAKS_VERSION=8.30.1
GITLEAKS_SHA256=551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb
ZERO=0000000000000000000000000000000000000000

die() { printf 'publish: %s\n' "$*" >&2; exit 1; }

repo_of() { sed -E 's#^(git@[^:]+:|ssh://git@[^/]+/|https?://[^/]+/)##; s#\.git$##; s#/$##' <<< "$1"; }

public_url() {
  git config --get "remote.$REMOTE.url" \
    || die "no remote '$REMOTE': add the public repository with: git remote add $REMOTE <URL>"
}

gitleaks_bin() {
  if [ -n "${PUBLISH_GITLEAKS:-}" ]; then echo "$PUBLISH_GITLEAKS"; return; fi
  if command -v gitleaks >/dev/null 2>&1; then command -v gitleaks; return; fi
  local dir="${XDG_CACHE_HOME:-$HOME/.cache}/agentbox/gitleaks-$GITLEAKS_VERSION"
  if [ ! -x "$dir/gitleaks" ]; then
    [ "$(uname -s)-$(uname -m)" = Linux-x86_64 ] \
      || die "gitleaks isn't installed: install it (https://github.com/gitleaks/gitleaks) or set PUBLISH_GITLEAKS"
    mkdir -p "$dir"
    curl -fsSL -o "$dir/gitleaks.tar.gz" \
      "https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}/gitleaks_${GITLEAKS_VERSION}_linux_x64.tar.gz" \
      || die "couldn't download gitleaks $GITLEAKS_VERSION"
    echo "$GITLEAKS_SHA256  $dir/gitleaks.tar.gz" | sha256sum -c --quiet - >/dev/null 2>&1 \
      || { rm -f "$dir/gitleaks.tar.gz"; die "the gitleaks download doesn't match its checksum"; }
    tar -xzf "$dir/gitleaks.tar.gz" -C "$dir" gitleaks
    rm -f "$dir/gitleaks.tar.gz"
  fi
  echo "$dir/gitleaks"
}

# Unpack TREE into a new temporary folder and print its path.
unpack() {
  local dir
  dir="$(mktemp -d)"
  git archive "$1" | tar -x -C "$dir"
  echo "$dir"
}

# The gates every push to the public repository passes: deny list, secrets, left-out paths.
# Prints one line per gate; returns 1 if any failed.
content_gates() {  # TREE MESSAGE_FILE
  local tree="$1" msg="$2" out dir gl failed=0
  if out="$("${GATES[@]}" deny "$tree" ${msg:+--message "$msg"} 2>&1)"; then
    echo "  ✔ deny list: no matches"
  else
    echo "  ✘ deny list matches (remove them, or the entry if it's wrong):"; echo "$out"; failed=1
  fi
  if out="$("${GATES[@]}" ignored "$tree" 2>&1)"; then
    echo "  ✔ left out: no path from .publishignore"
  else
    echo "  ✘ paths .publishignore leaves out are in the release:"; echo "$out"; failed=1
  fi
  dir="$(unpack "$tree")"
  gl="$(gitleaks_bin)"
  if out="$(cd "$dir" && "$gl" dir . --no-banner --redact --exit-code 1 2>&1)"; then
    echo "  ✔ secrets: gitleaks found none"
  else
    echo "  ✘ secrets: gitleaks reports (values redacted):"; grep -E 'Finding|RuleID|File|Line' <<< "$out" | sed 's/^/    /'; failed=1
  fi
  rm -rf "$dir"
  return "$failed"
}

identity_gate() {  # NAME EMAIL
  local out
  if out="$("${GATES[@]}" identity "$1" "$2" 2>&1)"; then
    echo "  ✔ identity: $1, a noreply address"
  else
    echo "  ✘ identity:"; echo "$out"; return 1
  fi
}

checks_gate() {  # TREE
  local dir cmd rc=0 log
  dir="$(unpack "$1")"
  log="$(mktemp)"
  if [ -n "${PUBLISH_CHECKS:-}" ]; then
    cmd="$PUBLISH_CHECKS"
  elif [ -f "$dir/AGENTS.md" ]; then
    cmd="$(sed -n '/## Checks before committing/,/^```$/p' "$dir/AGENTS.md" | sed -n '/^```bash/,/^```/p' | sed '1d;$d')"
  fi
  if [ -z "${cmd:-}" ]; then
    echo "  ✘ checks: none defined (set PUBLISH_CHECKS or add a 'Checks before committing' block to AGENTS.md)"
    rm -rf "$dir" "$log"; return 1
  fi
  (cd "$dir" && bash -e -c "$cmd") >"$log" 2>&1 || rc=$?
  if [ "$rc" -eq 0 ]; then
    echo "  ✔ checks: pass on the release tree"
  else
    echo "  ✘ checks failed on the release tree (exit $rc); the end of their output:"; tail -15 "$log" | sed 's/^/    /'
  fi
  rm -rf "$dir" "$log"
  return "$rc"
}

# Every publish is recorded here as a merge of the public commit (keeping this repository's
# files), so the public branch is an ancestor of HEAD until someone else commits there.
moved_gate() {  # PUBLIC_COMMIT (none: the first publish)
  if [ -z "$1" ]; then
    echo "  ✔ public branch: none yet; this publish starts it with one commit"
  elif git merge-base --is-ancestor "$1" HEAD; then
    echo "  ✔ public branch: nothing new there to merge back"
  else
    echo "  ✘ public branch: $REMOTE/$BRANCH has commits not merged here yet: run scripts/publish.sh --pull"
    return 1
  fi
}

version_of() {  # TREE
  git show "$1:.claude-plugin/plugin.json" 2>/dev/null \
    | python3 -c 'import json, sys; print(json.load(sys.stdin)["version"])' 2>/dev/null || true
}

# The public branch's commit, or nothing when the public repository doesn't have it yet (the
# first publish). --prune drops a stale tracking branch, e.g. of a repository made again.
public_commit() {
  public_url >/dev/null
  local _ heads
  for _ in 1 2 3; do
    if heads="$(git ls-remote --heads "$REMOTE" "$BRANCH" 2>/dev/null)"; then
      [ -n "$heads" ] || { git fetch -q --prune "$REMOTE" 2>/dev/null || true; return 0; }
      git fetch -q --prune "$REMOTE" "$BRANCH" 2>/dev/null && { git rev-parse "$REMOTE/$BRANCH"; return 0; }
    fi
    sleep 3
  done
  die "couldn't fetch $REMOTE/$BRANCH"
}
EMPTY_TREE="$(git hash-object -t tree /dev/null)"

cmd_publish() {  # dry|push SUMMARY YES
  local mode="$1" summary="$2" yes="$3" pub tree version name email msg failed=0 commit last_tag since
  local -a parent=()
  pub="$(public_commit)"
  [ -z "$pub" ] || parent=(-p "$pub")
  [ -z "$(git status --porcelain --untracked-files=no)" ] \
    || echo "Note: uncommitted changes aren't published; only the committed HEAD is."
  tree="$("${GATES[@]}" release-tree HEAD)"
  version="${PUBLISH_VERSION:-$(version_of "$tree")}"
  [ -n "$version" ] || die "no version: set PUBLISH_VERSION or a version in .claude-plugin/plugin.json"
  name="$(git config user.name || true)"; email="$(git config user.email || true)"
  echo "Release $version: $(repo_of "$(git config --get remote.origin.url || echo .)") HEAD $(git rev-parse --short HEAD) -> $(repo_of "$(public_url)") $BRANCH (${pub:+$(git rev-parse --short "$pub")}${pub:-none yet: the first publish starts it, without history})"
  if [ -n "$pub" ] && [ "$(git rev-parse "$pub^{tree}")" = "$tree" ]; then
    echo "The release tree is the same as $REMOTE/$BRANCH: nothing to publish."
    return 0
  fi
  echo "Changes against ${pub:+$REMOTE/$BRANCH}${pub:-an empty repository}:"
  git diff --stat=100 "${pub:-$EMPTY_TREE}" "$tree" | sed 's/^/  /'
  if git rev-parse -q --verify "refs/tags/published/$version" >/dev/null; then
    echo "  ✘ version: $version was published already; bump it first"; failed=1
  fi
  msg="$(mktemp)"
  if [ -n "$summary" ]; then
    printf '%s: %s\n' "$version" "$summary" > "$msg"
  else
    last_tag="$(git tag -l 'published/*' --sort=-creatordate | head -1)"
    printf '%s: <one-line summary, given with -m>\n' "$version" > "$msg"
    echo "Commits since ${last_tag:-the public branch} (for writing the summary; they stay private):"
    since="${last_tag:-$pub}"
    git log --no-merges --format='  - %s' "${since:+$since..}HEAD" 2>/dev/null | cut -c1-110 | head -30 || true
  fi
  echo "Gates:"
  content_gates "$tree" "$msg" || failed=1
  identity_gate "$name" "$email" || failed=1
  moved_gate "$pub" || failed=1
  checks_gate "$tree" || failed=1
  echo "Message: $(head -1 "$msg")"
  if [ "$mode" = dry ]; then
    rm -f "$msg"
    if [ "$failed" -ne 0 ]; then echo "Dry run: a gate failed (above); nothing was changed."; return 1; fi
    echo "Dry run: nothing was changed. Publish with: scripts/publish.sh --push -m \"SUMMARY\""
    return 0
  fi
  [ "$failed" -eq 0 ] || { rm -f "$msg"; die "a gate failed (above); nothing was published"; }
  [ -n "$summary" ] || { rm -f "$msg"; die "give the one-line summary with -m"; }
  [ -z "$(git status --porcelain --untracked-files=no)" ] \
    || { rm -f "$msg"; die "commit or stash the changes first: the publish is recorded here as a merge"; }
  if [ "$yes" != 1 ]; then
    local answer
    printf 'Publish %s to %s %s? [y/N] ' "$version" "$(repo_of "$(public_url)")" "$BRANCH" >/dev/tty
    read -r answer </dev/tty
    [[ "$answer" == [yY]* ]] || { rm -f "$msg"; echo "Not published."; return 1; }
  fi
  commit="$(GIT_AUTHOR_NAME="$name" GIT_AUTHOR_EMAIL="$email" GIT_COMMITTER_NAME="$name" GIT_COMMITTER_EMAIL="$email" \
    git commit-tree "$tree" ${parent[@]+"${parent[@]}"} -F "$msg")"
  rm -f "$msg"
  git push "$REMOTE" "$commit:refs/heads/$BRANCH"
  git tag -a "published/$version" -m "Published to $(repo_of "$(public_url)") as $commit" HEAD
  git merge -q -s ours --allow-unrelated-histories --no-edit -m "Published $version to $(repo_of "$(public_url)") as $(git rev-parse --short "$commit")" "$commit"
  git push -q origin HEAD "refs/tags/published/$version" 2>/dev/null \
    || echo "Note: couldn't push the publish record to origin; push this branch and published/$version later."
  git fetch -q "$REMOTE" "$BRANCH" || true
  echo "Published $version as $(git rev-parse --short "$commit")."
}

cmd_pull() {
  local pub
  pub="$(public_commit)"
  [ -n "$pub" ] || { echo "Nothing on $REMOTE yet."; return 0; }
  if git merge-base --is-ancestor "$pub" HEAD; then
    echo "Nothing new on $REMOTE/$BRANCH."; return 0
  fi
  echo "New public commits:"
  git log --format='  %h %an: %s' "HEAD..$pub" 2>/dev/null | head -30 || true
  if git merge --no-ff -m "Merge public contributions from $(repo_of "$(public_url)") $BRANCH" "$pub"; then
    echo "Merged. Review, run the checks, then push to origin."
  else
    echo "The merge has conflicts (above): resolve them, then git commit, or git merge --abort." >&2
    return 1
  fi
}

# The pre-push guard. Git passes the remote and its URL, and one line per ref on stdin.
cmd_pre_push() {  # REMOTE_NAME URL
  local url="$2" lsha failed=0 c tree msg
  [ "$(repo_of "$url")" = "$(repo_of "$(public_url)")" ] || return 0
  echo "publish guard: checking the push to $(repo_of "$url")"
  while read -r _ lsha _ _; do
    [ -n "$lsha" ] && [ "$lsha" != "$ZERO" ] || continue
    for c in $(git rev-list "$lsha" --not --remotes="$1"); do
      identity_gate "$(git log -1 --format=%an "$c")" "$(git log -1 --format=%ae "$c")" >/dev/null 2>&1 \
        && identity_gate "$(git log -1 --format=%cn "$c")" "$(git log -1 --format=%ce "$c")" >/dev/null 2>&1 \
        || { echo "  ✘ identity: commit $(git rev-parse --short "$c") isn't authored and committed with the public noreply identity"; failed=1; }
    done
    tree="$(git rev-parse "$lsha^{tree}")"
    msg="$(mktemp)"
    git log --format=%B "$lsha" --not --remotes="$1" > "$msg"
    content_gates "$tree" "$msg" || failed=1
    rm -f "$msg"
  done
  [ "$failed" -eq 0 ] || { echo "publish guard: push refused (above). Publish with scripts/publish.sh."; return 1; }
}

cmd_install_hooks() {
  local hooks hook
  hooks="$(git rev-parse --git-common-dir)/hooks"
  hook="$hooks/pre-push"
  mkdir -p "$hooks"
  if [ -f "$hook" ] && ! grep -q 'agentbox publish guard' "$hook"; then
    mv "$hook" "$hooks/pre-push.local"
    echo "Kept the existing pre-push hook as pre-push.local; the guard runs it first."
  fi
  cat > "$hook" <<EOF
#!/usr/bin/env bash
# agentbox publish guard, installed by scripts/publish.sh --install-hooks: a push to the public
# repository passes the deny list, secrets and identity gates. Other remotes are left alone.
set -euo pipefail
input="\$(cat)"
hooks="\$(cd "\$(dirname "\$0")" && pwd)"
if [ -x "\$hooks/pre-push.local" ]; then printf '%s\n' "\$input" | "\$hooks/pre-push.local" "\$@"; fi
script="$SCRIPT"
pub="\$(git config --get remote.$REMOTE.url || true)"
norm() { sed -E 's#^(git@[^:]+:|ssh://git@[^/]+/|https?://[^/]+/)##; s#\.git\$##; s#/\$##' <<< "\$1"; }
[ -n "\$pub" ] && [ "\$(norm "\$2")" = "\$(norm "\$pub")" ] || exit 0
[ -x "\$script" ] || { echo "publish guard: \$script is missing, so this push to the public repository is refused." >&2; exit 1; }
printf '%s\n' "\$input" | PUBLISH_REMOTE=$REMOTE "\$script" --pre-push "\$@"
EOF
  chmod +x "$hook"
  if [ -n "$(git config --get core.hooksPath || true)" ]; then
    echo "Note: core.hooksPath is set; git runs the hooks there, which must hand over to $hooks (agentbox's passthrough does)."
  fi
  echo "Installed the publish guard: $hook"
}

# Everything these commands print passes the deny list's mask, so no file name, path or commit
# subject shows what the list guards (in a terminal, a log or an agent's transcript).
redacted() { "$@" 2>&1 | "${GATES[@]}" redact; }

mode=dry summary="" yes=0
case "${1:-}" in
  --pull) redacted cmd_pull; exit ;;
  --install-hooks) cmd_install_hooks; exit ;;
  --pre-push) shift; redacted cmd_pre_push "$@"; exit ;;
  -h|--help) sed -n '2,/^set -euo/p' "$0" | sed '$d; s/^# \{0,1\}//'; exit 0 ;;
esac
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) mode=dry ;;
    --push) mode=push ;;
    -m) summary="${2:?-m needs a summary}"; shift ;;
    --yes) yes=1 ;;
    *) die "unknown option $1 (see --help)" ;;
  esac
  shift
done
redacted cmd_publish "$mode" "$summary" "$yes"
