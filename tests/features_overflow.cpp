// Overflow policies (reject, truncate_end, truncate_start, scan, aggregates, max_windows, head
// shortening, the engine default). Their logic is arithmetic on token counts that does not depend
// on the context size, so they run on a 64-token engine: a few forward passes of 64 tokens instead
// of about twenty of 512. The default 512-token boundary is checked in features.cpp.
#include "features_support.hpp"
#include <algorithm>

namespace features {
void overflow_policies(context &c) {
    const auto &requests = c.requests;
    const auto &long_option = c.long_option;
    auto &engine = c.engine;
    {   // Overflow policies. Expected counts come from a fitting prompt's own report:
        // head = input tokens minus state tokens, so a cut keeps context - head tokens.
        const size_t context_tokens = 64;
        auto small = ducktape::engine::open(c.model, R"({"threads":2,"context_tokens":64})");
        const size_t base = json::parse(small.execute(c.sized(1)))["usage"]["input_tokens"].get<size_t>() - 1;
        expect(base + 8 < context_tokens, "the 64-token engine leaves room for a state");
        const size_t room = context_tokens - base, words = room + 30;
        auto over = [&](const std::string &policy, size_t n) {
            json r = json::parse(c.sized(n)); r["overflow"] = policy;
            return json::parse(small.execute(r.dump()));
        };
        const auto fit = json::parse(small.execute(c.sized(room)));
        const auto fits = over("scan", room);
        expect(fits["answers"] == fit["answers"] && fits["usage"]["state_tokens_dropped"] == 0, "a fitting state reads as before");
        const size_t head = fits["usage"]["input_tokens"].get<size_t>() - fits["usage"]["state_tokens"].get<size_t>();
        const size_t kept = context_tokens - head, state_tokens = fits["usage"]["state_tokens"].get<size_t>() + (words - room);
        for (const char *policy : {"truncate_end", "truncate_start"}) {
            auto r = over(policy, words);
            expect(r["answers"]["refund"].contains("noul") && !r["answers"]["refund"].contains("window") &&
                   r["usage"]["input_tokens"] == context_tokens && r["usage"]["state_tokens"] == state_tokens &&
                   r["usage"]["state_tokens_dropped"] == state_tokens - kept &&
                   r["usage"]["truncated_questions"] == json::array({"refund"}), policy);
        }
        const size_t stride = kept / 2, windows = 1 + (state_tokens - kept + stride - 1) / stride;
        auto scanned = over("scan", words);
        auto answer = scanned["answers"]["refund"];
        expect(windows > 2 && answer["window"]["count"] == windows && answer["window"]["token_end"].get<size_t>() -
               answer["window"]["token_start"].get<size_t>() == kept && scanned["usage"]["input_tokens"] == context_tokens * windows &&
               scanned["usage"]["state_tokens_dropped"] == 0 && scanned["usage"]["truncated_questions"].empty(),
               "scan reads every window");
        json all = json::parse(c.sized(words));
        all["overflow"] = "scan"; all["questions"]["refund"]["aggregate"] = "all";
        auto minimum = json::parse(small.execute(all.dump()))["answers"]["refund"];
        expect(minimum["noul"] <= answer["noul"] && minimum["window"]["count"] == windows, "aggregate all is the minimum");
        {   // Which tokens each policy keeps, by value: a state whose start and end differ.
            const size_t tail = kept / 3; // words, fewer tokens than a scan stride; their token count comes from the engine
            auto mixed = [&](size_t refunds, size_t others, const char *policy) {
                json r = {{"state", repeated("refund ", refunds) + repeated("weather ", others)},
                          {"questions", {{"refund", requests[0]["questions"]["refund"]}}}};
                if (policy) r["overflow"] = policy;
                return json::parse(small.execute(r.dump()));
            };
            auto end_cut = mixed(kept, tail, "truncate_end"), start_cut = mixed(kept, tail, "truncate_start");
            const size_t tail_tokens = end_cut["usage"]["state_tokens"].get<size_t>() - kept;
            expect(tail >= 4 && tail_tokens >= tail && tail_tokens < kept, "the tail is cut off by truncate_end");
            // A longer state of the same words cut the same way keeps the same tokens: the
            // first `kept` refunds, or the tail preceded by refunds.
            expect(end_cut["answers"] == mixed(kept + 50, 0, "truncate_end")["answers"], "truncate_end keeps the start");
            expect(start_cut["answers"] == mixed(kept + 50, tail, "truncate_start")["answers"], "truncate_start keeps the end");
            expect(end_cut["answers"] != start_cut["answers"], "the two cuts read different text");
            // Scan reads both of those windows: "any" is at least, "all" at most, either one.
            const double first = end_cut["answers"]["refund"]["noul"], last = start_cut["answers"]["refund"]["noul"];
            auto any = mixed(kept, tail, "scan")["answers"]["refund"];
            json every = {{"state", repeated("refund ", kept) + repeated("weather ", tail)}, {"overflow", "scan"},
                          {"questions", {{"refund", requests[0]["questions"]["refund"]}}}};
            every["questions"]["refund"]["aggregate"] = "all";
            auto all_windows = json::parse(small.execute(every.dump()))["answers"]["refund"];
            expect(any["noul"].get<double>() >= std::max(first, last) && all_windows["noul"].get<double>() <= std::min(first, last),
                   "scan any is the maximum and all the minimum over windows");
        }
        expect(c.rejected([&] { json r = json::parse(c.sized(words)); r["overflow"] = "scan"; r["max_windows"] = 1; return r.dump(); }(),
                          "max_windows", &small), "too many windows rejected");
        {   // The per-call ceiling counts every planned pass, scan or not, before inference.
            const std::string scan = [&] { json r = json::parse(c.sized(words)); r["overflow"] = "scan"; return r.dump(); }();
            auto ceiling = [&](size_t n) {
                return ducktape::engine::open(c.model, R"({"threads":2,"context_tokens":64,"max_windows_per_call":)" + std::to_string(n) + "}");
            };
            expect(small.info().find("\"max_windows_per_call\":256") != std::string::npos, "the default ceiling is reported");
            auto exact = ceiling(windows), below = ceiling(windows - 1);
            expect(json::parse(exact.execute(scan))["answers"]["refund"]["window"]["count"] == windows, "a call at the ceiling runs");
            expect(c.rejected(scan, "max_windows_per_call", &below), "a scan over the ceiling is rejected");
            json batch = json::array({json::parse(c.sized(1)), json::parse(c.sized(1)), json::parse(c.sized(1))});
            auto two = ceiling(2);
            expect(c.rejected(batch.dump(), "max_windows_per_call", &two), "single-window passes count toward the ceiling");
            batch.erase(batch.begin() + 2);
            expect(json::parse(two.execute(batch.dump())).size() == 2, "two passes fit a ceiling of two");
            auto router = ducktape::engine::open_router(json({{"models", {{"a", c.model}}}}).dump(),
                                                        R"({"threads":2,"context_tokens":64,"max_windows_per_call":2})");
            json routed = json::array({json::parse(c.sized(1)), json::parse(c.sized(1)), json::parse(c.sized(1))});
            for (auto &r : routed) r["model"] = "a";
            expect(c.rejected(routed.dump(), "max_windows_per_call", &router), "the router's shared ceiling bounds the call");
        }
        for (json bad : {json({{"overflow", "clip"}}), json({{"max_windows", 0}}), json({{"overflow", 1}})}) {
            json r = json::parse(c.sized(1));
            for (auto &[k, v] : bad.items()) r[k] = v;
            invalid([&] { small.execute(r.dump()); });
        }
        json wrong_aggregate = primitive("Please refund me.");
        wrong_aggregate["questions"]["department"]["aggregate"] = "all";
        invalid([&] { small.execute(wrong_aggregate.dump()); });
        // Head shortening as llama.cpp's decision server does, reported per question. The head
        // budget is the model's (max_head_tokens), and a head cut to it can be longer than 64
        // tokens, so this runs on the default 512-token engine; its prompts are short.
        json shortened = long_option; shortened["overflow"] = "truncate_end";
        auto cut = json::parse(engine.execute(shortened.dump()));
        expect(cut["usage"]["truncated_questions"] == json::array({"department"}) && cut["usage"]["state_tokens_dropped"] == 0,
               "long option shortened and reported");
        // The engine default applies unless a request names a policy.
        auto lenient = ducktape::engine::open(c.model, R"({"threads":2,"context_tokens":64,"overflow":"truncate_end"})");
        expect(json::parse(lenient.execute(c.sized(words))) == over("truncate_end", words), "engine default overflow");
        invalid([&] { json r = json::parse(c.sized(words)); r["overflow"] = "reject"; lenient.execute(r.dump()); });
        invalid([&] { ducktape::engine::open(c.model, R"({"overflow":"clip"})"); });
        // A question and options longer than the limit are rejected under every policy,
        // including with an empty state (it used to overrun the context and abort).
        json wide = {{"state", ""}, {"questions", {{"q", {{"type", "choice"}, {"instructions", "pick one"}, {"criteria", json::array()}}}}}};
        for (int i = 0; i < 255; ++i) wide["questions"]["q"]["criteria"].push_back("a" + std::to_string(i));
        for (const char *policy : {"reject", "truncate_end", "truncate_start", "scan"}) {
            wide["overflow"] = policy;
            expect(c.rejected(wide.dump(), "question and options need", &small), "head over the limit is rejected");
        }
        json short_limit = {{"state", ""}, {"max_tokens", 20},
                            {"questions", {{"q", {{"type", "noul"}, {"instructions", "Is this text urgent, and does it ask for a refund today?"}}}}}};
        expect(c.rejected(short_limit.dump(), "20-token limit", &small), "max_tokens bounds the question and options too");
    }
    c.checkpoint("overflow policies");
}
} // namespace features
