'use strict';
const fs = require('node:fs');
const path = require('node:path');
// native/<platform>-<arch>/ holds the addon and the shared library it links.
const PLATFORMS = ['linux-x64', 'linux-arm64', 'darwin-arm64', 'win32-x64'];
module.exports = function resolveAddon(override) {
  if (override || process.env.DUCKTAPE_LAYA_ADDON) return path.resolve(override || process.env.DUCKTAPE_LAYA_ADDON);
  const platform = `${process.platform}-${process.arch}`;
  if (!PLATFORMS.includes(platform)) {
    throw new Error(`Unsupported native platform: ${process.platform}/${process.arch}; this package supports Linux x64 and arm64, macOS arm64 and Windows x64`);
  }
  // Electron packagers must unpack the package's native directory. Resolve the
  // physical path before handing it to Node's worker/addon loader.
  const packaged = path.join(__dirname, 'native', platform, 'ducktape_laya.node').replace(/\.asar([/\\])/, '.asar.unpacked$1');
  if (fs.existsSync(packaged)) return packaged;
  throw new Error(`Native Laya addon for ${platform} is missing. Install a package built for this platform, or set addonPath / DUCKTAPE_LAYA_ADDON for a development build.`);
};
