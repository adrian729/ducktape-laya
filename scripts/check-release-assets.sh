#!/usr/bin/env bash
# Checks a directory holding all of a release's assets, offline, before they are
# attested (attest-release.yml runs this on the published assets):
#   - TAG is vX.Y.Z and names the package_version of COMMIT's release record;
#   - SHA256SUMS lists every file in the directory (hidden ones included) once and
#     the bytes match;
#   - gates.json qualifies COMMIT under COMMIT's own release record and policy
#     (scripts/release-gates.jq: exactly the required gates and targets, all passed);
#   - native-inventory.json and npm-browser-inventory.json are the inventories
#     gates.json qualified, were packaged from COMMIT, and every artifact they list
#     is present with the listed hash (the SBOMs under their prefixed names);
#   - for gates.json schema 2, the native inventory is a multi-platform assembly
#     whose platforms are exactly the qualified targets, every fragment packaged
#     from COMMIT on a clean tree;
#   - a multi-platform native inventory goes with a schema 2 gates.json (schema is compared in jq,
#     like scripts/release-gates.jq, so 2 and 2.0 mean the same everywhere);
#   - nothing else is in SHA256SUMS.
# Accepts the schema 1, single-platform 0.2.0 release unchanged. 0.1.0 is rejected here (its gates.json
# has no `inventories_sha256`, so it does not bind the inventories); scripts/release-gates.jq itself still
# accepts both the 0.1.0 and 0.2.0 gates.json. Schema 1 is accepted for those two versions only.
# attest-release.yml also requires the native inventory to be the CI candidate run's
# (scripts/check-release-run.py); that needs the network, this does not.
#
# Usage: scripts/check-release-assets.sh DIR COMMIT TAG RELEASE_DIR
#   RELEASE_DIR holds COMMIT's release/compatibility.json and release/qualification.json
#   (for example: mkdir -p R && git archive COMMIT release | tar -x -C R && use R/release).
# Needs jq and sha256sum.
set -euo pipefail

[ $# = 4 ] || { echo "Usage: $0 DIR COMMIT TAG RELEASE_DIR" >&2; exit 2; }
dir=$1 commit=$2 tag=$3
release=$(cd "$4" && pwd)
filter=$(cd "$(dirname "$0")" && pwd)/release-gates.jq
fail() { echo "$*" >&2; exit 1; }
[[ $tag =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || fail "Not a release tag: $tag"
[[ $commit =~ ^[0-9a-f]{40}$ ]] || fail "Not a commit: $commit"
[ -f "$release/compatibility.json" ] && [ -f "$release/qualification.json" ] || fail "$release lacks compatibility.json or qualification.json"
jq -e --arg tag "$tag" '"v" + .package_version == $tag' "$release/compatibility.json" > /dev/null ||
  fail "$tag is not the package version of $commit's release record"
cd "$dir"

[ -f SHA256SUMS ] && [ -f gates.json ] || fail "SHA256SUMS or gates.json missing"
sha256sum -c --quiet SHA256SUMS
listed=$(awk '{print $2}' SHA256SUMS)
[ -z "$(sort <<< "$listed" | uniq -d)" ] || fail "SHA256SUMS lists a name twice"
# Every directory entry, hidden ones included.
while IFS= read -r -d '' name; do
  name=${name#./}
  [ "$name" = SHA256SUMS ] || grep -qxF -- "$name" <<< "$listed" || fail "Not listed in SHA256SUMS: $name"
done < <(find . -mindepth 1 -maxdepth 1 -print0)
jq -e --arg commit "$commit" --argjson record "$(< "$release/compatibility.json")" --argjson policy "$(< "$release/qualification.json")" \
  -f "$filter" gates.json > /dev/null || fail "gates.json does not qualify $commit"

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

if jq -e '.kind == "multi-platform"' native-inventory.json > /dev/null && ! jq -e '.schema_version == 2' gates.json > /dev/null; then
  fail "native-inventory.json is a multi-platform assembly, which needs a schema 2 gates.json"
fi
if jq -e '.schema_version == 2' gates.json > /dev/null; then
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
