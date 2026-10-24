# Whether a release's gates.json qualifies the tagged commit ($commit) under that commit's own
# release record ($record: release/compatibility.json) and policy ($policy: release/qualification.json):
# release ready, produced by a clean checker for that commit, every gate passed, and exactly the
# gates the record requires.
#   - Schema 1 (0.1.0, 0.2.0 only) has release-level gates and no targets.
#   - Schema 2 has the record's required_release_gates (the policy's qualification_gates) and
#     every release target of the record with its tier and exactly its required_gates (tier 1:
#     the release gates; tier 2: every target gate of the policy), all passed, and names the CI
#     candidate run of $commit (ci_run) that every tier-2 target's evidence came from.
# A gates.json cannot choose its own gate set: a gate it leaves out, renames or adds fails here.
# Used by attest-release.yml (through check-release-assets.sh) and verify-release-assets.sh.
def same($a; $b): ($a | sort) == ($b | sort);
def passed: type == "object" and length > 0 and ([.[].status] | all(. == "passed"));
.release_ready == true and .candidate_commit == $commit and .checker_tree_dirty == false
and (.gates | passed)
and (if .schema_version == 1 then
       (has("targets") | not) and ($record.package_version == "0.1.0" or $record.package_version == "0.2.0")
     elif .schema_version == 2 then
       $record.schema_version == 2
       and same($record.required_release_gates; $policy.qualification_gates | keys)
       and same(.gates | keys; $record.required_release_gates)
       and (.targets | type == "object")
       and same(.targets | keys; [$record.targets[].platform])
       and ([$record.targets[] | select(.tier == 1)] | length == 1)
       and (.ci_run | type == "object" and (.run_id | type == "number") and .head_sha == $commit)
       and (.ci_run.run_id as $run | . as $gates
            | all($record.targets[];
                .platform as $platform | .tier as $tier | .required_gates as $required
                | $gates.targets[$platform] as $target
                | $target.tier == $tier
                  and ($target.gates | passed)
                  and same($target.gates | keys; $required)
                  and (if $tier == 1 then same($required; $record.required_release_gates)
                       elif $tier == 2 then same($required; $policy.target_gates | keys) and $target.ci_run.run_id == $run
                       else false end)))
     else false end)
