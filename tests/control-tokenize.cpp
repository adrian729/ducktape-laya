// Deadlines and cancellation during tokenization (tokenizer-stop-check.patch) of plain words and of
// text that special tokens split into short pieces, and a cancellation callback that calls into the library.
// Usage: laya-control-tokenize-tests MODEL [MULTILINGUAL_MODEL...]
//
// The English and typed-decisions vocabularies make runs of 2 to 24 spaces special tokens, and
// the multilingual one every newline, so double-spaced English and newline-separated lines are
// tokenized as many short pieces. Tokenization counts its work across the whole call, so such
// text is checked as often as one long piece (when it counted per piece, double-spaced long
// words and newline-separated lines ran to the end). Times are the calling thread's CPU time, which
// load on the machine does not inflate: the work happens on that thread, before inference.
#include "ducktape_laya.hpp"
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <functional>
#include <iostream>
#include <stdexcept>
#include <string>
#include <thread>
#include "thread-cpu-time.hpp"

namespace {
using dtl_test::thread_cpu_seconds;
void expect(bool ok, const std::string &message) { if (!ok) throw std::runtime_error(message); }

const std::string question = R"("questions":{"refund":{"type":"noul","instructions":"Is a refund requested?"}})";
const std::string small = R"({"state":"Please refund me.",)" + question + "}";

// Text made of pieces: `next(seed)` returns the next piece, already JSON-escaped.
using pieces = std::function<std::string(uint32_t &)>;
// A request whose JSON text is just under 1 MiB: the state is `first` and then pieces. The
// state is longer than the context, so under "reject" the call ends with status 1 right after
// tokenizing it, before any inference.
std::string large_request(const std::string &first, const pieces &next) {
    std::string state = first;
    const size_t room = 1048576 - 64 - question.size() - first.size();
    uint32_t seed = 12345;
    for (std::string piece = next(seed); state.size() + piece.size() <= room; piece = next(seed)) state += piece;
    return R"({"state":")" + state + "\"," + question + "}";
}
pieces repeated(const std::string &unit) { return [unit](uint32_t &) { return unit; }; }

dtl_status status_of(const std::function<void()> &call) {
    try { call(); return DTL_OK; } catch (const ducktape::error &e) { return e.status; }
}

// Full tokenization time (the faster of two calls with different states, so neither reuses the
// other's cached tokens), then a deadline and a cancellation at a tenth of it: each must end the
// call with its own status within half of it, and the engine must stay exact afterwards.
void stops_promptly(ducktape::engine &engine, const std::string &baseline, const std::string &label, const pieces &unit) {
    auto timed = [&](const std::string &body, const ducktape::call_options &options, double &seconds) {
        const double started = thread_cpu_seconds();
        const auto status = status_of([&] { engine.execute(body, options); });
        seconds = thread_cpu_seconds() - started;
        return status;
    };
    double full = 0, again = 0, stopped = 0;
    expect(timed(large_request("Full.", unit), {}, full) == DTL_INVALID_ARGUMENT, label + ": a 1 MiB state fits the context");
    expect(timed(large_request("Again.", unit), {}, again) == DTL_INVALID_ARGUMENT, label + ": a 1 MiB state fits the context");
    full = std::min(full, again);
    const auto tenth = std::max<uint64_t>(1, static_cast<uint64_t>(full * 100));  // milliseconds
    ducktape::call_options deadline;
    deadline.timeout_ms = tenth;
    const auto deadline_status = timed(large_request("Deadline.", unit), deadline, stopped);
    std::cerr << label << ": 1 MiB tokenization " << full << " s CPU; a " << tenth << " ms deadline ended it after " << stopped << " s CPU\n";
    expect(deadline_status == DTL_DEADLINE_EXCEEDED, label + ": deadline returned status " + std::to_string(deadline_status));
    expect(stopped * 2 < full, label + ": the deadline did not stop tokenization promptly");
    std::atomic<bool> stop{false};
    ducktape::call_options cancellable;
    cancellable.should_cancel = [](void *p) -> uint32_t { return static_cast<std::atomic<bool> *>(p)->load() ? 1 : 0; };
    cancellable.cancel_user_data = &stop;
    std::thread canceller([&] { std::this_thread::sleep_for(std::chrono::milliseconds(tenth)); stop = true; });
    const auto cancel_status = timed(large_request("Cancel.", unit), cancellable, stopped);
    canceller.join();
    std::cerr << label << ": cancelled after " << tenth << " ms, returned after " << stopped << " s CPU\n";
    expect(cancel_status == DTL_CANCELLED, label + ": cancellation returned status " + std::to_string(cancel_status));
    expect(stopped * 2 < full, label + ": the cancellation did not stop tokenization promptly");
    expect(engine.execute(small) == baseline, label + ": result changed after a stopped tokenization");
}

// A cancellation callback that calls another engine while this call tokenizes (the inner call
// installs and removes its own stop check) must leave this call's check in place: the next poll
// cancels the call, which would otherwise tokenize to the end and fail the context check (1).
void reentrant_callback(ducktape::engine &engine, ducktape::engine &other, const pieces &unit) {
    struct state {
        ducktape::engine *other;
        uint64_t polls = 0, reenter_at = 0;
        bool reentered = false;
        dtl_status inner = DTL_OK;
    } s{&other};
    ducktape::call_options options;
    options.cancel_user_data = &s;
    options.should_cancel = [](void *p) -> uint32_t {
        auto &x = *static_cast<state *>(p);
        ++x.polls;
        if (!x.reenter_at) return 0;  // counting
        if (x.polls < x.reenter_at) return 0;
        if (x.polls == x.reenter_at) {
            ducktape::call_options inner;
            inner.should_cancel = [](void *) -> uint32_t { return 0; };
            x.inner = status_of([&] { x.other->execute(small, inner); });
            x.reentered = true;
            return 0;
        }
        return 1;
    };
    const auto counted = status_of([&] { engine.execute(large_request("Counting.", unit), options); });
    expect(counted == DTL_INVALID_ARGUMENT, "uncancelled reentrant call returned status " + std::to_string(counted));
    expect(s.polls > 100, "too few polls during tokenization: " + std::to_string(s.polls));
    // Late in the polls, so within the tokenization of the state, the call's last work.
    s.reenter_at = s.polls - s.polls / 8;
    s.polls = 0;
    const auto status = status_of([&] { engine.execute(large_request("Reentrant.", unit), options); });
    expect(s.reentered && s.inner == DTL_OK, "the callback did not call the other engine");
    expect(status == DTL_CANCELLED, "after a reentrant callback the call returned status " + std::to_string(status) +
           "; its stop check was lost");
    std::cerr << "reentrant callback at poll " << s.reenter_at << ": cancelled at the next poll\n";
}
}

int main(int argc, char **argv) {
    try {
        expect(argc >= 2, "usage: laya-control-tokenize-tests MODEL [MULTILINGUAL_MODEL...]");
        // English words separated by two spaces: every separator is a special token.
        const auto double_spaced = repeated("The  customer  asked  for  a  refund  because  the  parcel  arrived  late.  ");
        // Pseudo-random 160-letter words separated by two spaces: each piece is one long word,
        // whose merging is most of the work, while the split at the separators is quick.
        const pieces long_words = [](uint32_t &seed) {
            std::string word = "  ";
            for (int i = 0; i < 160; ++i) { seed = seed * 1103515245u + 12345u; word += char('a' + (seed >> 16) % 26); }
            return word;
        };
        // Pseudo-random 160-letter words separated by single spaces: plain words with no special
        // token, so the text is one piece split only by the regex pre-split (the shape real-control
        // checked before; long words make merging, which is checked, most of the work).
        const pieces plain_words = [](uint32_t &seed) {
            std::string word = " ";
            for (int i = 0; i < 160; ++i) { seed = seed * 1103515245u + 12345u; word += char('a' + (seed >> 16) % 26); }
            return word;
        };
        // Lines in several languages and scripts separated by newlines (JSON escapes).
        const std::string lines = "The customer asked for a refund.\\nEl cliente pidi\xc3\xb3 un reembolso.\\n"
                                  "Der Kunde bat um eine R\xc3\xbc" "ckerstattung.\\n\xd0\x9a\xd0\xbb\xd0\xb8\xd0\xb5\xd0\xbd\xd1\x82 "
                                  "\xd0\xbf\xd0\xbe\xd0\xbf\xd1\x80\xd0\xbe\xd1\x81\xd0\xb8\xd0\xbb \xd0\xb2\xd0\xbe\xd0\xb7\xd0\xb2\xd1\x80\xd0\xb0\xd1\x82.\\n"
                                  "\xe9\xa1\xbe\xe5\xae\xa2\xe8\xa6\x81\xe6\xb1\x82\xe9\x80\x80\xe6\xac\xbe\xe3\x80\x82\\n";
        {
            auto engine = ducktape::engine::open(argv[1], R"({"threads":2})");
            const auto baseline = engine.execute(small);
            stops_promptly(engine, baseline, "double-spaced English", double_spaced);
            stops_promptly(engine, baseline, "double-spaced long words", long_words);
            stops_promptly(engine, baseline, "single-spaced long words", plain_words);
            auto other = ducktape::engine::open(argv[1], R"({"threads":2})");
            reentrant_callback(engine, other, long_words);
            expect(engine.execute(small) == baseline, "result changed after the reentrant callback");
        }
        for (int i = 2; i < argc; ++i) {
            auto engine = ducktape::engine::open(argv[i], R"({"threads":2})");
            stops_promptly(engine, engine.execute(small), std::string("newline-separated lines, ") + argv[i], repeated(lines));
        }
        std::cout << "tokenization control tests passed\n";
        return 0;
    } catch (const std::exception &e) { std::cerr << e.what() << '\n'; return 1; }
}
