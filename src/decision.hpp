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
inline json answer_from_scores(const std::string &type, const json &options, const std::vector<float> &scores, float temperature) {
    size_t n = scores.size();
    float largest = *std::max_element(scores.begin(), scores.end());
    std::vector<double> p(n);
    double sum = 0;
    for (size_t i = 0; i < n; ++i) { p[i] = std::exp(double(scores[i] - largest) / temperature); sum += p[i]; }
    for (double &v : p) v /= sum;
    json answer = {{"type", type}};
    if (type == "noul") { answer["noul"] = p[1]; return answer; }
    json probabilities = json::object(), legend = json::object();
    size_t best = first_max(p);
    double expected = 0, distance = 0, uniform_distance = 0;
    for (size_t i = 0; i < n; ++i) {
        auto key = options[i]["key"].get<std::string>();
        probabilities[key] = p[i]; legend[key] = options[i]["description"];
        expected += i * p[i]; distance += p[i] * std::fabs(double(i) - double(best));
        uniform_distance += std::fabs(double(i) - (n - 1) / 2.0) / n;
    }
    if (type == "choice") {
        answer["choice"] = options[best]["key"];
        answer["probabilities"] = probabilities;
        answer["confidence"] = std::max(0.0, (p[best] - 1.0/n) / (1.0 - 1.0/n));
    } else {
        answer["score"] = expected; answer["legend"] = legend;
        answer["probabilities"] = probabilities;
        answer["confidence"] = std::max(0.0, 1.0 - distance / uniform_distance);
    }
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
