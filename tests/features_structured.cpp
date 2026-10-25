// Structured decisions (schema requests and their projections) and the structured state that the
// questions of one request share.
#include "features_support.hpp"
#include <cstdlib>
#include <iostream>

namespace features {
void structured_decisions(context &c) {
    auto &engine = c.engine;
    const auto &requests = c.requests;
    const auto &singles = c.singles;
    auto schema = json::parse(R"({"state":"Please refund me.","schema":{"type":"object","properties":{
      "department":{"type":"string","enum":["billing","technical","sales"],"description":"Which department should handle this?"},
      "refund":{"type":"boolean","description":"Does the customer request a refund?"},
      "urgency":{"type":"integer","minimum":-2,"maximum":0,"description":"How urgent is this?"}},
      "required":["department","refund"],"additionalProperties":false}})");
    auto structured = json::parse(engine.execute(schema.dump()));
    expect(structured["values"]["refund"] == (structured["answers"]["refund"]["noul"].get<double>() >= .5), "boolean projection");
    expect(structured["values"]["department"] == structured["answers"]["department"]["choice"], "enum projection");
    double best = -1; int level = -2, chosen = 0;
    for (auto &p : structured["answers"]["urgency"]["probabilities"]) { if (p.get<double>() > best) { best = p; chosen = level; } ++level; }
    expect(structured["values"]["urgency"] == chosen, "ordinal argmax and negative offset");
    expect(structured["values"].size() == 3, "optional properties are also populated");
    expect(json::parse(engine.execute(json::array({requests[0], schema}).dump())) == json::array({singles[0], structured}), "mixed batch");
    auto boolean = schema; boolean["schema"]["properties"] = {{"refund",schema["schema"]["properties"]["refund"]}};
    boolean["schema"].erase("required"); boolean["boolean_threshold"] = 0;
    expect(json::parse(engine.execute(boolean.dump()))["values"]["refund"] == true, "explicit threshold");
    boolean["boolean_threshold"] = 2; invalid([&] { engine.execute(boolean.dump()); });
    for (auto bad : {json{{"type","string"}}, json{{"type","object"}}, json{{"type","array"}},
                    json{{"type","boolean"},{"$ref","x"}}, json{{"type","integer"},{"minimum",0},{"maximum",10}},
                    json{{"type","string"},{"enum",{"a","a"}}}, json{{"type","integer"},{"minimum",0.5},{"maximum",2}}}) {
        auto b = schema; b["schema"]["properties"]["refund"] = bad;
        invalid([&] { engine.execute(b.dump()); });
    }
    auto bad = schema; bad["schema"]["required"] = {"missing"}; invalid([&] { engine.execute(bad.dump()); });
    bad = schema; bad["schema"]["oneOf"] = json::array(); invalid([&] { engine.execute(bad.dump()); });
    bad = schema; bad["questions"] = requests[0]["questions"]; invalid([&] { engine.execute(bad.dump()); });
    c.checkpoint("structured decisions");
}

void structured_state_shared(context &c) {
    auto &engine = c.engine;
    {
        // A structured state is serialized once per request and model and shared by its
        // questions (request_state.hpp; decision-rules checks that it is prepared once): each
        // question of a multi-question request answers exactly as it does alone. Windows of 64
        // tokens keep the passes short; what is compared does not depend on the window size.
        json readings = json::array();
        for (int i = 0; i < 400; ++i) readings.push_back({{"sensor", "s" + std::to_string(i % 7)}, {"value", i * 0.125 + 1.0 / (i + 3)}, {"ok", i % 5 != 0}});
        const json state = {{"ticket", {{"subject", "Refund for duplicate charge"}, {"priority", 2}, {"amount", 1e15}}}, {"readings", readings}};
        const json all = {{"state", state}, {"overflow", "truncate_end"}, {"max_tokens", 64}, {"questions", {
            {"refund", {{"type", "noul"}, {"instructions", "Does the customer request a refund?"}}},
            {"urgency", {{"type", "score"}, {"instructions", "How urgent is this?"}, {"criteria", {"low", "moderate", "high"}}}},
            {"team", {{"type", "choice"}, {"instructions", "Which team handles it?"}, {"criteria", {{"billing", "Payments"}, {"technical", "Bugs"}}}}},
            {"faulty", {{"type", "noul"}, {"instructions", "Do the readings show a faulty sensor?"}}}}}};
        const auto together = json::parse(engine.execute(all.dump()));
        for (const auto &[id, question] : all["questions"].items()) {
            json alone = all;
            alone["questions"] = {{id, question}};
            expect(json::parse(engine.execute(alone.dump()))["answers"][id] == together["answers"][id],
                   "a question answers the same alone and among others sharing a structured state");
        }
#ifdef DTL_COUNTS_ALLOCATIONS
        // Prepared once, measured: the allocations three more questions add with a structured
        // state, less those they add with the same state passed as text (which is never
        // serialized), must stay far below one serialization of that state (estimated as a
        // one-question request with the structured state less one with the text). A backend
        // that serialized per question would add three serializations. Every request has a
        // new state of equal length, so each is tokenized once; windows of 64 tokens keep the
        // passes short.
        const auto allocations_of = [&](json state, bool four) {
            json request = all;
            request["state"] = std::move(state);
            request["max_tokens"] = 64;
            if (!four) request["questions"] = {{"refund", all["questions"]["refund"]}};
            const auto body = request.dump();
            const long before = allocation_count();
            engine.execute(body);
            return allocation_count() - before;
        };
        const auto variant = [&](const char *nonce) { json copy = state; copy["ticket"]["subject"] = std::string("Refund ") + nonce; return copy; };
        const long one = allocations_of(variant("A"), false), four = allocations_of(variant("B"), true);
        const long one_text = allocations_of(variant("C").dump(), false), four_text = allocations_of(variant("D").dump(), true);
        const long serialization = one - one_text, repeated_work = (four - one) - (four_text - one_text);
        std::cerr << "allocations: one serialization about " << serialization << ", added by three more questions " << repeated_work << "\n";
        expect(serialization > 1000 && std::labs(repeated_work) * 4 < serialization, "a structured state is prepared once per request");
#elif defined(DTL_SANITIZED)
        std::cout << "SKIPPED: allocation count of the shared state (sanitizer runtimes own operator new)\n";
#else
        std::cout << "NOT APPLICABLE: allocation count of the shared state (counts operator new through ELF interposition; Linux only)\n";
#endif
    }
    c.checkpoint("structured state shared by questions");
}
} // namespace features
