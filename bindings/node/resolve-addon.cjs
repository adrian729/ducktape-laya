'use strict';
const fs = require('node:fs');
const path = require('node:path');
module.exports = function resolveAddon(override) {
  if (override || process.env.DUCKTAPE_LAYA_ADDON) return path.resolve(override || process.env.DUCKTAPE_LAYA_ADDON);
  if (process.platform !== 'linux' || process.arch !== 'x64') {
    throw new Error(`Unsupported native platform: ${process.platform}/${process.arch}; this package currently ships Linux x64 only`);
  }
  // Electron packagers must unpack the package's native directory. Resolve the
  // physical path before handing it to Node's worker/addon loader.
  const packaged = path.join(__dirname, 'native', 'linux-x64', 'ducktape_laya.node').replace(/\.asar([/\\])/, '.asar.unpacked$1');
  if (fs.existsSync(packaged)) return packaged;
  throw new Error('Native Laya addon is missing. Install a built package, or set addonPath / DUCKTAPE_LAYA_ADDON for a development build.');
};
