#!/usr/bin/env bash
# Plain-JDK build: compiles main and tests, writes target/ducktape-laya.jar, then
# runs the tests unless --no-test is given. Needs JDK 22+ on PATH.
set -euo pipefail
cd "$(dirname "$0")"
export DUCKTAPE_LAYA_LIB_DIR="${DUCKTAPE_LAYA_LIB_DIR:-$(cd ../.. && pwd)/build}"
lint=-Xlint:all,-restricted,-serial,-missing-explicit-ctor,-module
rm -rf target
mkdir -p target/classes target/test-classes
javac --release 22 "$lint" -d target/classes $(find src/main -name '*.java')
jar --create --file target/ducktape-laya.jar -C target/classes .
javac --release 22 "$lint" -cp target/ducktape-laya.jar -d target/test-classes $(find src/test -name '*.java')
[ "${1:-}" = --no-test ] && exit 0
java --enable-native-access=ALL-UNNAMED -cp target/ducktape-laya.jar:target/test-classes io.github.adrian729.laya.EngineTest
