#include "ducktape_laya.hpp"
#include <nlohmann/json.hpp>
#include <algorithm>
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
        auto observer = dtl_default_call_options();
        observer.request_id = 42;
        observer.on_event = [](const char *event, void *user) { static_cast<std::vector<json> *>(user)->push_back(json::parse(event)); };
        observer.event_user_data = &events;
        expect(dtl_execute(nullptr, "{}", &observer, &result, &error) == DTL_INVALID_ARGUMENT, "null execute");
        expect(!result && error && events.size() == 2 && events[0]["event"] == "start" &&
               events[1]["event"] == "error" && events[1]["request_id"] == 42, "failure observer/output contract");
        dtl_free(error);
        struct reset_check { char **result; char **error; bool reset = false; } reset{&result, &error};
        result = error = reinterpret_cast<char *>(1);
        auto reset_observer = dtl_default_call_options();
        reset_observer.on_event = [](const char *event, void *user) {
            auto &check = *static_cast<reset_check *>(user);
            if (json::parse(event)["event"] == "start") check.reset = !*check.result && !*check.error;
        };
        reset_observer.event_user_data = &reset;
        expect(dtl_execute(nullptr, "{}", &reset_observer, &result, &error) == DTL_INVALID_ARGUMENT,
               "observed error");
        expect(reset.reset && !result && error, "outputs reset before start callback"); dtl_free(error);
        dtl_engine *raw = reinterpret_cast<dtl_engine *>(1);
        expect(dtl_open_router("{}", nullptr, &raw, &error) == DTL_INVALID_ARGUMENT && !raw, "invalid router");
        dtl_free(error); dtl_close(nullptr);
        invalid([] { ducktape::engine::open_router(R"({"models":{"x":"missing.gguf"}})"); }, DTL_MODEL_ERROR);
        {
            // Metadata validation occurs before lazy loading, so no model is needed.
            auto validator = ducktape::engine::open_router(json({{"models", {{"unused", std::filesystem::absolute(argv[0]).string()}}}}).dump());
            auto valid = json::parse(R"({"model":"unused","state":"x","schema":{"type":"object","properties":{"q":{"type":"boolean"}}}})");
            for (const char *key : {"title", "description"}) {
                auto bad = valid; bad["schema"][key] = 3;
                invalid([&] { validator.execute(bad.dump()); });
                bad = valid; bad["schema"]["properties"]["q"][key] = json::object();
                invalid([&] { validator.execute(bad.dump()); });
            }
        }
        {
            auto meta = ducktape::engine::open_router(json({{"models", {{"unused", std::filesystem::absolute(argv[0]).string()}}}}).dump());
            const auto info = json::parse(meta.info());
            expect(info.at("abi_version") == 2 && !info.contains("model"), "router info is runtime-only");
        }
        if (argc < 2) return 0;
        const std::string shared = R"({"threads":2})";
        auto engine = ducktape::engine::open(argv[1], shared);
        json requests = json::array({primitive("Please refund the duplicate payment."),
            primitive("Settings crashes immediately! Fix this now."), primitive("What does the enterprise plan cost?")});
        {
            std::vector<json> seen;
            ducktape::call_options counter;
            counter.request_id = 9;
            counter.on_event = [&](const std::string &event) { seen.push_back(json::parse(event)); };
            const auto observed = engine.execute(requests[0].dump(), counter);
            expect(seen.size() == 2 && seen[0]["event"] == "start" && seen[1]["event"] == "end" && seen[0]["request_id"] == 9 &&
                   observed == engine.execute(requests[0].dump()), "events do not change results");
        }
        // Exercise all primitive types across the batch. Routing and observer
        // assertions need one question per request, not three repeated forward
        // passes; multi-question primitives have separate conformance fixtures.
        const char *question_ids[] = {"refund", "department", "urgency"};
        for (size_t i = 0; i < requests.size(); ++i) {
            const auto question = requests[i]["questions"][question_ids[i]];
            requests[i]["questions"] = {{question_ids[i], question}};
        }
        json singles = json::array();
        for (auto &r : requests) singles.push_back(json::parse(engine.execute(r.dump())));
        checkpoint("single requests");
        events.clear();
        expect(json::parse(engine.execute(requests.dump(), {42, 0, [&](const std::string &e) { events.push_back(json::parse(e)); }})) == singles, "exact batch parity");
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
        {   // Overflow policies. Expected counts come from a fitting prompt's own report:
            // head = input tokens minus state tokens, so a cut keeps 512 - head tokens.
            const size_t room = 512 - base, words = room + 300;
            auto over = [&](const std::string &policy, size_t n) {
                json r = json::parse(sized(n)); r["overflow"] = policy;
                return json::parse(engine.execute(r.dump()));
            };
            const auto fit = json::parse(engine.execute(sized(room)));
            const auto fits = over("scan", room);
            expect(fits["answers"] == fit["answers"] && fits["usage"]["state_tokens_dropped"] == 0, "a fitting state reads as before");
            const size_t head = fits["usage"]["input_tokens"].get<size_t>() - fits["usage"]["state_tokens"].get<size_t>();
            const size_t kept = 512 - head, state_tokens = fits["usage"]["state_tokens"].get<size_t>() + (words - room);
            for (const char *policy : {"truncate_end", "truncate_start"}) {
                auto r = over(policy, words);
                expect(r["answers"]["refund"].contains("noul") && !r["answers"]["refund"].contains("window") &&
                       r["usage"]["input_tokens"] == 512 && r["usage"]["state_tokens"] == state_tokens &&
                       r["usage"]["state_tokens_dropped"] == state_tokens - kept &&
                       r["usage"]["truncated_questions"] == json::array({"refund"}), policy);
            }
            const size_t stride = kept / 2, windows = 1 + (state_tokens - kept + stride - 1) / stride;
            auto scanned = over("scan", words);
            auto answer = scanned["answers"]["refund"];
            expect(answer["window"]["count"] == windows && answer["window"]["token_end"].get<size_t>() -
                   answer["window"]["token_start"].get<size_t>() == kept && scanned["usage"]["input_tokens"] == 512 * windows &&
                   scanned["usage"]["state_tokens_dropped"] == 0 && scanned["usage"]["truncated_questions"].empty(),
                   "scan reads every window");
            json all = json::parse(sized(words));
            all["overflow"] = "scan"; all["questions"]["refund"]["aggregate"] = "all";
            auto minimum = json::parse(engine.execute(all.dump()))["answers"]["refund"];
            expect(minimum["noul"] <= answer["noul"] && minimum["window"]["count"] == windows, "aggregate all is the minimum");
            expect(rejected([&] { json r = json::parse(sized(words)); r["overflow"] = "scan"; r["max_windows"] = 1; return r.dump(); }(),
                            "max_windows"), "too many windows rejected");
            for (json bad : {json({{"overflow", "clip"}}), json({{"max_windows", 0}}), json({{"overflow", 1}})}) {
                json r = json::parse(sized(1));
                for (auto &[k, v] : bad.items()) r[k] = v;
                invalid([&] { engine.execute(r.dump()); });
            }
            json wrong_aggregate = primitive("Please refund me.");
            wrong_aggregate["questions"]["department"]["aggregate"] = "all";
            invalid([&] { engine.execute(wrong_aggregate.dump()); });
            // Head shortening as llama.cpp's decision server does, reported per question.
            json shortened = long_option; shortened["overflow"] = "truncate_end";
            auto cut = json::parse(engine.execute(shortened.dump()));
            expect(cut["usage"]["truncated_questions"] == json::array({"department"}) && cut["usage"]["state_tokens_dropped"] == 0,
                   "long option shortened and reported");
            // The engine default applies unless a request names a policy.
            auto lenient = ducktape::engine::open(argv[1], R"({"threads":2,"overflow":"truncate_end"})");
            expect(json::parse(lenient.execute(sized(words))) == over("truncate_end", words), "engine default overflow");
            invalid([&] { json r = json::parse(sized(words)); r["overflow"] = "reject"; lenient.execute(r.dump()); });
            invalid([&] { ducktape::engine::open(argv[1], R"({"overflow":"clip"})"); });
        }
        checkpoint("overflow policies");
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
        ducktape::call_options reentrant;
        reentrant.request_id = 7;
        reentrant.on_event = [&](const std::string &) { engine.execute(requests[0].dump()); ++reentered; };
        engine.execute(requests[0].dump(), reentrant);
        expect(reentered == 2, "reentrant hooks");
        ducktape::call_options throwing;
        throwing.on_event = [](const std::string &) { throw std::runtime_error("observer failure"); };
        expect(json::parse(engine.execute(requests[0].dump(), throwing)) == singles[0], "passive throwing callback");
        checkpoint("observer reentry and exceptions");
        auto dir = std::filesystem::current_path() / ("router-test-" + std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()));
        std::filesystem::create_directory(dir);
        struct cleanup { std::filesystem::path dir; ~cleanup() { std::error_code ec; std::filesystem::remove_all(dir, ec); } } guard{dir};
        std::filesystem::create_hard_link(std::filesystem::canonical(argv[1]), dir / "a.gguf");
        std::filesystem::create_hard_link(std::filesystem::canonical(argv[1]), dir / "b.gguf");
        std::ofstream(dir / "invalid.gguf") << "not a model";
        auto router = ducktape::engine::open_router(json({{"models",{{"a",(dir / "a.gguf").string()}, {"alias",(dir / "a.gguf").string()},
            {"b",(dir / "b.gguf").string()}, {"bad",(dir / "invalid.gguf").string()}}},{"max_loaded",1}}).dump(), shared);
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
        // ABI 2 settings. Open config: temperatures override the model's calibration; the
        // same override in a request gives the identical result, and a request wins over the engine.
        const json plain = singles[0];
        auto with = [](json request, const char *key, json value) { request[key] = std::move(value); return request; };
        const json hot = {{"noul.2", 4.0}}, hotter = {{"noul.2", 8.0}};
        auto hot_engine = ducktape::engine::open(argv[1], json({{"threads", 2}, {"temperatures", hot}}).dump());
        const auto engine_hot = json::parse(hot_engine.execute(requests[0].dump()));
        expect(engine_hot["answers"] != plain["answers"], "open temperatures change probabilities");
        expect(json::parse(engine.execute(with(requests[0], "temperatures", hot).dump())) == engine_hot,
               "request temperatures equal the same engine override");
        expect(json::parse(hot_engine.execute(with(requests[0], "temperatures", hotter).dump())) ==
               json::parse(engine.execute(with(requests[0], "temperatures", hotter).dump())), "request temperatures win over the engine's");
        expect(json::parse(engine.execute(requests[0].dump())) == plain, "request temperatures do not persist");
        for (auto bad : {json(0), json(-1), json("x"), json(nullptr), json::array()}) {
            invalid([&] { engine.execute(with(requests[0], "temperatures", json{{"noul.2", bad}}).dump()); });
            invalid([&] { ducktape::engine::open(argv[1], json({{"temperatures", {{"noul.2", bad}}}}).dump()); });
        }
        invalid([&] { engine.execute(with(requests[0], "temperatures", 3).dump()); });
        // Temperatures stored in the model are bounded to [0.5, 5] (the pinned model's
        // choice.11 is 0.1006); temperatures the app passes are used as given.
        const auto calibration = json::parse(engine.info())["model"];
        for (const auto &[key, t] : calibration["metadata_temperatures"].items())
            expect(calibration["temperatures"][key] == std::clamp(t.get<float>(), 0.5f, 5.0f), "stored temperatures are bounded");
        expect(calibration["metadata_temperatures"]["choice.11"].get<float>() < 0.5f &&
               calibration["temperatures"]["choice.11"] == 0.5f, "the shipped choice.11 temperature is bounded");
        auto sharp = ducktape::engine::open(argv[1], json({{"threads", 2}, {"temperatures", {{"choice.11", 0.1}}}}).dump());
        expect(json::parse(sharp.info())["model"]["temperatures"]["choice.11"] == 0.1f, "app temperatures are not bounded");
        // Open config: validation and context size.
        const auto model_info = json::parse(engine.info())["model"];
        const size_t trained = model_info.at("trained_context_tokens").get<size_t>();
        expect(model_info.at("context_tokens") == std::min<size_t>(512, trained) && trained >= 512, "default context");
        for (const char *bad : {R"({"threads":257})", R"({"threads":-1})", R"({"threads":"2"})", R"({"bogus":1})",
                                R"({"context_tokens":31})", R"({"context_tokens":0})", R"({"context_tokens":"64"})", R"([])", "{"})
            invalid([&] { ducktape::engine::open(argv[1], bad); });
        invalid([&] { ducktape::engine::open(argv[1], json({{"context_tokens", trained + 1}}).dump()); });
        {
            auto small = ducktape::engine::open(argv[1], R"({"threads":0,"context_tokens":32})");
            expect(json::parse(small.info())["model"]["context_tokens"] == 32, "context_tokens applies");
            auto tiny = ducktape::engine::open(argv[1], R"({"threads":1,"context_tokens":64})");
            expect(json::parse(tiny.info())["model"]["context_tokens"] == 64, "context_tokens 64");
            invalid([&] { tiny.execute(sized(70 - base)); });
            invalid([&] { tiny.execute(with(requests[0], "max_tokens", 65).dump()); });
        }
        // Per-request max_tokens: a prompt over it is rejected; it never changes results; it cannot
        // exceed the engine's context_tokens.
        const size_t fit = json::parse(engine.execute(sized(1)))["usage"]["input_tokens"].get<size_t>();
        expect(json::parse(engine.execute(with(json::parse(sized(1)), "max_tokens", fit).dump())) == json::parse(engine.execute(sized(1))),
               "max_tokens at the prompt size is accepted and changes nothing");
        expect(rejected(with(json::parse(sized(2)), "max_tokens", fit).dump(), (std::to_string(fit) + "-token").c_str()), "prompt over max_tokens");
        expect(rejected(with(json::parse(sized(1)), "max_tokens", 513).dump(), "context_tokens"), "max_tokens over context_tokens");
        for (auto bad : {json(0), json(-5), json(1.5), json("9"), json(nullptr)})
            invalid([&] { engine.execute(with(json::parse(sized(1)), "max_tokens", bad).dump()); });
        expect(json::parse(engine.execute(json::array({with(json::parse(sized(1)), "max_tokens", fit), json::parse(sized(1))}).dump())).size() == 2,
               "max_tokens is per request in a batch");
        invalid([&] { engine.execute(json::array({with(json::parse(sized(1)), "max_tokens", fit), with(json::parse(sized(2)), "max_tokens", fit)}).dump()); });
        checkpoint("open config and per-request settings");
        {
            // Router: the open config applies to every route; a route's own config overrides it key by key.
            std::filesystem::create_hard_link(std::filesystem::canonical(argv[1]), dir / "c.gguf");
            const auto b = (dir / "b.gguf").string(), c = (dir / "c.gguf").string();
            auto overridden = ducktape::engine::open_router(json({{"models", {{"inherit", b},
                {"hotter", {{"path", c}, {"config", {{"temperatures", hotter}}}}},
                {"small", {{"path", c}, {"config", {{"context_tokens", 64}}}}}}}, {"max_loaded", 2}}).dump(),
                json({{"threads", 2}, {"temperatures", hot}}).dump());
            const json first_request = requests[0];
            auto route = [&](const char *id, json request) {
                auto result = json::parse(overridden.execute(with(request, "model", id).dump()));
                expect(result.at("model") == id, "route id");
                result.erase("model");
                return result;
            };
            expect(route("inherit", first_request) == engine_hot, "route inherits the shared config");
            expect(route("hotter", first_request)["answers"] != engine_hot["answers"] && route("hotter", first_request) ==
                   json::parse(engine.execute(with(requests[0], "temperatures", hotter).dump())), "route config overrides the shared temperatures");
            invalid([&] { route("small", json::parse(sized(70 - base))); });
            expect(route("small", json::parse(sized(1)))["usage"] == json::parse(engine.execute(sized(1)))["usage"], "route context_tokens applies");
            invalid([&] { ducktape::engine::open_router(json({{"models", {{"x", {{"path", b}, {"config", {{"bogus", 1}}}}}}}}).dump()); });
        }
        checkpoint("router configuration");
        std::cout << "feature tests passed\n";
        return 0;
    } catch (const std::exception &e) { std::cerr << e.what() << '\n'; return 1; }
}
