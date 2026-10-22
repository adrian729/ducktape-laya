#!/usr/bin/env bash
# Checks a directory holding all of a release's assets, offline, before they are
# attested (attest-release.yml runs this on the published assets):
#   - SHA256SUMS lists every file in the directory once and the bytes match;
#   - gates.json qualifies COMMIT (scripts/release-gates.jq);
#   - native-inventory.json and npm-browser-inventory.json are the inventories
#     gates.json qualified, were packaged from COMMIT, and every artifact they list
#     is present with the listed hash (the SBOMs under their prefixed names);
#   - for gates.json schema 2, the native inventory is a multi-platform assembly
#     whose platforms are exactly the qualified targets, every fragment packaged
#     from COMMIT on a clean tree;
#   - nothing else is in SHA256SUMS.
# Accepts the 0.1.0 and 0.2.0 (schema 1, single-platform) releases unchanged.
#
# Usage: scripts/check-release-assets.sh DIR COMMIT
# Needs jq and sha256sum.
set -euo pipefail

[ $# = 2 ] || { echo "Usage: $0 DIR COMMIT" >&2; exit 2; }
dir=$1 commit=$2
filter=$(cd "$(dirname "$0")" && pwd)/release-gates.jq
fail() { echo "$*" >&2; exit 1; }
cd "$dir"

[ -f SHA256SUMS ] && [ -f gates.json ] || fail "SHA256SUMS or gates.json missing"
sha256sum -c --quiet SHA256SUMS
listed=$(awk '{print $2}' SHA256SUMS)
[ -z "$(sort <<< "$listed" | uniq -d)" ] || fail "SHA256SUMS lists a name twice"
for name in *; do
  [ "$name" = SHA256SUMS ] || grep -qxF -- "$name" <<< "$listed" || fail "Not listed in SHA256SUMS: $name"
done
jq -e --arg commit "$commit" -f "$filter" gates.json > /dev/null || fail "gates.json does not qualify $commit"

allowed="gates.json"
for pair in native:native-inventory.json:native-sbom.cdx.json browser:npm-browser-inventory.json:npm-browser-sbom.cdx.json; do
  label=${pair%%:*} rest=${pair#*:}
  inventory=${rest%%:*} sbom=${rest#*:}
  [ -f "$inventory" ] || fail "$inventory missing"
  jq -e --arg label "$label" --arg sum "$(sha256sum "$inventory" | cut -d' ' -f1)" '.inventories_sha256[$label] == $sum' gates.json > /dev/null ||
    fail "$inventory is not the inventory gates.json qualified"
  jq -e --arg commit "$commit" '.packaging_source_commit == $commit' "$inventory" > /dev/null || fail "$inventory was not packaged from $commit"
  allowed="$allowed $inventory"
  while read -r sum name; do
    [ "$name" = sbom.cdx.json ] && name=$sbom
    [[ $name != */* ]] || fail "Artifact names have no directory: $name"
    echo "$sum  $name" | sha256sum -c --quiet - || fail "$name differs from $inventory"
    allowed="$allowed $name"
  done < <(jq -r '.artifacts | to_entries[] | "\(if (.value | type) == "object" then .value.sha256 else .value end) \(.key)"' "$inventory")
done

if [ "$(jq .schema_version gates.json)" = 2 ]; then
  jq -e --slurpfile gates gates.json --arg commit "$commit" '.kind == "multi-platform"
    and .platforms == ($gates[0].targets | keys | sort)
    and (.fragments | keys) == .platforms
    and ([.fragments[] | .packaging_source_commit == $commit and .packaging_source_tracked_dirty == false] | all)' \
    native-inventory.json > /dev/null || fail "native-inventory.json platforms are not the qualified targets, or a fragment is not from $commit"
fi

# Nothing outside the qualified inventories.
while read -r name; do
  case " $allowed " in *" $name "*) ;; *) fail "Unqualified asset: $name" ;; esac
done <<< "$listed"
echo "Release assets in $dir qualified for $commit: $(wc -l < SHA256SUMS) files"
