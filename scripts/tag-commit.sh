#!/usr/bin/env bash
# Prints the commit a release tag points to, through the tag ref itself
# (repos/REPO/git/ref/tags/TAG; an annotated tag is dereferenced through its tag
# object). Unlike commits/TAG, a branch or other ref of the same name cannot
# answer instead, and a lightweight and an annotated tag resolve alike.
#
# Usage: scripts/tag-commit.sh REPO TAG
# Needs gh (authenticated).
set -euo pipefail

[ $# = 2 ] || { echo "Usage: $0 REPO TAG" >&2; exit 2; }
repo=$1 tag=$2
[[ $tag =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "Not a release tag: $tag" >&2; exit 2; }
read -r type sha < <(gh api "repos/$repo/git/ref/tags/$tag" --jq '"\(.ref) \(.object.type) \(.object.sha)"' |
  awk -v ref="refs/tags/$tag" '$1 == ref {print $2, $3}') || true
[ -n "${sha:-}" ] || { echo "No tag $tag in $repo" >&2; exit 1; }
# A tag object can point to another tag object; follow the chain to the commit.
for _ in 1 2 3 4 5; do
  [ "$type" = tag ] || break
  read -r type sha < <(gh api "repos/$repo/git/tags/$sha" --jq '"\(.object.type) \(.object.sha)"') || true
done
[ "$type" = commit ] && [[ $sha =~ ^[0-9a-f]{40}$ ]] || { echo "$tag does not point to a commit ($type $sha)" >&2; exit 1; }
echo "$sha"
