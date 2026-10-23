<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/ducktape-lockup-dark.svg">
    <img src="assets/ducktape-lockup-light.svg" alt="ducktape laya" width="440">
  </picture>
</p>

ducktape-laya runs [Laya](https://github.com/NandhaKishorM/laya) decision models inside your application. You pass a text (or a JSON value) and a few named questions; it answers each one with calibrated probabilities, on the CPU, in-process. It needs no server, network access or separate runtime (no Python for the C, Node, Rust, Go, Java or .NET bindings), and it does not generate text.

One native library does the work; thin bindings expose it to C/C++, Rust, Node.js and Electron, Python, Go, Java/Kotlin and .NET, plus an experimental browser build (WebAssembly, optional WebGPU).

**Terms used below.** The *state* is the text or JSON the questions are about. A question has one of three types: `choice` picks one of several labelled options, `noul` gives the probability that a statement is true (a yes/no question; the name comes from Laya), and `score` picks a level on an ordered scale. A *model* is a GGUF file (the llama.cpp format) that you download once; an *engine* is a model opened by the library.

## Status

- **Current release: 0.2.0** (2026-10-22), on [GitHub Releases](https://github.com/adrian729/ducktape-laya/releases/tag/v0.2.0), npm, crates.io, PyPI, NuGet, Maven Central and the Go module proxy. Every release file is attested ([verify a download](SECURITY.md#verify-a-download)). Its packages run on **Linux x86-64**; the browser package is experimental.
- **Platforms:** Linux x86-64 is tier 1 (passes every release check); Linux arm64 and Windows x64 are tier 2 (tested in CI with the English model) from the next release; macOS on Apple silicon builds from source. Details: [docs/PLATFORMS.md](docs/PLATFORMS.md).
- **Unreleased on main:** Linux arm64 and Windows x64 packages and the fixes listed in the [changelog](CHANGELOG.md#unreleased). Until the next release, they are available by [building from source](docs/BUILDING.md).

## Requirements

| Platform | CPU | System |
| --- | --- | --- |
| Linux x86-64 | AVX2, FMA, F16C, BMI2, SSE4.2 (Intel Haswell, AMD Excavator/Zen or newer) | glibc 2.38+ and libstdc++ with `GLIBCXX_3.4.29` (Ubuntu 24.04+); not musl/Alpine |
| Linux arm64 (next release) | Armv8.2-A with dot product and FP16 (Graviton2+, Ampere Altra, Raspberry Pi 5) | as Linux x86-64 |
| Windows x64 (next release) | as Linux x86-64 | Microsoft Visual C++ Redistributable |

On a CPU without the required features, opening an engine fails with status 7 and names the missing feature. Plan for about 0.6 GiB of memory per open engine with the English model (1.2 GiB with the multilingual one) and 449 MB of disk for the English model. Language runtimes, tested versions and macOS: [docs/PLATFORMS.md](docs/PLATFORMS.md).

## Install

| Language | Install | Notes |
| --- | --- | --- |
| Node.js, Electron | `npm install ducktape-laya` | Node 20+; prebuilt addon. [Package README](bindings/node/README.md) |
| Python | `pip install ducktape-laya` | Python 3.10+; the wheel bundles the library. [Package README](bindings/python/README.md) |
| Rust | `cargo add ducktape-laya` | Links the native library from the release archive. [Crate README](bindings/rust/ducktape-laya/README.md) |
| Go | `go get github.com/adrian729/ducktape-laya/bindings/go@v0.2.0` | cgo; links the native library from the release archive. [Package README](bindings/go/README.md) |
| Java, Kotlin | `io.github.adrian729:ducktape-laya:0.2.0` | Java 22+, run with `--enable-native-access`; the jar bundles the library. [Package README](bindings/java/README.md) |
| .NET | `dotnet add package DucktapeLaya` | .NET 10; the package bundles the library. [Package README](bindings/dotnet/README.md) |
| C, C++ | [`ducktape-laya-0.2.0-linux-x64.tar.gz`](https://github.com/adrian729/ducktape-laya/releases/tag/v0.2.0) | Headers, library, CMake and pkg-config files. [Linking guide](docs/PACKAGING.md) |
| Browser | `npm install ducktape-laya-browser` | Experimental, tested on Chromium only. [Browser guide](docs/BROWSER.md) |

## Get the model

The model is not inside any package. Download the pinned English model (449 MB) and check its hash:

```sh
curl -fL -o Laya-Q8_0.gguf https://huggingface.co/ggml-org/Laya-GGUF/resolve/22265007700297ba9e128297e82540cf28c5d7d4/Laya-Q8_0.gguf
echo 'c06528c5746d3bb8baa72a27938be95abbfd0b226f8471e8a9e365ed0bb066d2  Laya-Q8_0.gguf' | sha256sum --check
```

The multilingual (100+ languages) and typed-decisions models are built from the official checkpoints with one command; see [docs/MODELS.md](docs/MODELS.md).

## Quick start

The same request in three languages: one `noul` question about a short text.

Node.js (an ES module; `require('ducktape-laya')` works too):

```js
import { Laya } from 'ducktape-laya';

const engine = await Laya.open('/path/to/Laya-Q8_0.gguf');
try {
  const result = await engine.execute({
    state: 'Please refund the duplicate charge.',
    questions: { refund: { type: 'noul', instructions: 'Does the customer request a refund?' } },
  });
  console.log(result.answers.refund.noul); // probability that the answer is "true"
} finally {
  await engine.close(); // required: the worker keeps the process alive
}
```

Python:

```python
import ducktape_laya

with ducktape_laya.Engine.open("/path/to/Laya-Q8_0.gguf") as engine:
    result = engine.execute({
        "state": "Please refund the duplicate charge.",
        "questions": {"refund": {"type": "noul", "instructions": "Does the customer request a refund?"}},
    })
    print(result["answers"]["refund"]["noul"])
```

Rust, inside a function that returns a `Result` (results are JSON text):

```rust
use ducktape_laya::{Config, Engine};

let engine = Engine::open("/path/to/Laya-Q8_0.gguf", &Config::default())?;
let result_json = engine.execute(
    r#"{"state":"Please refund the duplicate charge.",
        "questions":{"refund":{"type":"noul","instructions":"Does the customer request a refund?"}}}"#,
)?;
```

Every answer also carries `answer_confidence` (the probability of the reported answer) and `confidence` (how decided the model is). C, C++, Go, Java, .NET and the browser take the same request JSON; see their package READMEs, the ready-made requests in [examples/](examples/README.md) and the [API guide](docs/API.md) for structured decisions, batches, long input and routing between models.

## Performance

Native Linux x86-64 build, Intel Core i7-1165G7 (4 cores), 4 threads, default 512-token context. Ranges span three benchmark runs.

| | English Q8_0 | Multilingual F16 | Typed decisions Q8_0 |
| --- | --- | --- | --- |
| Open the model | 0.11–0.14 s | 0.58–0.72 s | 0.10–0.14 s |
| Request with 3 short questions (median) | 1.5–1.8 s | 0.4–0.6 s | 1.5 s |
| One question near the 512-token limit (median) | 6.6–7.9 s | 2.0–2.2 s | 6.2–6.4 s |
| Memory of one open engine | 0.6 GiB | 1.2 GiB | 0.6 GiB |

Each question is one model pass, so time grows with the number of questions and the length of the text. Method, more measurements and how to reproduce them: [docs/PERFORMANCE.md](docs/PERFORMANCE.md).

## Accuracy

Each model is checked against results recorded with the original Laya implementation (29 answers per model): input token counts must match exactly and probabilities must stay within 0.05. In the 0.2.0 qualification run on Linux x86-64, the largest probability difference was 0.037 for the English model (mean 0.007), 0.003 for the multilingual model (mean 0.0004) and 0.046 for the typed-decisions model (mean 0.006). Other platforms: [docs/PLATFORMS.md](docs/PLATFORMS.md#numerical-results-across-platforms).

## Documentation

- [API guide](docs/API.md): requests, results, configuration, long input, routing, cancellation, errors.
- [Models](docs/MODELS.md), [performance](docs/PERFORMANCE.md), [platforms](docs/PLATFORMS.md).
- [Linking and deployment](docs/PACKAGING.md) (CMake, pkg-config, loader paths, Electron) and the [browser guide](docs/BROWSER.md).
- [Changelog](CHANGELOG.md), [compatibility and migration](docs/COMPATIBILITY.md), [roadmap](docs/ROADMAP.md).
- [Building from source](docs/BUILDING.md) and the [examples](examples/README.md).
- For maintainers: [architecture](docs/ARCHITECTURE.md), [qualification](docs/QUALIFICATION.md), [release checks](docs/RELEASE_CHECKS.md), [releasing](docs/RELEASING.md), [upgrades](docs/UPGRADES.md).

## Licence and security

The code is MIT. The packages also carry Apache-2.0 components, and the Laya models are Apache-2.0; see [third-party notices](THIRD_PARTY_NOTICES.md). Treat model files as trusted code and report vulnerabilities privately: [SECURITY.md](SECURITY.md). This project is independent of the upstream Laya authors.

## Alternatives

- [Official Laya / laya-ts](https://github.com/NandhaKishorM/laya/tree/main/laya-ts): richer TypeScript workflows and an ONNX browser path.
- [LibLayaX](https://github.com/DaragonTech/LibLayaX), [RLaya](https://github.com/DaragonTech/RLaya), [laya.cpp](https://github.com/lkarlslund/laya.cpp): native C, Rust and C++ runtimes.
- [laya-rust (Candle)](https://github.com/aovestdipaperino/laya-rust), [laya-rust (GGUF)](https://github.com/zerodegress/laya-rust), [receptron/laya](https://github.com/receptron/laya): Rust inference and another Node/ONNX integration.
