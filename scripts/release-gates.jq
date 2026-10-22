# Whether a release's gates.json qualifies the tagged commit ($commit): release ready,
# produced by a clean checker for that commit, every gate passed. Schema 1 (0.1.0,
# 0.2.0) has release-level gates only; schema 2 adds every release target's gates,
# with exactly one tier-1 target, and all of them must have passed.
# Used by attest-release.yml (through check-release-assets.sh) and verify-release-assets.sh.
.release_ready == true and .candidate_commit == $commit and .checker_tree_dirty == false
and (.gates | type == "object" and length > 0)
and ([.gates[].status] | all(. == "passed"))
and (if .schema_version == 1 then (has("targets") | not)
     elif .schema_version == 2 then
       (.targets | type == "object" and length > 0)
       and ([.targets[] | select(.tier == 1)] | length == 1)
       and all(.targets[]; (.tier == 1 or .tier == 2) and (.gates | type == "object" and length > 0)
                           and ([.gates[].status] | all(. == "passed")))
     else false end)
