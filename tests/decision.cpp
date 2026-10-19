// Model-free decision rules: calibration buckets, probabilities, confidence and the
// documented tie rule (first option or level) for choices, scores and integer values.
#include "decision.hpp"
#include "python_json.hpp"
#include <iostream>
#include <stdexcept>
using dtl::json;
static void expect(bool ok, const char *message) { if (!ok) throw std::runtime_error(message); }
static bool near(double a, double b) { return std::fabs(a - b) < 1e-12; }
static json options(std::initializer_list<const char *> keys) {
    json result = json::array();
    for (auto key : keys) result.push_back({{"key", key}, {"description", std::string("level ") + key}});
    return result;
}
int main() {
    try {
        // Structured values are serialized exactly as Python's json.dumps(v, ensure_ascii=False).
        const std::pair<double, const char *> floats[] = {{149.0, "149.0"}, {3.14159265358979, "3.14159265358979"},
            {1e21, "1e+21"}, {1e-07, "1e-07"}, {-0.125, "-0.125"}, {1.0 / 3, "0.3333333333333333"}, {-0.0, "-0.0"},
            {0.0, "0.0"}, {1e16, "1e+16"}, {9999999999999998.0, "9999999999999998.0"}, {0.0001, "0.0001"},
            {0.00001234, "1.234e-05"}, {12345678.9, "12345678.9"}, {2.5e-300, "2.5e-300"},
            {1.7976931348623157e308, "1.7976931348623157e+308"}, {5e-324, "5e-324"}, {100.0, "100.0"},
            {123456789012345678.0, "1.2345678901234568e+17"}, {0.1 + 0.2, "0.30000000000000004"}};
        for (auto [value, text] : floats) expect(dtl::python_float(value) == text, text);
        auto mixed = json::parse(R"({"s":"Tom \"x\" / \\ \n\t\b\f\r\u0001\u007f\u2028 na\u00efve","n":null,"t":true,)"
                                 R"("f":false,"l":[1,2.0,{}],"e":{},"i":-17,"u":18446744073709551615})");
        expect(dtl::python_json(mixed) == "{\"s\": \"Tom \\\"x\\\" / \\\\ \\n\\t\\b\\f\\r\\u0001\x7f\u2028 na\u00efve\", "
               "\"n\": null, \"t\": true, \"f\": false, \"l\": [1, 2.0, {}], \"e\": {}, \"i\": -17, "
               "\"u\": 18446744073709551615}", "python_json object, escaping and separators");
        const std::map<std::string, float> temperatures{{"choice.2", 2}, {"choice.3_5", 3}, {"choice.6_10", 6},
            {"choice.11", 11}, {"score", 4}, {"score.3_5", 5}, {"noul.2", 7}};
        expect(dtl::temperature_for("choice", 2, temperatures) == 2 && dtl::temperature_for("choice", 3, temperatures) == 3 &&
               dtl::temperature_for("choice", 5, temperatures) == 3 && dtl::temperature_for("choice", 6, temperatures) == 6 &&
               dtl::temperature_for("choice", 10, temperatures) == 6 && dtl::temperature_for("choice", 11, temperatures) == 11 &&
               dtl::temperature_for("choice", 255, temperatures) == 11, "choice calibration buckets");
        expect(dtl::temperature_for("score", 4, temperatures) == 5 && dtl::temperature_for("score", 2, temperatures) == 4 &&
               dtl::temperature_for("score", 7, temperatures) == 4, "score bucket and type default");
        expect(dtl::temperature_for("noul", 2, temperatures) == 7 && dtl::temperature_for("other", 2, temperatures) == 1,
               "noul bucket and uncalibrated default");

        // Equal scores: uniform probabilities, first option, zero concentration.
        auto tie = dtl::answer_from_scores("choice", options({"a", "b", "c"}), {0.5f, 0.5f, 0.5f}, 3);
        expect(tie["choice"] == "a" && near(tie["probabilities"]["b"].get<double>(), 1.0 / 3) &&
               near(tie["confidence"].get<double>(), 0) && near(tie["answer_confidence"].get<double>(), 1.0 / 3),
               "three-way choice tie chooses the first option");
        auto later = dtl::answer_from_scores("choice", options({"a", "b", "c"}), {1, 3, 3}, 1);
        expect(later["choice"] == "b", "tie between later options chooses the first of them");
        expect(near(later["probabilities"]["a"].get<double>(), 1 / (1 + 2 * std::exp(2.0))) &&
               later["probabilities"]["b"] == later["probabilities"]["c"], "softmax probabilities");
        auto scaled = dtl::answer_from_scores("choice", options({"a", "b"}), {0, 2}, 2);
        const double pb = 1 / (1 + std::exp(-1.0));
        const double entropy = -(pb * std::log(pb) + (1 - pb) * std::log(1 - pb));
        expect(near(scaled["probabilities"]["b"].get<double>(), pb) && near(scaled["answer_confidence"].get<double>(), pb) &&
               near(scaled["confidence"].get<double>(), 1 - entropy / std::log(2.0)), "temperature, answer_confidence and concentration");

        // Ordinal score: expectation over levels; distance is measured from the first most probable level.
        auto score = dtl::answer_from_scores("score", options({"0", "1", "2"}), {2, 0, 2}, 1);
        const double edge = 1 / (2 + std::exp(-2.0)), middle = std::exp(-2.0) * edge;
        expect(near(score["score"].get<double>(), middle + 2 * edge), "expected level");
        const double score_entropy = -(2 * edge * std::log(edge) + middle * std::log(middle));
        expect(near(score["confidence"].get<double>(), 1 - score_entropy / std::log(3.0)) &&
               near(score["answer_confidence"].get<double>(), edge), "score concentration and answer_confidence");
        expect(score["legend"]["2"] == "level 2" && !score.contains("choice"), "score legend");

        auto noul = dtl::answer_from_scores("noul", json::array(), {0, 0}, 1);
        expect(noul.size() == 4 && noul["noul"].get<double>() == 0.5 && near(noul["confidence"].get<double>(), 0) &&
               noul["answer_confidence"].get<double>() == 0.5, "noul tie is one half");
        auto single = dtl::single_option_answer(options({"only"}));
        expect(single["choice"] == "only" && single["probabilities"]["only"] == 1.0 && single["confidence"] == 1.0 &&
               single["answer_confidence"] == 1.0, "a single option is the answer with certainty");

        // Structured integer values use the first most probable level.
        expect(dtl::ordinal_index(json{{"0", 0.4}, {"1", 0.4}, {"2", 0.2}}) == 0, "integer tie chooses the first level");
        expect(dtl::ordinal_index(json{{"0", 0.2}, {"1", 0.4}, {"2", 0.4}}) == 1, "integer tie chooses the first of the tied levels");
        expect(dtl::ordinal_index(json{{"0", 0.1}, {"1", 0.2}, {"2", 0.7}}) == 2, "integer maximum");
        std::cout << "decision rule tests passed\n";
        return 0;
    } catch (const std::exception &e) { std::cerr << e.what() << '\n'; return 1; }
}
