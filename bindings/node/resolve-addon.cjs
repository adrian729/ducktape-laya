'use strict';
const fs = require('node:fs');
const path = require('node:path');
// native/<platform>-<arch>/ holds the addon and the shared library it links.
const PLATFORMS = ['linux-x64', 'linux-arm64', 'darwin-arm64', 'win32-x64'];
const unsupported = message => Object.assign(new Error(message), { status: 7 });
// A musl dynamic loader is present and no glibc one is, in the directories where the loaders live
// (/lib and /lib64; tests pass their own).
function isMusl(dirs = ['/lib', '/lib64']) {
  let musl = false, glibc = false;
  for (const dir of dirs) {
    let names = [];
    try { names = fs.readdirSync(dir); } catch {}
    for (const name of names) { musl ||= name.startsWith('ld-musl-'); glibc ||= name.startsWith('ld-linux-'); }
  }
  return musl && !glibc;
}
// host describes the running system; tests substitute parts of it to simulate another one.
function resolveAddon(override, { platform: os = process.platform, arch = process.arch, env = process.env, musl = isMusl } = {}) {
  if (override || env.DUCKTAPE_LAYA_ADDON) return path.resolve(override || env.DUCKTAPE_LAYA_ADDON);
  const platform = `${os}-${arch}`;
  if (!PLATFORMS.includes(platform)) {
    throw unsupported(`Unsupported native platform: ${os}/${arch}; this package supports Linux x64 and arm64, macOS arm64 and Windows x64`);
  }
  if (os === 'linux' && musl()) {
    throw unsupported(`The native Laya addon for ${platform} needs glibc, and this looks like a musl system (for example Alpine). Use a glibc-based image, or set addonPath / DUCKTAPE_LAYA_ADDON to a musl build.`);
  }
  // Electron packagers must unpack the package's native directory. Resolve the
  // physical path before handing it to Node's worker/addon loader.
  const packaged = path.join(__dirname, 'native', platform, 'ducktape_laya.node').replace(/\.asar([/\\])/, '.asar.unpacked$1');
  if (fs.existsSync(packaged)) return packaged;
  throw unsupported(`Native Laya addon for ${platform} is missing. Install a package built for this platform, or set addonPath / DUCKTAPE_LAYA_ADDON for a development build.`);
}
module.exports = resolveAddon;
module.exports.isMusl = isMusl;
