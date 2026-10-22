#!/usr/bin/env bash
# Downloads the named assets of a published GitHub release into OUTPUT_DIR and
# checks, before anything is uploaded to a registry, that each one is the file
# that was qualified and attested:
#   - its SHA-256 is listed in the release's SHA256SUMS;
#   - the release's gates.json is release_ready, every gate (and, from schema 2,
#     every release target's gate) passed, it was produced by a clean checker for
#     the tagged commit (scripts/release-gates.jq, shared with attest-release),
#     and it is the gates.json listed in SHA256SUMS;
#   - the file has an attest-release attestation for this release, tag commit and
#     gates.json.
# Only the named assets are written to OUTPUT_DIR (SHA256SUMS and gates.json are
# kept elsewhere), so the directory can be handed to an upload tool as it is.
#
# Usage: scripts/verify-release-assets.sh TAG OUTPUT_DIR ASSET...
# Needs gh (authenticated), jq and sha256sum.
set -euo pipefail

[ $# -ge 3 ] || { echo "Usage: $0 TAG OUTPUT_DIR ASSET..." >&2; exit 2; }
tag=$1 out=$2
shift 2
[[ $tag =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "Not a release tag: $tag" >&2; exit 2; }
repo=${GITHUB_REPOSITORY:-adrian729/ducktape-laya}
predicate_type=https://github.com/$repo/release-qualification/v1
signer=$repo/.github/workflows/attest-release.yml

meta=$(mktemp -d)
trap 'rm -rf "$meta"' EXIT
mkdir -p "$out"
gh release download "$tag" -R "$repo" -D "$meta" -p SHA256SUMS -p gates.json
commit=$(gh api "repos/$repo/commits/$tag" --jq .sha)

(cd "$meta" && grep '  gates.json$' SHA256SUMS | sha256sum -c --quiet -)
jq -e --arg commit "$commit" -f "$(dirname "$0")/release-gates.jq" "$meta/gates.json" > /dev/null ||
  { echo "gates.json does not qualify $tag ($commit)" >&2; exit 1; }
gates=$(sha256sum "$meta/gates.json" | cut -d' ' -f1)

for asset in "$@"; do
  [[ $asset != */* ]] || { echo "Asset names have no directory: $asset" >&2; exit 2; }
  gh release download "$tag" -R "$repo" -D "$out" -p "$asset" --clobber
  # Exactly one SHA256SUMS line for this name, and the bytes match it.
  [ "$(awk -v name="$asset" '$2 == name' "$meta/SHA256SUMS" | wc -l)" = 1 ] ||
    { echo "$asset is not listed once in SHA256SUMS" >&2; exit 1; }
  (cd "$out" && awk -v name="$asset" '$2 == name' "$meta/SHA256SUMS" | sha256sum -c --quiet -)
  gh attestation verify "$out/$asset" -R "$repo" --predicate-type "$predicate_type" --signer-workflow "$signer" --format json |
    jq -e --arg tag "$tag" --arg repo "$repo" --arg commit "$commit" --arg gates "$gates" \
      'any(.[]; .verificationResult.statement.predicate | .release == $tag and .repository == $repo
        and .source_commit == $commit and .gates_sha256 == $gates and .release_ready == true)' > /dev/null ||
    { echo "$asset has no attestation for $tag, $commit and this gates.json" >&2; exit 1; }
  echo "$asset: listed in SHA256SUMS, qualified and attested for $tag"
done
