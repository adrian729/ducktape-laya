#pragma once
// Automatic thread count (threads 0): at most 4, and no more than the CPUs this process
// may run on. ggml's workers spin at barriers, so more threads than usable CPUs is slower,
// not faster. The OS readers are in platform.cpp; the parsing here is tested model-free.
#include <algorithm>
#include <climits>
#include <cstdint>
#include <initializer_list>
#include <string>
namespace dtl {
namespace threads_detail {
inline bool parse_count(const std::string &text, unsigned long long &value) {
    if (text.empty() || text.size() > 19) return false;
    value = 0;
    for (char c : text) {
        if (c < '0' || c > '9') return false;
        value = value * 10 + static_cast<unsigned>(c - '0');
    }
    return true;
}
}
// A cgroup v2 cpu.max line ("<quota> <period>", or "max <period>" for no limit): the CPUs the
// quota allows, rounded up; 0 when there is no limit or the text cannot be read.
inline unsigned cgroup_quota_cpus(const std::string &cpu_max) {
    const size_t space = cpu_max.find(' ');
    if (space == std::string::npos) return 0;
    std::string period_text = cpu_max.substr(space + 1);
    while (!period_text.empty() && (period_text.back() == '\n' || period_text.back() == '\r' || period_text.back() == ' '))
        period_text.pop_back();
    unsigned long long quota = 0, period = 0;
    if (!threads_detail::parse_count(cpu_max.substr(0, space), quota) ||
        !threads_detail::parse_count(period_text, period) || !quota || !period) return 0;
    const unsigned long long cpus = quota / period + (quota % period != 0);
    return static_cast<unsigned>(std::min<unsigned long long>(cpus, UINT_MAX));
}
// The process's cgroup v2 path from /proc/self/cgroup (the "0::<path>" line), or empty.
inline std::string cgroup_v2_path(const std::string &proc_self_cgroup) {
    size_t at = 0;
    while (at < proc_self_cgroup.size()) {
        size_t end = proc_self_cgroup.find('\n', at);
        if (end == std::string::npos) end = proc_self_cgroup.size();
        if (proc_self_cgroup.compare(at, 3, "0::") == 0 && end > at + 3 && proc_self_cgroup[at + 3] == '/')
            return proc_self_cgroup.substr(at + 3, end - at - 3);
        at = end + 1;
    }
    return {};
}
// min(4, hardware threads, each known limit), at least 1; an affinity or quota of 0 is
// unknown, and an unknown hardware thread count (0) gives 1 as before.
inline unsigned automatic_threads(unsigned affinity_cpus, unsigned quota_cpus, unsigned hardware_threads) {
    unsigned n = std::min(4u, hardware_threads);
    for (unsigned limit : {affinity_cpus, quota_cpus})
        if (limit) n = std::min(n, limit);
    return std::max(1u, n);
}
// The automatic thread count for this process (platform.cpp).
unsigned default_threads();
}
