# Performance and accuracy

All numbers here were measured, and each says where it comes from. They describe one reference machine and, in a separate section, one second machine; they are not promises for other hardware, and nothing here was measured on Linux arm64, Windows, Apple silicon or in the browser.

## Method

- **Machine:** Intel Core i7-1165G7 (4 cores, 8 threads), Linux x86-64, the native CPU build (AVX2 profile), `threads` 4, default `context_tokens` 512.
- **Tool:** `tests/benchmark.py`, one fresh process per run, calling the C library directly. Each run opens and closes the model, then times each workload repeatedly (first call, then median and 95th percentile), two engines in concurrent threads, router loads, cancellation and deadlines, and samples memory, file descriptors and threads.
- **Source:** the three baseline runs per model frozen in [`release/budgets/`](../release/budgets) as the release performance budgets. Ranges below span those three runs. The English baseline was measured on a 0.1.0-era build (commit `baaf568`) and a 0.2.0 build still met its budget; the multilingual and typed-decisions baselines were measured on a 0.2.0 build (commit `347585b`).
- **Workloads:** the shared test fixtures in `tests/fixtures`: a request with 3 questions (`choice`, `noul`, `score`) about a short text (`primitive.json`), a structured request with 3 properties (`structured.json`), the largest fixture request, close to the 512-token limit (from `coverage.json`), and a batch of 3 requests with 5 questions in total (`batch.json`).

## Latency and memory

| | English Q8_0 | Multilingual F16 | Typed decisions Q8_0 |
| --- | --- | --- | --- |
| Open the model | 0.11–0.14 s | 0.58–0.72 s | 0.10–0.14 s |
| Close the engine | 0.05–0.08 s | 0.17–0.18 s | 0.03 s |
| 3-question request, first call | 1.3–1.7 s | 0.45–0.53 s | 1.4–1.5 s |
| 3-question request, median / 95th percentile | 1.5–1.8 s / 1.8–2.4 s | 0.41–0.57 s / 0.49–0.64 s | 1.5 s / 1.5–1.6 s |
| 3-property structured request, median | 1.5–1.6 s | 0.43–0.55 s | 1.4–1.5 s |
| Request near the 512-token limit, median | 6.6–7.9 s | 2.0–2.2 s | 6.2–6.4 s |
| Batch of 3 requests (5 questions), median | 3.7–4.3 s | 1.0–1.3 s | 3.1–3.3 s |
| Two engines in concurrent threads, 95th percentile per request | 4.3–5.8 s | 1.1–1.3 s | 3.3–3.5 s |
| Memory with one open engine (resident set) | 597 MiB | 1,243 MiB | 598 MiB |
| Peak memory of the benchmark process (includes the two-engine and router phases) | 945 MiB | 1,570 MiB | 946 MiB |
| Memory after closing every engine | 49 MiB | 132 MiB | 50 MiB |
| Cancellation: time from cancel to the call returning | 32–64 ms | 6–22 ms | 14–28 ms |
| Deadline: time past the deadline before the call returns | 29–73 ms | 3–9 ms | 11–44 ms |

What drives the cost:

- **One model pass per question.** Questions in a request run one after another, each over the state plus that question, so a request's time is roughly the sum of its questions' times. The state is tokenized once per request. A `choice` with a single option needs no pass.
- **Prompt length.** A pass over a prompt near 512 tokens takes several times as long as one over a short prompt. Contexts longer than 512 tokens cost more than proportionally; no longer-context measurements are recorded here.
- **Model.** The multilingual model is the smaller network (mmBERT-base, against ModernBERT-large) and the fastest per question, but its F16 weights take about twice the memory of the Q8_0 models.
- **Threads.** One call uses up to `threads` CPU threads (0 means automatic, at most 4). Calls on one engine run one at a time; open several engines for parallel work, each with its own memory.
- **Memory.** `context_tokens` sets how much memory an engine reserves; a router holds up to `max_loaded` models at once.

The memory rows above, and the second machine's below, were measured before an unused logits buffer was removed (runtime patch `llama-embeddings-no-logits-buffer.patch`). llama.cpp reserved `n_vocab` floats per prompt token that the decision graph never reads, about 98 MiB per 512 prompt tokens with the English and typed-decisions models and about 500 MiB with the multilingual one, so memory after a long request is now lower by up to that much: the audit measured 479 MiB less on the multilingual near-limit request on a busy desktop. The table has not been re-measured on an idle machine; the budgets in `release/budgets/` remain upper bounds.

Tokenizing very long input is linear in its length: on a busy 8-core x86-64 desktop, 1 MiB of state took about 4 s for ordinary text and up to about 11 s for the slowest input measured (long words without spaces). Those times were measured while every piece of text was still tokenized twice, once to count its tokens and once to store them, each pass doing the full work; it is now tokenized once. The times have not been re-measured since. Tokenization cannot be interrupted by a deadline ([API guide](API.md#cancellation-and-deadlines)).

## A second machine: Intel MacBook Pro

An independent run on different hardware, to show how the numbers move with the CPU. macOS on x86-64 is not a release target and is unsupported ([platforms](PLATFORMS.md)); it builds and the tests pass there, but nothing on this page is a support promise.

- **Machine:** MacBook Pro, Intel Core i9-9880H (8 cores, 16 threads, AVX2/FMA/F16C/BMI2), 32 GB, macOS 15.7.2, Apple clang 17.0.0, Release build with the native CPU profile (`native-cpu:avx+avx2+fma+f16c+bmi2+sse4.2`). ggml's Accelerate (Apple BLAS) option was on, Metal off.
- **Source:** `tests/benchmark.py` run on 2026-10-24 against main `8994a03` plus the macOS port of the benchmark (the same code as `10c39d1`): three runs per model and thread count, 20 warm samples per single-request workload, the workloads above. Cells show the median of the three runs with the minimum and maximum in parentheses. The raw reports are not stored in the repository.
- **Load:** the laptop was not isolated. macOS background indexing (Spotlight `mds_stores`, `mediaanalysisd`) used one to two cores throughout, and the load average read 3 to 8 at the start of a run (it includes the tail of the previous run; each run started 45 s after the last). Treat the numbers as a retained sample, as on the first machine.
- **Memory:** resident set from `ps` on macOS, not directly comparable with `VmRSS` on Linux.

| | English Q8_0, 4 threads | English Q8_0, 8 threads | Multilingual F16, 4 threads | Typed decisions Q8_0, 4 threads |
| --- | --- | --- | --- | --- |
| Open the model | 0.13 (0.12–0.17) s | 0.12 (0.11–0.16) s | 0.70 (0.69–0.72) s | 0.13 (0.12–0.17) s |
| Close the engine | 0.029 (0.028–0.030) s | 0.028 (0.025–0.041) s | 0.14 (0.13–0.15) s | 0.026 (0.025–0.028) s |
| 3-question request, first call | 0.92 (0.83–1.13) s | 0.99 (0.95–1.08) s | 0.48 (0.48–0.52) s | 0.94 (0.85–1.06) s |
| 3-question request, median | 0.92 (0.91–1.02) s | 1.03 (1.02–1.04) s | 0.40 (0.36–0.44) s | 0.93 (0.91–0.95) s |
| 3-question request, 95th percentile | 1.10 (0.98–1.12) s | 1.26 (1.06–1.33) s | 0.45 (0.42–0.51) s | 1.04 (1.02–1.07) s |
| 3-property structured request, median | 0.94 (0.91–1.07) s | 1.02 (1.01–1.02) s | 0.40 (0.35–0.41) s | 0.92 (0.90–0.92) s |
| Request near the 512-token limit, median | 1.85 (1.81–2.16) s | 1.67 (1.63–1.68) s | 0.79 (0.77–0.80) s | 1.86 (1.86–1.96) s |
| Batch of 3 requests (5 questions), median | 2.00 (1.79–2.03) s | 1.89 (1.85–1.90) s | 0.67 (0.66–0.72) s | 1.83 (1.74–1.89) s |
| Two engines in concurrent threads, 95th percentile per request | 2.11 (2.09–2.27) s | 2.90 (2.62–2.91) s | 0.75 (0.70–0.77) s | 2.20 (2.06–2.31) s |
| Memory with one open engine (resident set) | 658 (657–659) MiB | 658 (657–661) MiB | 1,300 (1,279–1,304) MiB | 658 (658–658) MiB |
| Peak memory of the benchmark process | 1,114 (1,099–1,137) MiB | 1,112 (1,091–1,120) MiB | 1,640 (1,639–1,655) MiB | 1,110 (1,093–1,126) MiB |
| Memory after closing every engine | 111 (110–112) MiB | 111 (110–114) MiB | 190 (168–194) MiB | 112 (111–112) MiB |
| Cancellation: time from cancel to the call returning | 20 (8–23) ms | 15 (9–17) ms | 4 (2–7) ms | 11 (9–17) ms |
| Deadline: time past the deadline before the call returns | 21 (5–26) ms | 15 (13–16) ms | 2 (2–5) ms | 10 (9–20) ms |

With 4 threads on both machines, the i9 is roughly 1.6 to 2 times faster on a 3-question request with the Q8_0 models (about 0.9 s against 1.5-1.8 s) and about 3.5 to 4 times faster near the 512-token limit (about 1.9 s against 6.2-7.9 s). The multilingual model is about as fast on short requests (0.40 s against 0.41-0.57 s) and 2.5 to 3 times faster near the limit. Eight threads on the 8-core i9 did not help short requests (1.03 s against 0.92 s) and made two concurrent engines slower (2.9 s against 2.1 s, they compete for the same cores); only the near-limit request gained (1.67 s against 1.85 s). The post-close memory is higher than on Linux (about 111 MiB against 49 MiB); it was not investigated further.

One benchmark gate is not enforced on macOS: the thread-leak check, because the libdispatch worker pool behind Accelerate grows and shrinks on its own (16 or more extra threads, varying by one or two between runs). The report records this under `skipped_checks`; the thread counts are still recorded and the descriptor-leak check still applies (none leaked).

## Package sizes

The 0.2.0 release files (from the [GitHub release](https://github.com/adrian729/ducktape-laya/releases/tag/v0.2.0), Linux x86-64 only):

| File | Size |
| --- | --- |
| Native archive `ducktape-laya-0.2.0-linux-x64.tar.gz` | 3.0 MB |
| npm `ducktape-laya-0.2.0.tgz` | 5.7 MB (15.6 MB unpacked) |
| npm `ducktape-laya-browser-0.2.0.tgz` | 3.2 MB (13.1 MB unpacked) |
| Python wheel, Maven jar, NuGet package | 2.9 MB each |
| Rust crates | 12 KB and 3 KB (source only; they link the native archive's library) |

Models are separate downloads ([models](MODELS.md)). The next release's npm package, jar and NuGet package carry the libraries of every release platform, so they are larger.

## Accuracy against the original Laya

Each model is run on the original Laya implementation's recorded cases (`tests/original-reference/`: 29 answers per model) at its trained prompt length. Input token counts must be identical and every probability within 0.05; the decision must be the same except on near ties (the original's two best options within 0.05). Measured in the 0.2.0 qualification run on Linux x86-64 (the release's `gates.json` records only that the gate passed):

| Model | Largest probability difference | Mean difference |
| --- | --- | --- |
| English Q8_0 | 0.037 | 0.0068 |
| Multilingual F16 | 0.0027 | 0.0004 |
| Typed decisions Q8_0 | 0.046 | 0.0059 |

The 16-bit multilingual model is much closer than the two 8-bit (Q8_0) models, so most of the remaining difference comes from quantization. Results on other CPUs and in the browser differ slightly again: [platforms](PLATFORMS.md#numerical-results-across-platforms).

## How to reproduce

Run on an otherwise idle machine; builds or other inference running at the same time skew every number.

```sh
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release && cmake --build build -j 4
python3 tests/benchmark.py /path/to/Laya-Q8_0.gguf --build build --report build/bench.json
python3 scripts/performance-budgets.py check build/bench.json
```

The benchmark also runs on macOS (it picks `libducktape_laya.dylib` and samples `ps`); `tests/benchmark.py --help` lists the options (thread count, samples per workload). For another model, pass its file to the benchmark and its budget to the check, for example `--budgets release/budgets/native-performance-laya-multilingual-f16.json`. How budgets are derived and enforced: [release checks](RELEASE_CHECKS.md#performance-budgets).
