#pragma once
// Late-growth rules of the stress and soak runs (tests/stress.cpp) as pure functions over the
// closed-RSS series (KiB, one value per completed round, in order), the result-line writer, and
// the reader `laya-stress --check-series` uses. Header-only and model-free, so the model-free
// test tests/stress-rule.cpp checks the same code the stress program runs.
#include <algorithm>
#include <cctype>
#include <cstddef>
#include <cstdlib>
#include <optional>
#include <sstream>
#include <string>
#include <vector>
namespace stress_rule {
// From 40 rounds (every run): the last quarter's median closed RSS must not exceed the second
// quarter's by more than 32 MiB; the first quarter is warm-up.
inline constexpr std::size_t rule_32mib_rounds = 40;
inline constexpr long rule_32mib_limit_kib = 32 * 1024;
// From 1,000 rounds (the soak rule): the first quarter is skipped; median(last quarter) -
// median(second quarter) <= 2 MiB, and the least-squares slope of closed RSS over rounds
// [N/4, N) <= 1 KiB/round.
inline constexpr std::size_t soak_rule_rounds = 1000;
inline constexpr long soak_limit_median_difference_kib = 2048;
inline constexpr double soak_limit_slope_kib_per_round = 1.0;

// The upper median of series[begin, end) (the element at size/2 after sorting), as the stress
// program has always computed it.
inline long median(const std::vector<long> &series, std::size_t begin, std::size_t end) {
    std::vector<long> part(series.begin() + begin, series.begin() + end);
    std::nth_element(part.begin(), part.begin() + part.size() / 2, part.end());
    return part[part.size() / 2];
}
// Least-squares slope of series[i] against i over [begin, end), in KiB per round.
inline double slope(const std::vector<long> &series, std::size_t begin, std::size_t end) {
    const double n = double(end - begin);
    double mean_x = 0, mean_y = 0;
    for (std::size_t i = begin; i < end; ++i) { mean_x += double(i); mean_y += double(series[i]); }
    mean_x /= n; mean_y /= n;
    double covariance = 0, variance = 0;
    for (std::size_t i = begin; i < end; ++i) {
        const double dx = double(i) - mean_x;
        covariance += dx * (double(series[i]) - mean_y);
        variance += dx * dx;
    }
    return variance > 0 ? covariance / variance : 0.0;
}

// Both rules' statistics. Quarters use one index arithmetic: quarter = N / 4, second quarter
// [quarter, 2 * quarter), last quarter [N - quarter, N). A rule's statistics are null (empty)
// while it does not apply: the 32 MiB rule's medians exist from the first full quarter (N >= 4)
// and are informational below 40 rounds; the soak rule's statistics exist only from 1,000 rounds.
struct late_growth {
    std::size_t rounds = 0;
    bool rule_32mib_applied = false;
    std::optional<long> warm_median_kib, late_median_kib;
    bool soak_rule_applied = false;
    std::optional<long> second_quarter_median_kib, last_quarter_median_kib, median_difference_kib;
    std::optional<double> slope_kib_per_round;
};
inline late_growth evaluate(const std::vector<long> &series) {
    late_growth out;
    const std::size_t n = series.size(), quarter = n / 4;
    out.rounds = n;
    if (quarter) {
        out.warm_median_kib = median(series, quarter, 2 * quarter);
        out.late_median_kib = median(series, n - quarter, n);
    }
    out.rule_32mib_applied = n >= rule_32mib_rounds;
    out.soak_rule_applied = n >= soak_rule_rounds;
    if (out.soak_rule_applied) {
        out.second_quarter_median_kib = out.warm_median_kib;
        out.last_quarter_median_kib = out.late_median_kib;
        out.median_difference_kib = *out.late_median_kib - *out.warm_median_kib;
        out.slope_kib_per_round = slope(series, quarter, n);
    }
    return out;
}
// The first rule the series breaks, as a message; empty when both applicable rules hold.
inline std::string violation(const late_growth &g) {
    if (g.rule_32mib_applied && *g.late_median_kib > *g.warm_median_kib + rule_32mib_limit_kib)
        return "Sustained post-warmup closed RSS growth exceeded 32 MiB";
    if (g.soak_rule_applied) {
        if (*g.median_difference_kib > soak_limit_median_difference_kib)
            return "Soak rule: the last quarter's median closed RSS exceeds the second quarter's by " +
                   std::to_string(*g.median_difference_kib) + " KiB (limit 2048 KiB)";
        if (*g.slope_kib_per_round > soak_limit_slope_kib_per_round) {
            std::ostringstream text;
            text << "Soak rule: closed RSS grows " << *g.slope_kib_per_round
                 << " KiB/round over rounds [N/4, N) (limit 1 KiB/round)";
            return text.str();
        }
    }
    return {};
}

inline std::string json_string(const std::string &text) {
    std::string out = "\"";
    for (unsigned char c : text) {
        if (c == '"') out += "\\\"";
        else if (c == '\\') out += "\\\\";
        else if (c == '\n') out += "\\n";
        else if (c == '\r') out += "\\r";
        else if (c == '\t') out += "\\t";
        else if (c < 0x20) { static const char hex[] = "0123456789abcdef"; out += "\\u00"; out += hex[c >> 4]; out += hex[c & 15]; }
        else out += char(c);
    }
    return out + "\"";
}
template <class T> std::string json_value(const std::optional<T> &value) {
    if (!value) return "null";
    std::ostringstream text;
    text << *value;
    return text.str();
}
inline std::string late_growth_json(const late_growth &g) {
    std::ostringstream out;
    out << "{\"rule_32mib\":{\"applied\":" << (g.rule_32mib_applied ? "true" : "false")
        << ",\"warm_median_kib\":" << json_value(g.warm_median_kib)
        << ",\"late_median_kib\":" << json_value(g.late_median_kib)
        << "},\"soak_rule\":{\"applied\":" << (g.soak_rule_applied ? "true" : "false")
        << ",\"second_quarter_median_kib\":" << json_value(g.second_quarter_median_kib)
        << ",\"last_quarter_median_kib\":" << json_value(g.last_quarter_median_kib)
        << ",\"median_difference_kib\":" << json_value(g.median_difference_kib)
        << ",\"slope_kib_per_round\":" << json_value(g.slope_kib_per_round)
        << ",\"limit_median_difference_kib\":" << soak_limit_median_difference_kib
        << ",\"limit_slope_kib_per_round\":" << soak_limit_slope_kib_per_round << "}}";
    return out.str();
}

// Everything the stress program's result line reports. Empty optionals are values not
// measured yet (a run that failed before them); they are written as null.
struct result {
    unsigned rounds = 0;
    int requested_seconds = 0;
    int min_rounds = 0;
    std::optional<long> max_closed_rss_kib, baseline_rss_kib, warm_median_closed_rss_kib, late_median_closed_rss_kib;
    std::optional<long> final_rss_kib, peak_rss_kib, fds, threads;
    double elapsed_seconds = 0;
    std::string memory_metric, thread_metric;
    unsigned settle_waits = 0;
    std::string failure;  // empty: passed
    std::vector<long> closed_rss_kib_by_round;
};
// One JSON line: today's fields in today's order, then min_rounds, status, failure (null when
// passed), late_growth (computed from the series) and the series itself.
inline std::string result_line(const result &r) {
    std::ostringstream out;
    out << "{\"rounds\":" << r.rounds << ",\"requested_seconds\":" << r.requested_seconds
        << ",\"inference_threads\":2,\"max_closed_rss_kib\":" << json_value(r.max_closed_rss_kib)
        << ",\"concurrent_calls\":12,\"baseline_rss_kib\":" << json_value(r.baseline_rss_kib)
        << ",\"warm_median_closed_rss_kib\":" << json_value(r.warm_median_closed_rss_kib)
        << ",\"late_median_closed_rss_kib\":" << json_value(r.late_median_closed_rss_kib)
        << ",\"final_rss_kib\":" << json_value(r.final_rss_kib) << ",\"peak_rss_kib\":" << json_value(r.peak_rss_kib)
        << ",\"fds\":" << json_value(r.fds) << ",\"threads\":" << json_value(r.threads)
        << ",\"elapsed_seconds\":" << r.elapsed_seconds
        << ",\"memory_metric\":" << json_string(r.memory_metric) << ",\"thread_metric\":" << json_string(r.thread_metric)
        << ",\"settle_waits\":" << r.settle_waits << ",\"min_rounds\":" << r.min_rounds
        << ",\"status\":\"" << (r.failure.empty() ? "passed" : "failed") << "\",\"failure\":"
        << (r.failure.empty() ? std::string("null") : json_string(r.failure))
        << ",\"late_growth\":" << late_growth_json(evaluate(r.closed_rss_kib_by_round))
        << ",\"closed_rss_kib_by_round\":[";
    for (std::size_t i = 0; i < r.closed_rss_kib_by_round.size(); ++i) out << (i ? "," : "") << r.closed_rss_kib_by_round[i];
    out << "]}\n";
    return out.str();
}

// Reads the integer array "closed_rss_kib_by_round" from one line of JSON text. The key is
// unique in a result line (no nested object repeats it); whitespace is allowed around tokens.
inline bool read_series_line(const std::string &line, std::vector<long> &series) {
    const std::string key = "\"closed_rss_kib_by_round\"";
    const std::size_t at = line.find(key);
    if (at == std::string::npos) return false;
    std::size_t i = at + key.size();
    const auto skip = [&] { while (i < line.size() && std::isspace(static_cast<unsigned char>(line[i]))) ++i; };
    skip();
    if (i >= line.size() || line[i] != ':') return false;
    ++i; skip();
    if (i >= line.size() || line[i] != '[') return false;
    ++i; skip();
    std::vector<long> out;
    if (i < line.size() && line[i] == ']') { series = out; return true; }
    for (;;) {
        skip();
        const std::size_t start = i;
        if (i < line.size() && line[i] == '-') ++i;
        while (i < line.size() && std::isdigit(static_cast<unsigned char>(line[i]))) ++i;
        if (i == start || (line[start] == '-' && i == start + 1) || i - start > 18) return false;
        out.push_back(std::strtol(line.c_str() + start, nullptr, 10));
        skip();
        if (i >= line.size()) return false;
        if (line[i] == ']') break;
        if (line[i] != ',') return false;
        ++i;
    }
    series = std::move(out);
    return true;
}
// The series of a run's output (`laya-stress ... > result.json`): the last line that starts
// with '{' (after whitespace) and carries a well-formed closed_rss_kib_by_round array. Progress
// lines go to stderr, so a captured stdout normally holds only the result line.
inline bool read_series(const std::string &text, std::vector<long> &series) {
    std::vector<std::string> lines;
    std::istringstream input(text);
    for (std::string line; std::getline(input, line);) lines.push_back(line);
    for (auto it = lines.rbegin(); it != lines.rend(); ++it) {
        const std::size_t first = it->find_first_not_of(" \t\r");
        if (first == std::string::npos || (*it)[first] != '{') continue;
        if (read_series_line(*it, series)) return true;
    }
    return false;
}
} // namespace stress_rule
