#pragma once
// Model-independent decision arithmetic: calibration bucket selection, calibrated
// probabilities and answer/value projection. Shared by the backend and the
// model-free decision-rule tests. Ties choose the first maximum everywhere.
#include "backend.hpp"
#include <algorithm>
#include <cmath>
#include <map>
namespace dtl {
// Index of the first maximum, so ties choose the first option or level.
template<class T> size_t first_max(const std::vector<T> &values) {
    return std::max_element(values.begin(), values.end()) - values.begin();
}
inline float temperature_for(const std::string &type, size_t n, const std::map<std::string, float> &temperatures) {
    std::string bucket = n <= 2 ? "2" : n <= 5 ? "3_5" : n <= 10 ? "6_10" : "11";
    auto it = temperatures.find(type + "." + bucket);
    if (it == temperatures.end()) it = temperatures.find(type);
    return it == temperatures.end() ? 1.0f : it->second;
}
// How concentrated the distribution is: 1 - H(p)/log k, in 0..1 (1 for one option).
inline double concentration(const std::vector<double> &p) {
    if (p.size() < 2) return 1.0;
    double entropy = 0;
    for (double v : p) if (v > 0) entropy -= v * std::log(v);
    return std::clamp(1.0 - entropy / std::log(double(p.size())), 0.0, 1.0);
}
// A choice with one option has a known answer; the model is not run for it.
inline json single_option_answer(const json &options) {
    const auto &key = options[0]["key"];
    return {{"type", "choice"}, {"choice", key}, {"probabilities", {{key.get<std::string>(), 1.0}}},
            {"confidence", 1.0}, {"answer_confidence", 1.0}};
}
// `answer_confidence` is max(p), the calibrated probability of the reported answer;
// `confidence` is the concentration of the whole distribution. Both mean the same
// for every question type.
inline json answer_from_scores(const std::string &type, const json &options, const std::vector<float> &scores, float temperature) {
    size_t n = scores.size();
    float largest = *std::max_element(scores.begin(), scores.end());
    std::vector<double> p(n);
    double sum = 0;
    for (size_t i = 0; i < n; ++i) { p[i] = std::exp(double(scores[i] - largest) / temperature); sum += p[i]; }
    for (double &v : p) v /= sum;
    json answer = {{"type", type}};
    size_t best = first_max(p);
    if (type == "noul") {
        answer["noul"] = p[1];
    } else {
        json probabilities = json::object(), legend = json::object();
        double expected = 0;
        for (size_t i = 0; i < n; ++i) {
            auto key = options[i]["key"].get<std::string>();
            probabilities[key] = p[i]; legend[key] = options[i]["description"];
            expected += i * p[i];
        }
        if (type == "choice") answer["choice"] = options[best]["key"];
        else { answer["score"] = expected; answer["legend"] = legend; }
        answer["probabilities"] = probabilities;
    }
    answer["confidence"] = concentration(p);
    answer["answer_confidence"] = p[best];
    return answer;
}
// Structured integer value: the first most probable ordinal level.
inline size_t ordinal_index(const json &probabilities) {
    size_t best = 0, index = 0;
    double probability = -1;
    for (auto &v : probabilities) {
        if (v.get<double>() > probability) { probability = v.get<double>(); best = index; }
        ++index;
    }
    return best;
}
} // namespace dtl
