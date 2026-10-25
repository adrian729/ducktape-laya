// Model-free check of the stress and soak late-growth rules (tests/stress_rule.hpp) and of the
// result-line writer, on synthetic closed-RSS series.
#include "stress_rule.hpp"
#include <cstdlib>
#include <iostream>
#include <string>
#include <vector>
namespace {
int failures = 0;
void expect(bool ok, const std::string &name) {
    std::cout << (ok ? "ok: " : "FAILED: ") << name << '\n';
    if (!ok) { std::cout.flush(); std::exit(1); }
}
// Deterministic noise in [-amplitude, amplitude] KiB (a linear congruential generator).
std::vector<long> flat(std::size_t n, long level, long amplitude, unsigned seed = 7) {
    std::vector<long> out(n);
    unsigned state = seed;
    for (auto &value : out) {
        state = state * 1103515245u + 12345u;
        value = level + long((state >> 16) % unsigned(2 * amplitude + 1)) - amplitude;
    }
    return out;
}
bool passes(const std::vector<long> &series) { return stress_rule::violation(stress_rule::evaluate(series)).empty(); }
}
int main() {
    const long base = 600 * 1024;  // about the closed RSS of a run with the Q8_0 model
    {
        const auto series = flat(3000, base, 300);
        const auto g = stress_rule::evaluate(series);
        expect(g.rule_32mib_applied && g.soak_rule_applied && passes(series), "flat series with noise passes (N=3000)");
    }
    {
        auto series = flat(1000, base, 300, 11);
        for (std::size_t i = series.size() / 2; i < series.size(); ++i) series[i] += 150;  // one 150 KiB allocator step
        expect(stress_rule::evaluate(series).soak_rule_applied && passes(series), "a 150 KiB step at mid-window passes (N=1000)");
        auto longer = flat(3000, base, 300, 13);
        for (std::size_t i = 1875; i < longer.size(); ++i) longer[i] += 150;  // the middle of [N/4, N)
        expect(passes(longer), "a 150 KiB step at the middle of [N/4, N) passes (N=3000)");
    }
    {
        // Windows-like warm-up: about 60 MiB gained over the first 110 rounds, then flat.
        auto series = flat(1200, base, 300, 17);
        for (std::size_t i = 0; i < series.size(); ++i) series[i] += long(60 * 1024 * std::min<std::size_t>(i, 110) / 110) - 60 * 1024;
        expect(passes(series), "a Windows-like warm-up in the first quarter passes (N=1200)");
    }
    {
        auto series = flat(3000, base, 300, 19);
        for (std::size_t i = 0; i < series.size(); ++i) series[i] += long(2 * i);  // 2 KiB/round
        const auto g = stress_rule::evaluate(series);
        const auto message = stress_rule::violation(g);
        expect(g.soak_rule_applied && !message.empty() && message.find("Soak rule") == 0,
               "2 KiB/round linear growth fails the soak rule (N=3000)");
        expect(*g.late_median_kib - *g.warm_median_kib <= stress_rule::rule_32mib_limit_kib,
               "the same growth stays within the 32 MiB rule, so only the soak rule catches it");
        expect(*g.slope_kib_per_round > 1.9 && *g.slope_kib_per_round < 2.1, "the slope estimate is about 2 KiB/round");
    }
    {
        auto series = flat(999, base, 300, 23);
        for (std::size_t i = 0; i < series.size(); ++i) series[i] += long(2 * i);
        const auto g = stress_rule::evaluate(series);
        expect(g.rule_32mib_applied && !g.soak_rule_applied && !g.slope_kib_per_round && !g.median_difference_kib && passes(series),
               "at N=999 the soak rule is not applied (its statistics are null), the 32 MiB rule is");
        const auto json = stress_rule::late_growth_json(g);
        expect(json.find("\"soak_rule\":{\"applied\":false,\"second_quarter_median_kib\":null") != std::string::npos,
               "late_growth JSON writes the soak rule's statistics as null when not applied");
    }
    {
        auto series = flat(400, base, 300, 29);
        for (std::size_t i = 300; i < series.size(); ++i) series[i] += 40 * 1024;  // 40 MiB late growth
        const auto g = stress_rule::evaluate(series);
        expect(g.rule_32mib_applied && stress_rule::violation(g) == "Sustained post-warmup closed RSS growth exceeded 32 MiB",
               "40 MiB late growth fails the 32 MiB rule (N=400)");
        expect(passes(std::vector<long>(series.begin(), series.begin() + 39)), "below 40 rounds no rule applies");
    }
    {
        // The writer on a failing series: one parseable line with the status, the failure and every value.
        stress_rule::result r;
        r.closed_rss_kib_by_round = flat(1500, base, 300, 31);
        for (std::size_t i = 0; i < r.closed_rss_kib_by_round.size(); ++i) r.closed_rss_kib_by_round[i] += long(3 * i);
        r.rounds = unsigned(r.closed_rss_kib_by_round.size());
        r.requested_seconds = 2700;
        r.min_rounds = 1000;
        r.baseline_rss_kib = base;
        r.memory_metric = "VmRSS";
        r.thread_metric = "threads (/proc/self/task)";
        r.failure = stress_rule::violation(stress_rule::evaluate(r.closed_rss_kib_by_round));
        expect(!r.failure.empty(), "the synthetic writer series fails a rule");
        r.failure += " \"quoted\"\n";  // the writer escapes the message
        const auto line = stress_rule::result_line(r);
        std::vector<long> back;
        expect(line.size() > 1 && line.back() == '\n' && line.find('\n') == line.size() - 1, "the result is one line");
        expect(stress_rule::read_series("progress\n" + line + "\n", back) && back == r.closed_rss_kib_by_round,
               "the series reads back whole from the line");
        expect(line.find("\"status\":\"failed\"") != std::string::npos && line.find("\"failure\":\"Soak rule") != std::string::npos &&
               line.find("\\\"quoted\\\"\\n\"") != std::string::npos, "status failed and the escaped failure message are written");
        expect(line.find("\"final_rss_kib\":null") != std::string::npos && line.find("\"fds\":null") != std::string::npos,
               "values not measured are null");
        expect(line.find("\"late_growth\":{\"rule_32mib\":{\"applied\":true") != std::string::npos &&
               line.find("\"soak_rule\":{\"applied\":true") != std::string::npos && line.find("\"min_rounds\":1000") != std::string::npos,
               "late_growth and min_rounds are written");
        r.failure.clear();
        expect(stress_rule::result_line(r).find("\"status\":\"passed\",\"failure\":null") != std::string::npos,
               "a passing result writes status passed and failure null");
    }
    {
        std::vector<long> back;
        expect(!stress_rule::read_series("{\"rounds\":3}\n", back) && !stress_rule::read_series("{\"closed_rss_kib_by_round\":[1,,2]}", back),
               "malformed or missing series are refused");
        expect(stress_rule::read_series("{\"closed_rss_kib_by_round\" : [ 1 , 2 ,3 ] }", back) && back == std::vector<long>{1, 2, 3},
               "whitespace around the series is accepted");
    }
    return failures;
}
