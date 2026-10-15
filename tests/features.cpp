#include "ducktape_laya.hpp"
#include <nlohmann/json.hpp>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <vector>
#include <chrono>
using json = nlohmann::ordered_json;
static void expect(bool value, const char *message) { if (!value) throw std::runtime_error(message); }
template<class F> static void invalid(F fn, int status = DTL_INVALID_ARGUMENT) {
    try { fn(); } catch (const ducktape::error &e) { expect(e.status == status, e.what()); return; }
    throw std::runtime_error("Expected rejection");
}
static json primitive(const std::string &state) {
    return {{"state",state}, {"questions", {
        {"department", {{"type","choice"},{"instructions","Which department should handle this?"},
            {"criteria",{{"billing","Payments and refunds"},{"technical","Bugs and crashes"},{"sales","Buying products"}}}}},
        {"refund", {{"type","noul"},{"instructions","Does the customer request a refund?"}}},
        {"urgency", {{"type","score"},{"instructions","How urgent is this?"},{"criteria",{"low","moderate","high"}}}}
    }}};
}
int main(int argc, char **argv) {
    try {
        const auto started = std::chrono::steady_clock::now();
        auto checkpoint = [&](const char *phase) {
            std::cerr << phase << ": " << std::chrono::duration<double>(std::chrono::steady_clock::now() - started).count()
                      << " seconds\n";
        };
        char *result = reinterpret_cast<char *>(1), *error = nullptr;
        std::vector<json> events;
        dtl_observer observer{42, [](const char *event, void *user) {
            static_cast<std::vector<json> *>(user)->push_back(json::parse(event));
        }, &events};
        expect(dtl_execute(nullptr, "{}", &observer, &result, &error) == DTL_INVALID_ARGUMENT, "null execute");
        expect(!result && error && events.size() == 2 && events[0]["event"] == "start" &&
               events[1]["event"] == "error" && events[1]["request_id"] == 42, "failure observer/output contract");
        dtl_free(error);
        struct reset_check { char **result; char **error; bool reset = false; } reset{&result, &error};
        result = error = reinterpret_cast<char *>(1);
        dtl_observer reset_observer{0, [](const char *event, void *user) {
            auto &check = *static_cast<reset_check *>(user);
            if (json::parse(event)["event"] == "start") check.reset = !*check.result && !*check.error;
        }, &reset};
        expect(dtl_decide_observed(nullptr, "{}", &reset_observer, &result, &error) == DTL_INVALID_ARGUMENT,
               "observed legacy error");
        expect(reset.reset && !result && error, "outputs reset before start callback"); dtl_free(error);
        dtl_router *raw = reinterpret_cast<dtl_router *>(1);
        expect(dtl_router_open("{}", nullptr, &raw, &error) == DTL_INVALID_ARGUMENT && !raw, "invalid router");
        dtl_free(error); dtl_router_close(nullptr);
        invalid([] { ducktape::router r(R"({"models":{"x":"missing.gguf"}})"); }, DTL_MODEL_ERROR);
        {
            // Metadata validation occurs before lazy loading, so no model is needed.
            ducktape::router validator(json({{"models", {{"unused", std::filesystem::absolute(argv[0]).string()}}}}).dump());
            auto valid = json::parse(R"({"model":"unused","state":"x","schema":{"type":"object","properties":{"q":{"type":"boolean"}}}})");
            for (const char *key : {"title", "description"}) {
                auto bad = valid; bad["schema"][key] = 3;
                invalid([&] { validator.execute(bad.dump()); });
                bad = valid; bad["schema"]["properties"]["q"][key] = json::object();
                invalid([&] { validator.execute(bad.dump()); });
            }
        }
        if (argc < 2) return 0;
        auto options = dtl_default_options(); options.threads = 2;
        ducktape::laya engine(argv[1], options);
        json requests = json::array({primitive("Please refund the duplicate payment."),
            primitive("Settings crashes immediately! Fix this now."), primitive("What does the enterprise plan cost?")});
        // Exercise all primitive types across the batch. Routing and observer
        // assertions need one question per request, not three repeated forward
        // passes; multi-question primitives have separate conformance fixtures.
        const char *question_ids[] = {"refund", "department", "urgency"};
        for (size_t i = 0; i < requests.size(); ++i) {
            const auto question = requests[i]["questions"][question_ids[i]];
            requests[i]["questions"] = {{question_ids[i], question}};
        }
        json singles = json::array();
        for (auto &r : requests) singles.push_back(json::parse(engine.decide(r.dump())));
        checkpoint("single requests");
        events.clear();
        expect(json::parse(engine.execute(requests.dump(), &observer)) == singles, "exact batch parity");
        expect(events.size() == 2 && events[1]["event"] == "end" && events[1]["elapsed_ms"] >= 0, "success hooks");
        json reversed = json::array({requests[2], requests[0], requests[2]});
        expect(json::parse(engine.execute(reversed.dump())) == json::array({singles[2], singles[0], singles[2]}), "batch order/isolation");
        invalid([&] { engine.execute("[]"); });
        invalid([&] { engine.execute(json(std::vector<json>(33, requests[0])).dump()); });
        json huge = requests[0];
        for (int i = 0; i < 32; ++i) huge["questions"][std::to_string(i)] = requests[0]["questions"]["refund"];
        invalid([&] { engine.execute(huge.dump()); });
        huge["questions"] = json::object();
        for (int i = 0; i < 32; ++i) huge["questions"][std::to_string(i)] = requests[0]["questions"]["refund"];
        invalid([&] { engine.execute(json(std::vector<json>(5, huge)).dump()); });
        invalid([&] { engine.execute(json::array({requests[0], json::object()}).dump()); });
        invalid([&] { engine.execute(std::string(1048577, 'x')); });
        checkpoint("batches and bounds");
        // Overflow is rejected, never truncated. Each repeated word is one token, so the
        // boundary is exact; rejected prompts fail during planning, before any inference.
        auto repeated = [](const char *word, size_t count) { std::string s; while (count--) s += word; return s; };
        auto sized = [&](size_t words) {
            json r = {{"state", repeated("refund ", words)}, {"questions", {{"refund", requests[0]["questions"]["refund"]}}}};
            return r.dump();
        };
        auto rejected = [&](const std::string &request, const char *reason) {
            try { engine.execute(request); return false; }
            catch (const ducktape::error &e) {
                expect(e.status == DTL_INVALID_ARGUMENT && std::string(e.what()).find(reason) != std::string::npos, e.what());
                return true;
            }
        };
        const size_t base = json::parse(engine.execute(sized(1)))["usage"]["input_tokens"].get<size_t>() - 1;
        expect(base < 512 && json::parse(engine.execute(sized(512 - base)))["usage"]["input_tokens"] == 512,
               "largest accepted prompt uses the whole budget");
        expect(rejected(sized(513 - base), "512-token"), "prompt over budget rejected");
        json long_option = primitive("Please refund me.");
        long_option["questions"]["department"]["criteria"]["billing"] = repeated("payments ", 60);
        expect(rejected(long_option.dump(), "option text exceeds"), "long option rejected");
        json long_head = primitive("Please refund me.");
        long_head["questions"]["refund"]["instructions"] = repeated("refund ", 400);
        expect(rejected(long_head.dump(), "instructions exceed"), "long instructions rejected");
        checkpoint("token and head budgets");
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
        checkpoint("structured decisions");
        // Reentry on both events would deadlock if callbacks held the native lock.
        int reentered = 0;
        struct reentry { ducktape::laya *engine; int *count; std::string request; } data{&engine, &reentered, requests[0].dump()};
        dtl_observer reentrant{7, [](const char *, void *p) {
            auto &d = *static_cast<reentry *>(p);
            d.engine->decide(d.request); ++*d.count;
        }, &data};
        engine.execute(requests[0].dump(), &reentrant);
        expect(reentered == 2, "reentrant hooks");
        dtl_observer throwing{0, [](const char *, void *) { throw std::runtime_error("observer failure"); }, nullptr};
        expect(json::parse(engine.execute(requests[0].dump(), &throwing)) == singles[0], "passive throwing callback");
        checkpoint("observer reentry and exceptions");
        auto dir = std::filesystem::current_path() / ("router-test-" + std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()));
        std::filesystem::create_directory(dir);
        struct cleanup { std::filesystem::path dir; ~cleanup() { std::error_code ec; std::filesystem::remove_all(dir, ec); } } guard{dir};
        std::filesystem::create_hard_link(std::filesystem::canonical(argv[1]), dir / "a.gguf");
        std::filesystem::create_hard_link(std::filesystem::canonical(argv[1]), dir / "b.gguf");
        std::ofstream(dir / "invalid.gguf") << "not a model";
        ducktape::router router(json({{"models",{{"a",(dir / "a.gguf").string()}, {"alias",(dir / "a.gguf").string()},
            {"b",(dir / "b.gguf").string()}, {"bad",(dir / "invalid.gguf").string()}}},{"max_loaded",1}}).dump(), options);
        auto routed = requests[0]; routed["model"] = "a";
        auto answer = json::parse(router.execute(routed.dump()));
        expect(answer["model"] == "a", "explicit route"); answer.erase("model");
        expect(answer == singles[0], "route parity");
        std::filesystem::remove(dir / "a.gguf");
        routed["model"] = "alias"; router.execute(routed.dump()); // canonical-path dedup reuses loaded model
        routed["model"] = "b"; router.execute(routed.dump()); // evicts a at capacity
        routed["model"] = "a"; invalid([&] { router.execute(routed.dump()); }, DTL_MODEL_ERROR);
        routed["model"] = "bad"; invalid([&] { router.execute(routed.dump()); }, DTL_MODEL_ERROR);
        routed["model"] = "unknown"; invalid([&] { router.execute(routed.dump()); });
        invalid([&] { engine.execute(routed.dump()); });
        invalid([&] { router.execute(requests[0].dump()); });
        routed["model"] = "b";
        auto routed_results = json::parse(router.execute(json::array({routed,routed}).dump()));
        expect(routed_results.size() == 2 && routed_results[0] == routed_results[1], "router batch");
        checkpoint("routing and eviction");
        std::cout << "feature tests passed\n";
        return 0;
    } catch (const std::exception &e) { std::cerr << e.what() << '\n'; return 1; }
}
