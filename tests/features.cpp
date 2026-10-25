// Feature tests: without a model (feature-errors) the model-free contracts; with one
// (real-features) every part, in this order: requests, batches and budgets (here), overflow
// policies (features_overflow.cpp), structured decisions (features_structured.cpp), observers,
// routing and open config (here), the shared structured state (features_structured.cpp), router
// configuration and automatic routing (here). Each part prints its checkpoint label when done.
#include "features_support.hpp"
#include <algorithm>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <vector>
#include <chrono>
#include "thread-cpu-time.hpp"
#ifdef DTL_COUNTS_ALLOCATIONS
#include <atomic>
#include <cstdlib>
#include <new>
// Counts every operator new of the process; on Linux the library's calls bind to this
// definition too (ELF symbol interposition, as tests/faults.cpp relies on).
static std::atomic<long> allocations{0};
void *operator new(std::size_t size) {
    allocations.fetch_add(1, std::memory_order_relaxed);
    if (void *p = std::malloc(size ? size : 1)) return p;
    throw std::bad_alloc();
}
void operator delete(void *p) noexcept { std::free(p); }
void operator delete(void *p, std::size_t) noexcept { std::free(p); }
long features::allocation_count() { return allocations.load(); }
#endif
namespace features {
long resident_kib() {
#ifdef __linux__
    std::ifstream status("/proc/self/status");
    std::string name;
    while (status >> name) { if (name == "VmRSS:") { long value = -1; status >> value; return value; } std::getline(status, name); }
#endif
    return -1;
}
json primitive(const std::string &state) {
    return {{"state",state}, {"questions", {
        {"department", {{"type","choice"},{"instructions","Which department should handle this?"},
            {"criteria",{{"billing","Payments and refunds"},{"technical","Bugs and crashes"},{"sales","Buying products"}}}}},
        {"refund", {{"type","noul"},{"instructions","Does the customer request a refund?"}}},
        {"urgency", {{"type","score"},{"instructions","How urgent is this?"},{"criteria",{"low","moderate","high"}}}}
    }}};
}
namespace {
// Observer and output contracts and request validation that need no model.
void model_free(const char *self) {
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
        auto validator = ducktape::engine::open_router(json({{"models", {{"unused", std::filesystem::absolute(self).string()}}}}).dump());
        auto valid = json::parse(R"({"model":"unused","state":"x","schema":{"type":"object","properties":{"q":{"type":"boolean"}}}})");
        for (const char *key : {"title", "description"}) {
            auto bad = valid; bad["schema"][key] = 3;
            invalid([&] { validator.execute(bad.dump()); });
            bad = valid; bad["schema"]["properties"]["q"][key] = json::object();
            invalid([&] { validator.execute(bad.dump()); });
        }
    }
    {
        auto meta = ducktape::engine::open_router(json({{"models", {{"unused", std::filesystem::absolute(self).string()}}}}).dump());
        const auto info = json::parse(meta.info());
        expect(info.at("abi_version") == 2 && !info.contains("model"), "router info is runtime-only");
    }
}

// Single requests, observers on success, batches, request bounds, the 512-token boundary,
// serialized state limits, big integers, runs of spaces and the head budgets.
void requests_and_budgets(context &c) {
    auto &engine = c.engine;
    auto &requests = c.requests;
    std::vector<json> events;
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
    json &singles = c.singles;
    singles = json::array();
    for (auto &r : requests) singles.push_back(json::parse(engine.execute(r.dump())));
    c.checkpoint("single requests");
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
    c.checkpoint("batches and bounds");
    // Overflow is rejected, never truncated. Each repeated word is one token, so the
    // boundary is exact; rejected prompts fail during planning, before any inference.
    const size_t base = c.base = json::parse(engine.execute(c.sized(1)))["usage"]["input_tokens"].get<size_t>() - 1;
    expect(base < 512 && json::parse(engine.execute(c.sized(512 - base)))["usage"]["input_tokens"] == 512,
           "largest accepted prompt uses the whole budget");
    expect(c.rejected(c.sized(513 - base), "512-token"), "prompt over budget rejected");
    {
        // Serialization can make a structured state longer than its request (1e15 is
        // written 1000000000000000.0); the serialized text is bounded before tokenizing.
        const std::string question = R"(,"questions":{"refund":)" + requests[0]["questions"]["refund"].dump() + "}}";
        std::string numbers = "[1e15";
        while (numbers.size() < 600000) numbers += ",1e15";
        const std::string request = R"({"state":)" + numbers + "]" + question;
        expect(request.size() < 1048576 && c.rejected(request, "Serialized state is too large"), "serialized state over 2 MiB rejected");
        // Integers outside int64/uint64 enter the prompt with all their digits, as the
        // original Laya writes them: the same as passing that text as the state.
        expect(engine.execute(R"({"state":{"n":100000000000000000000000,"m":-9223372036854775809})" + question) ==
               engine.execute(R"({"state":"{\"n\": 100000000000000000000000, \"m\": -9223372036854775809}")" + question),
               "big integers are serialized exactly");
        // Up to the double range (309 digits) such integers are kept; beyond it the JSON
        // number cannot be represented and the request is rejected with status 1.
        const std::string within = "1" + std::string(308, '0'), beyond = "1" + std::string(309, '0');
        expect(engine.execute(R"({"state":{"n":)" + within + ",\"m\":-" + within + "}" + question) ==
               engine.execute(R"({"state":"{\"n\": )" + within + ", \\\"m\\\": -" + within + "}\"" + question),
               "309-digit integers are serialized exactly");
        for (const std::string &number : {beyond, "-" + beyond, "1" + std::string(400, '0')})
            invalid([&] { engine.execute(R"({"state":{"n":)" + number + "}" + question); });
    }
    {
        // Runs of spaces have no length limit (0.2.0 rejected runs over 4,096) and tokenize in
        // linear time: 128 KiB of spaces in the state takes no longer than twice the same size
        // of ordinary words. Both are truncated to one 64-token window, so the calls' time is
        // mostly tokenization, which runs on the calling thread: each is timed in that thread's
        // CPU time over at least 0.25 s of calls, every state new so that none reuses the
        // previous call's tokens.
        unsigned nonce = 0;
        const auto timed = [&](const std::string &state) {
            return dtl_test::cpu_seconds_per_run([&] {
                const json request = {{"state", "Refund " + std::to_string(++nonce) + "." + state + "Thanks."}, {"overflow", "truncate_end"},
                                      {"max_tokens", 64}, {"questions", {{"refund", requests[0]["questions"]["refund"]}}}};
                const auto result = json::parse(engine.execute(request.dump()));
                expect(result["usage"]["state_tokens_dropped"].get<size_t>() > 0, "a long state is truncated");
            });
        };
        const double spaces = timed(std::string(128 << 10, ' ')), words = timed(repeated("refund ", (128 << 10) / 7));
        std::cerr << "128 KiB of spaces: " << spaces << " s CPU per call; of words: " << words << " s\n";
        expect(spaces <= 2 * words, "a long run of spaces tokenizes in about the time of ordinary text");
    }
    json &long_option = c.long_option;
    long_option = primitive("Please refund me.");
    long_option["questions"]["department"]["criteria"]["billing"] = repeated("payments ", 60);
    expect(c.rejected(long_option.dump(), "option text exceeds"), "long option rejected");
    json long_head = primitive("Please refund me.");
    long_head["questions"]["refund"]["instructions"] = repeated("refund ", 400);
    expect(c.rejected(long_head.dump(), "instructions exceed"), "long instructions rejected");
    c.checkpoint("token and head budgets");
}

// Observer reentry and exceptions, routing and eviction, temperatures, open config, context
// size, the no-logits resident set and per-request max_tokens.
void observers_routing_and_config(context &c) {
    auto &engine = c.engine;
    const auto &requests = c.requests;
    const auto &singles = c.singles;
    const char *model = c.model;
    const size_t base = c.base;
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
    c.checkpoint("observer reentry and exceptions");
    auto dir = c.work.dir = std::filesystem::current_path() / ("router-test-" + std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()));
    std::filesystem::create_directory(dir);
    std::filesystem::create_hard_link(std::filesystem::canonical(model), dir / "a.gguf");
    std::filesystem::create_hard_link(std::filesystem::canonical(model), dir / "b.gguf");
    std::ofstream(dir / "invalid.gguf") << "not a model";
    // Route paths are UTF-8, also outside the Windows ANSI code page and the BMP.
    const auto unicode = dir / std::filesystem::u8path("m\u00f6del-\u6a21\u578b-\U0001F986.gguf");
    std::filesystem::create_hard_link(std::filesystem::canonical(model), unicode);
    const std::string shared = R"({"threads":2})";
    auto router = ducktape::engine::open_router(json({{"models",{{"a",(dir / "a.gguf").string()}, {"alias",(dir / "a.gguf").string()},
        {"b",(dir / "b.gguf").string()}, {"bad",(dir / "invalid.gguf").string()}, {"unicode",unicode.u8string()}}},{"max_loaded",1}}).dump(), shared);
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
    routed["model"] = "unicode";
    answer = json::parse(router.execute(routed.dump()));
    answer.erase("model");
    expect(answer == singles[0], "non-ASCII route");
    c.checkpoint("routing and eviction");
    // ABI 2 settings. Open config: temperatures override the model's calibration; the
    // same override in a request gives the identical result, and a request wins over the engine.
    const json plain = singles[0];
    const json &hot = c.hot = {{"noul.2", 4.0}};
    const json &hotter = c.hotter = {{"noul.2", 8.0}};
    auto hot_engine = ducktape::engine::open(model, json({{"threads", 2}, {"temperatures", hot}}).dump());
    const auto &engine_hot = c.engine_hot = json::parse(hot_engine.execute(requests[0].dump()));
    expect(engine_hot["answers"] != plain["answers"], "open temperatures change probabilities");
    expect(json::parse(engine.execute(with(requests[0], "temperatures", hot).dump())) == engine_hot,
           "request temperatures equal the same engine override");
    expect(json::parse(hot_engine.execute(with(requests[0], "temperatures", hotter).dump())) ==
           json::parse(engine.execute(with(requests[0], "temperatures", hotter).dump())), "request temperatures win over the engine's");
    expect(json::parse(engine.execute(requests[0].dump())) == plain, "request temperatures do not persist");
    for (auto bad : {json(0), json(-1), json("x"), json(nullptr), json::array(), json(1e-50), json(1e39)}) {
        invalid([&] { engine.execute(with(requests[0], "temperatures", json{{"noul.2", bad}}).dump()); });
        invalid([&] { ducktape::engine::open(model, json({{"temperatures", {{"noul.2", bad}}}}).dump()); });
    }
    invalid([&] { engine.execute(with(requests[0], "temperatures", 3).dump()); });
    // Temperatures stored in the model are bounded to [0.5, 5] (the pinned model's
    // choice.11 is 0.1006); temperatures the app passes are used as given.
    const auto calibration = json::parse(engine.info())["model"];
    for (const auto &[key, t] : calibration["metadata_temperatures"].items())
        expect(calibration["temperatures"][key] == std::clamp(t.get<float>(), 0.5f, 5.0f), "stored temperatures are bounded");
    expect(calibration["metadata_temperatures"]["choice.11"].get<float>() < 0.5f &&
           calibration["temperatures"]["choice.11"] == 0.5f, "the shipped choice.11 temperature is bounded");
    auto sharp = ducktape::engine::open(model, json({{"threads", 2}, {"temperatures", {{"choice.11", 0.1}}}}).dump());
    expect(json::parse(sharp.info())["model"]["temperatures"]["choice.11"] == 0.1f, "app temperatures are not bounded");
    // Open config: context size (the config rejections themselves are in control-errors).
    const auto model_info = json::parse(engine.info())["model"];
    const size_t trained = model_info.at("trained_context_tokens").get<size_t>();
    expect(model_info.at("context_tokens") == std::min<size_t>(512, trained) && trained >= 512, "default context");
    invalid([&] { ducktape::engine::open(model, json({{"context_tokens", trained + 1}}).dump()); });
    {
        auto small = ducktape::engine::open(model, R"({"threads":0,"context_tokens":32})");
        expect(json::parse(small.info())["model"]["context_tokens"] == 32, "context_tokens applies");
        auto tiny = ducktape::engine::open(model, R"({"threads":1,"context_tokens":64})");
        expect(json::parse(tiny.info())["model"]["context_tokens"] == 64, "context_tokens 64");
        invalid([&] { tiny.execute(c.sized(70 - base)); });
        invalid([&] { tiny.execute(with(requests[0], "max_tokens", 65).dump()); });
    }
#ifdef DTL_SANITIZED
    std::cout << "SKIPPED: no-logits resident-set check (sanitizer runtimes keep freed memory in quarantine)\n";
    if (false) {
#else
    if (resident_kib() < 0) std::cout << "NOT APPLICABLE: no-logits resident-set check (reads VmRSS; Linux only)\n";
    else {
#endif
        // No logits buffer (llama-embeddings-no-logits-buffer.patch): the decision graph has no
        // logits, and llama.cpp used to reserve and zero-fill n_vocab floats per prompt token
        // anyway. A full 256-token prompt would then add at least 50,368 x 256 x 4 bytes (49 MiB;
        // 50,368 is the smallest Laya vocabulary) to the resident set; without it the growth
        // measured about 9 MiB. The first call's growth must stay under half that buffer.
        auto window = ducktape::engine::open(model, R"({"threads":2,"context_tokens":256})");
        window.execute(c.sized(1));
        const long before = resident_kib();
        expect(json::parse(window.execute(c.sized(256 - base)))["usage"]["input_tokens"] == 256, "full 256-token prompt");
        const long growth = resident_kib() - before;
        std::cerr << "resident set growth for a full 256-token prompt: " << growth << " KiB\n";
        expect(growth < 50368L * 256 * 4 / 1024 / 2, "no logits buffer is reserved for the decision graph");
    }
    // Per-request max_tokens: a prompt over it is rejected; it never changes results; it cannot
    // exceed the engine's context_tokens.
    const size_t fit = json::parse(engine.execute(c.sized(1)))["usage"]["input_tokens"].get<size_t>();
    expect(json::parse(engine.execute(with(json::parse(c.sized(1)), "max_tokens", fit).dump())) == json::parse(engine.execute(c.sized(1))),
           "max_tokens at the prompt size is accepted and changes nothing");
    expect(c.rejected(with(json::parse(c.sized(2)), "max_tokens", fit).dump(), (std::to_string(fit) + "-token").c_str()), "prompt over max_tokens");
    expect(c.rejected(with(json::parse(c.sized(1)), "max_tokens", 513).dump(), "context_tokens"), "max_tokens over context_tokens");
    for (auto bad : {json(0), json(-5), json(1.5), json("9"), json(nullptr)})
        invalid([&] { engine.execute(with(json::parse(c.sized(1)), "max_tokens", bad).dump()); });
    expect(json::parse(engine.execute(json::array({with(json::parse(c.sized(1)), "max_tokens", fit), json::parse(c.sized(1))}).dump())).size() == 2,
           "max_tokens is per request in a batch");
    invalid([&] { engine.execute(json::array({with(json::parse(c.sized(1)), "max_tokens", fit), with(json::parse(c.sized(2)), "max_tokens", fit)}).dump()); });
    c.checkpoint("open config and per-request settings");
}

// Router configuration: shared and per-route config, per-route default overflow.
void router_configuration(context &c) {
    auto &engine = c.engine;
    const auto &requests = c.requests;
    const auto &dir = c.work.dir;
    const auto &hot = c.hot, &hotter = c.hotter, &engine_hot = c.engine_hot;
    const size_t base = c.base;
    {
        // Router: the open config applies to every route; a route's own config overrides it key by key.
        std::filesystem::create_hard_link(std::filesystem::canonical(c.model), dir / "c.gguf");
        const auto b = (dir / "b.gguf").string(), cc = (dir / "c.gguf").string();
        auto overridden = ducktape::engine::open_router(json({{"models", {{"inherit", b},
            {"hotter", {{"path", cc}, {"config", {{"temperatures", hotter}}}}},
            {"small", {{"path", cc}, {"config", {{"context_tokens", 64}}}}}}}, {"max_loaded", 2}}).dump(),
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
        invalid([&] { route("small", json::parse(c.sized(70 - base))); });
        expect(route("small", json::parse(c.sized(1)))["usage"] == json::parse(engine.execute(c.sized(1)))["usage"], "route context_tokens applies");
        invalid([&] { ducktape::engine::open_router(json({{"models", {{"x", {{"path", b}, {"config", {{"bogus", 1}}}}}}}}).dump()); });
        // Routes sharing one loaded model keep their own default overflow policy.
        auto policies = ducktape::engine::open_router(json({{"models", {{"lenient", {{"path", b}, {"config", {{"overflow", "truncate_end"}}}}},
                                                                        {"strict", b}}}}).dump());
        const auto long_request = json::parse(c.sized(600));
        expect(json::parse(policies.execute(with(long_request, "model", "lenient").dump()))["usage"]["state_tokens_dropped"] > 0,
               "route overflow default applies");
        expect(c.rejected(with(long_request, "model", "strict").dump(), "token limit", &policies), "other route keeps reject");
    }
    c.checkpoint("router configuration");
}

// "model": "auto": language routing.
void automatic_routing(context &c) {
    const auto &dir = c.work.dir;
    {
        // "model": "auto": confidently English text goes to auto.english, everything else to
        // auto.default; "lang" overrides detection; usage reports why.
        const auto b = (dir / "b.gguf").string(), cc = (dir / "c.gguf").string();
        const json routes = {{"en", b}, {"ml", cc}};
        auto automatic = ducktape::engine::open_router(json({{"models", routes}, {"auto", {{"english", "en"}, {"default", "ml"}}}}).dump(),
                                                       json({{"threads", 2}}).dump());
        json question = {{"q", {{"type", "noul"}, {"instructions", "Is a refund requested?"}}}};
        auto ask = [&](json state, json lang = nullptr) {
            json request = {{"model", "auto"}, {"state", std::move(state)}, {"questions", question}};
            if (!lang.is_null()) request["lang"] = lang;
            return json::parse(automatic.execute(request.dump()));
        };
        auto english = ask("The customer was charged twice and wants a refund for this invoice.");
        expect(english["model"] == "en" && english["usage"]["routing"] == json({{"reason", "english_text"}, {"language", "en"}}), "auto routes English text");
        auto hindi = ask({{"body", "ग्राहक से दो बार शुल्क लिया गया और वह धनवापसी चाहता है।"}});
        expect(hindi["model"] == "ml" && hindi["usage"]["routing"] == json({{"reason", "non_latin_script"}, {"script", "devanagari"}}), "auto routes non-Latin text");
        expect(ask("refund me")["model"] == "ml", "auto sends short undecided text to the default");
        auto forced = ask("Der Kunde wurde zweimal belastet und möchte eine Rückerstattung.", "en-US");
        expect(forced["model"] == "en" && forced["usage"]["routing"] == json({{"reason", "lang"}, {"lang", "en-US"}}), "lang overrides detection");
        expect(ask("The customer was charged twice.", "de")["model"] == "ml", "a non-English lang goes to the default");
        auto plain = json::parse(automatic.execute(json({{"model", "en"}, {"state", "x"}, {"questions", question}}).dump()));
        expect(!plain["usage"].contains("routing"), "explicit routes report no routing");
        invalid([&] { automatic.execute(json({{"model", "en"}, {"lang", "en"}, {"state", "x"}, {"questions", question}}).dump()); });
        invalid([&] { ask("x", ""); });
        invalid([&] { ask("x", 3); });
        auto manual = ducktape::engine::open_router(json({{"models", routes}}).dump());
        invalid([&] { manual.execute(json({{"model", "auto"}, {"state", "x"}, {"questions", question}}).dump()); });
        invalid([&] { ducktape::engine::open_router(json({{"models", {{"auto", b}}}}).dump()); });
        invalid([&] { ducktape::engine::open_router(json({{"models", routes}, {"auto", {{"english", "en"}, {"default", "nope"}}}}).dump()); });
        invalid([&] { ducktape::engine::open_router(json({{"models", routes}, {"auto", {{"english", "en"}}}}).dump()); });
        invalid([&] { ducktape::engine::open_router(json({{"models", routes}, {"auto", {{"english", "en"}, {"default", "ml"}, {"x", 1}}}}).dump()); });
    }
    c.checkpoint("automatic routing");
}
} // namespace
} // namespace features

int main(int argc, char **argv) {
    using namespace features;
    try {
        const auto started = std::chrono::steady_clock::now();
        model_free(argv[0]);
        if (argc < 2) return 0;
        const std::string shared = R"({"threads":2})";
        auto engine = ducktape::engine::open(argv[1], shared);
        context c{argv[1], engine, started};
        c.requests = json::array({primitive("Please refund the duplicate payment."),
            primitive("Settings crashes immediately! Fix this now."), primitive("What does the enterprise plan cost?")});
        // The parts run in this order; each prints its checkpoints.
        requests_and_budgets(c);           // single requests, batches and bounds, token and head budgets
        overflow_policies(c);              // overflow policies
        structured_decisions(c);           // structured decisions
        observers_routing_and_config(c);   // observer reentry and exceptions, routing and eviction, open config and per-request settings
        structured_state_shared(c);        // structured state shared by questions
        router_configuration(c);           // router configuration
        automatic_routing(c);              // automatic routing
        std::cout << "feature tests passed\n";
        return 0;
    } catch (const std::exception &e) { std::cerr << e.what() << '\n'; return 1; }
}
