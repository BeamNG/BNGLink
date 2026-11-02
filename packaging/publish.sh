#!/usr/bin/env bash
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

VERSION=$(cat VERSION)
TAG="v$VERSION"

# 1. refuse if this version is already released
if [ -n "$(git ls-remote origin "refs/tags/$TAG")" ]; then
  echo "error: $TAG already exists - bump VERSION first" >&2
  exit 1
fi

# 2. snapshot = the content of the current commit
TREE=$(git rev-parse 'HEAD^{tree}')

# 3. previous release commit (the parent)
git fetch -q origin public || true
PARENT=$(git rev-parse -q --verify refs/heads/public \
      || git rev-parse -q --verify refs/remotes/origin/public \
      || true)

# 4. build the release commit
export GIT_AUTHOR_NAME="${GITLAB_USER_NAME:-$(git config user.name)}"
export GIT_AUTHOR_EMAIL="${GITLAB_USER_EMAIL:-$(git config user.email)}"
export GIT_COMMITTER_NAME="$GIT_AUTHOR_NAME"
export GIT_COMMITTER_EMAIL="$GIT_AUTHOR_EMAIL"
: "${GIT_AUTHOR_NAME:?no git identity available}"
if [ -n "$PARENT" ]; then
  COMMIT=$(git commit-tree "$TREE" -p "$PARENT" -m "BNGLink $TAG")
else
  COMMIT=$(git commit-tree "$TREE" -m "BNGLink $TAG")
fi

# 5. move 'public' onto it, and tag the commit
git branch -f public "$COMMIT"
git tag -f "$TAG" "$COMMIT"   # -f: stale tags in the runner's cached clone are not real

# 6. publish branch + tag to the internal repo
git push origin public "refs/tags/$TAG"
