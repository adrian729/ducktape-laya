#!/bin/sh
# Run inside an Alpine (musl) container. The packages bundle glibc builds: the
# multi-platform npm package and jar must report that the library cannot load on
# musl (status 7, a message naming glibc and musl) instead of a loader error, and
# npm must refuse the Linux-only npm package, which declares "libc": ["glibc"].
#
#   musl-packages.sh node ASSEMBLED_DIR LINUX_FRAGMENT_DIR   (node:*-alpine image)
#   musl-packages.sh java ASSEMBLED_DIR                      (eclipse-temurin:*-jdk-alpine image)
set -eu
kind=$1
assembled=$2
ls /lib/ld-musl-* >/dev/null 2>&1 || { echo "not a musl system (no /lib/ld-musl-*)" >&2; exit 1; }
version=$(sed -n 's/^ *"package_version": *"\([^"]*\)".*/\1/p' "$assembled/inventory.json" | head -n 1)
[ -n "$version" ] || { echo "no package_version in $assembled/inventory.json" >&2; exit 1; }
work=$(mktemp -d)
cd "$work"
case $kind in
node)
  fragment=$3
  mkdir app linux
  echo '{"private":true}' > app/package.json
  echo '{"private":true}' > linux/package.json
  (cd app && npm install --offline --ignore-scripts --no-audit --no-fund "$assembled/ducktape-laya-$version.tgz" >/dev/null)
  (cd app && node -e '
    const { Engine } = require("ducktape-laya");
    const expected = e => e && e.status === 7 && e.code === "ERR_LAYA_UNSUPPORTED" && /needs glibc, and this looks like a musl system/.test(e.message);
    let error;
    try { Engine.runtimeInfo(); } catch (e) { error = e; }
    if (!expected(error)) { console.error("runtimeInfo:", error); process.exit(1); }
    Engine.open("model.gguf").then(() => { console.error("open succeeded"); process.exit(1); }, e => {
      if (!expected(e)) { console.error("open:", e); process.exit(1); }
      console.log("node: " + e.message);
    });')
  if (cd linux && npm install --offline --ignore-scripts --no-audit --no-fund "$fragment/ducktape-laya-$version.tgz" > npm.log 2>&1); then
    echo "npm installed the glibc-only package on musl" >&2
    exit 1
  fi
  grep -q EBADPLATFORM linux/npm.log || { cat linux/npm.log >&2; exit 1; }
  echo "npm: refused the linux-x64 package (EBADPLATFORM)"
  ;;
java)
  cat > Probe.java <<'EOF'
public class Probe {
    public static void main(String[] args) {
        try {
            io.github.adrian729.laya.Engine.runtimeInfo();
            System.err.println("the bundled library loaded on musl");
            System.exit(1);
        } catch (io.github.adrian729.laya.LayaException e) {
            if (e.status() != io.github.adrian729.laya.Status.UNSUPPORTED_PLATFORM || !e.getMessage().contains("needs glibc, and this looks like a musl system")) {
                e.printStackTrace();
                System.exit(1);
            }
            System.out.println("java: " + e.getMessage());
        }
    }
}
EOF
  java --enable-native-access=ALL-UNNAMED -cp "$assembled/ducktape-laya-$version.jar" Probe.java
  ;;
*)
  echo "usage: musl-packages.sh node|java ASSEMBLED_DIR [LINUX_FRAGMENT_DIR]" >&2
  exit 2
  ;;
esac
