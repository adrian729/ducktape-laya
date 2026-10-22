"""CycloneDX inventory for components incorporated in consumer artifacts."""
import json
from release_support import ROOT, read_json, sha256

def document(record, browser=False):
    def component(name, version, license_expression, notices):
        return {'type': 'library', 'bom-ref': name, 'name': name, 'version': version,
            'licenses': [{'expression': license_expression}],
            'properties': [{'name': 'ducktape:notice:' + path, 'value': sha256(ROOT / path)} for path in notices]}
    patches = [{'name': 'ducktape:patch:' + patch['path'], 'value': patch['sha256']} for patch in record['runtime']['patches']]
    components = [component('llama.cpp-ggml', record['runtime']['commit'], 'MIT', ['licenses/llama.cpp-MIT.txt']),
        component('nlohmann-json', '3.12.0', 'MIT', ['licenses/nlohmann-json-MIT.txt']),
        component('llamafile-cpu-kernels', record['runtime']['commit'], 'MIT', ['licenses/llamafile-MIT.txt'])]
    # Recorded patches modify build copies of llama.cpp/ggml and its vendored nlohmann header.
    for item in components: item['properties'] += patches
    # Word lists and thresholds in src/language.cpp come from the original Laya (laya/lang.py).
    components.append(component('laya-language-data', 'ffc142012af63d015fa19f05f29f9c5ff329a2cc', 'Apache-2.0',
                                ['licenses/Apache-2.0.txt']))
    if browser:
        sdk = read_json(ROOT / 'release/browser-toolchain.json')
        components += [component('emscripten-runtime', sdk['sdk_version'], 'MIT OR NCSA', ['licenses/emscripten.txt', 'licenses/emscripten-authors.txt']),
            component('musl', 'bundled-with-emscripten-' + sdk['sdk_version'], 'MIT', ['licenses/musl-COPYRIGHT.txt']),
            component('llvm-cxx-runtime', 'bundled-with-emscripten-' + sdk['sdk_version'], 'Apache-2.0 WITH LLVM-exception',
                ['licenses/libcxx-LICENSE.txt', 'licenses/libcxxabi-LICENSE.txt', 'licenses/compiler-rt-LICENSE.txt']),
            component('dawn-webgpu-bindings', sdk['webgpu_port']['version'], 'BSD-3-Clause', ['licenses/dawn-webgpu-LICENSE.txt'])]
    return {'bomFormat': 'CycloneDX', 'specVersion': '1.6', 'version': 1,
        'metadata': {'component': {'type': 'library', 'bom-ref': 'ducktape-laya', 'name': 'ducktape-laya', 'version': record['package_version']},
            'properties': [{'name': 'ducktape:scope', 'value': 'Incorporated runtime components; excludes model weights, host browser/GPU drivers, system libraries and test-only tools. Native system symbol requirements are in inventory.json.'}]},
        'components': components,
        'dependencies': [{'ref': 'ducktape-laya', 'dependsOn': [component['bom-ref'] for component in components]}]}

def write(path, record, browser=False):
    path.write_text(json.dumps(document(record, browser), indent=2) + '\n', newline='\n')
