#!/usr/bin/env bash
# Downloads the named assets of a published GitHub release into OUTPUT_DIR and
# checks, before anything is uploaded to a registry, that each one is the file
# that was qualified and attested:
#   - its SHA-256 is listed in the release's SHA256SUMS;
#   - the tag (resolved through its tag ref, scripts/tag-commit.sh) names the
#     package_version of the tagged commit's release/compatibility.json;
#   - the release's gates.json is release_ready, produced by a clean checker for
#     the tagged commit, and has exactly the gates (and, from schema 2, the release
#     targets and their gates) that the tagged commit's release record and policy
#     require, all passed (scripts/release-gates.jq, shared with attest-release),
#     and it is the gates.json listed in SHA256SUMS;
#   - the file has an attest-release attestation for this release, tag commit and
#     gates.json, signed by exactly the attest-release.yml workflow as run from main
#     (--cert-identity is the certificate's full SubjectAlternativeName, workflow path
#     and ref; --signer-workflow would be a prefix match without the ref) and whose
#     certificate names this repository, the main ref and a workflow_dispatch run on a
#     GitHub-hosted runner.
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
signer=https://github.com/$repo/.github/workflows/attest-release.yml@refs/heads/main

meta=$(mktemp -d)
trap 'rm -rf "$meta"' EXIT
mkdir -p "$out"
gh release download "$tag" -R "$repo" -D "$meta" -p SHA256SUMS -p gates.json
commit=$("$(dirname "$0")/tag-commit.sh" "$repo" "$tag")
# The tagged commit's own release record and policy say which gates and targets gates.json must have.
for name in compatibility.json qualification.json; do
  gh api -H 'Accept: application/vnd.github.raw+json' "repos/$repo/contents/release/$name?ref=$commit" > "$meta/$name"
done
jq -e --arg tag "$tag" '"v" + .package_version == $tag' "$meta/compatibility.json" > /dev/null ||
  { echo "$tag is not the package version of $commit's release record" >&2; exit 1; }

(cd "$meta" && grep '  gates.json$' SHA256SUMS | sha256sum -c --quiet -)
jq -e --arg commit "$commit" --argjson record "$(< "$meta/compatibility.json")" --argjson policy "$(< "$meta/qualification.json")" \
  -f "$(dirname "$0")/release-gates.jq" "$meta/gates.json" > /dev/null ||
  { echo "gates.json does not qualify $tag ($commit)" >&2; exit 1; }
gates=$(sha256sum "$meta/gates.json" | cut -d' ' -f1)

for asset in "$@"; do
  [[ $asset != */* ]] || { echo "Asset names have no directory: $asset" >&2; exit 2; }
  gh release download "$tag" -R "$repo" -D "$out" -p "$asset" --clobber
  # Exactly one SHA256SUMS line for this name, and the bytes match it.
  [ "$(awk -v name="$asset" '$2 == name' "$meta/SHA256SUMS" | wc -l)" = 1 ] ||
    { echo "$asset is not listed once in SHA256SUMS" >&2; exit 1; }
  (cd "$out" && awk -v name="$asset" '$2 == name' "$meta/SHA256SUMS" | sha256sum -c --quiet -)
  gh attestation verify "$out/$asset" -R "$repo" --predicate-type "$predicate_type" --cert-identity "$signer" --source-ref refs/heads/main --format json |
    jq -e --arg tag "$tag" --arg repo "$repo" --arg commit "$commit" --arg gates "$gates" --arg signer "$signer" \
      'any(.[]; (.verificationResult.signature.certificate
          | .subjectAlternativeName == $signer and .buildSignerURI == $signer and .buildConfigURI == $signer
            and .sourceRepositoryURI == "https://github.com/\($repo)" and .githubWorkflowRepository == $repo
            and .sourceRepositoryRef == "refs/heads/main" and .githubWorkflowRef == "refs/heads/main"
            and .githubWorkflowTrigger == "workflow_dispatch" and .runnerEnvironment == "github-hosted")
        and (.verificationResult.statement.predicate | .release == $tag and .repository == $repo
          and .source_commit == $commit and .gates_sha256 == $gates and .release_ready == true))' > /dev/null ||
    { echo "$asset has no attestation for $tag, $commit and this gates.json" >&2; exit 1; }
  echo "$asset: listed in SHA256SUMS, qualified and attested for $tag"
done
