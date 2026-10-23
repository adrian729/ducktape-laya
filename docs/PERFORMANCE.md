# Performance and accuracy

All numbers here were measured, and each says where it comes from. They describe one reference machine; they are not promises for other hardware, and nothing here was measured on Linux arm64, Windows, macOS or in the browser.

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

Tokenizing very long input is linear in its length: on a busy 8-core x86-64 desktop, 1 MiB of state took about 4 s for ordinary text and up to about 11 s for the slowest input measured (long words without spaces). Tokenization cannot be interrupted by a deadline ([API guide](API.md#cancellation-and-deadlines)).

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

`tests/benchmark.py --help` lists the options (thread count, samples per workload). For another model, pass its file to the benchmark and its budget to the check, for example `--budgets release/budgets/native-performance-laya-multilingual-f16.json`. How budgets are derived and enforced: [release checks](RELEASE_CHECKS.md#performance-budgets).
