// Model-free decision rules: calibration buckets, probabilities, confidence and the
// documented tie rule (first option or level) for choices, scores and integer values.
#include "decision.hpp"
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

        // Equal scores: uniform probabilities, first option, zero confidence.
        auto tie = dtl::answer_from_scores("choice", options({"a", "b", "c"}), {0.5f, 0.5f, 0.5f}, 3);
        expect(tie["choice"] == "a" && near(tie["probabilities"]["b"].get<double>(), 1.0 / 3) &&
               tie["confidence"].get<double>() == 0, "three-way choice tie chooses the first option");
        auto later = dtl::answer_from_scores("choice", options({"a", "b", "c"}), {1, 3, 3}, 1);
        expect(later["choice"] == "b", "tie between later options chooses the first of them");
        expect(near(later["probabilities"]["a"].get<double>(), 1 / (1 + 2 * std::exp(2.0))) &&
               later["probabilities"]["b"] == later["probabilities"]["c"], "softmax probabilities");
        auto scaled = dtl::answer_from_scores("choice", options({"a", "b"}), {0, 2}, 2);
        expect(near(scaled["probabilities"]["b"].get<double>(), 1 / (1 + std::exp(-1.0))) &&
               near(scaled["confidence"].get<double>(), 2 * scaled["probabilities"]["b"].get<double>() - 1), "temperature and confidence");

        // Ordinal score: expectation over levels; distance is measured from the first most probable level.
        auto score = dtl::answer_from_scores("score", options({"0", "1", "2"}), {2, 0, 2}, 1);
        const double edge = 1 / (2 + std::exp(-2.0)), middle = std::exp(-2.0) * edge;
        expect(near(score["score"].get<double>(), middle + 2 * edge), "expected level");
        expect(near(score["confidence"].get<double>(), std::max(0.0, 1 - (middle + 2 * edge) / (2.0 / 3))), "score confidence from first tied level");
        expect(score["legend"]["2"] == "level 2" && !score.contains("choice"), "score legend");

        auto noul = dtl::answer_from_scores("noul", json::array(), {0, 0}, 1);
        expect(noul.size() == 2 && noul["noul"].get<double>() == 0.5, "noul tie is one half");

        // Structured integer values use the first most probable level.
        expect(dtl::ordinal_index(json{{"0", 0.4}, {"1", 0.4}, {"2", 0.2}}) == 0, "integer tie chooses the first level");
        expect(dtl::ordinal_index(json{{"0", 0.2}, {"1", 0.4}, {"2", 0.4}}) == 1, "integer tie chooses the first of the tied levels");
        expect(dtl::ordinal_index(json{{"0", 0.1}, {"1", 0.2}, {"2", 0.7}}) == 2, "integer maximum");
        std::cout << "decision rule tests passed\n";
        return 0;
    } catch (const std::exception &e) { std::cerr << e.what() << '\n'; return 1; }
}
