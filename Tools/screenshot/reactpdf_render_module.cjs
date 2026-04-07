#!/usr/bin/env node
/* eslint-disable no-console */
const path = require('path');
const Module = require('module');
const fs = require('fs');

function parseArgs(argv) {
  const args = {};
  for (let i = 2; i < argv.length; i += 1) {
    const token = argv[i];
    if (!token.startsWith('--')) continue;
    const key = token.slice(2);
    const next = argv[i + 1];
    if (next && !next.startsWith('--')) {
      args[key] = next;
      i += 1;
    } else {
      args[key] = 'true';
    }
  }
  return args;
}

function die(message) {
  console.error(`ERROR: ${message}`);
  process.exit(1);
}

function installAssetLoaders() {
  require.extensions['.css'] = () => {};

  for (const ext of [
    '.png',
    '.jpg',
    '.jpeg',
    '.gif',
    '.svg',
    '.ttf',
    '.woff',
    '.woff2',
  ]) {
    require.extensions[ext] = (module, filename) => {
      module.exports = filename;
    };
  }
}

function fileExists(filePath) {
  try {
    return fs.existsSync(filePath);
  } catch (_err) {
    return false;
  }
}

function safeRequire(specifier) {
  try {
    return require(specifier);
  } catch (_err) {
    return null;
  }
}

function safeResolve(specifier, options) {
  try {
    return require.resolve(specifier, options);
  } catch (_err) {
    return null;
  }
}

function loadRepoPackageJson(repoDir) {
  const pkgPath = path.join(repoDir, 'package.json');
  if (!fileExists(pkgPath)) return {};
  return JSON.parse(fs.readFileSync(pkgPath, 'utf8'));
}

function createRendererAliases(repoDir) {
  const repoPkg = loadRepoPackageJson(repoDir);
  const aliases = {};
  const styledShimPath = path.join(
    __dirname,
    'reactpdf_styled_components.cjs',
  );

  if (repoPkg.name === '@react-pdf/renderer') {
    aliases['@react-pdf/renderer'] = fileExists(path.join(repoDir, repoPkg.main || ''))
      ? path.join(repoDir, repoPkg.main)
      : repoDir;
  }

  const styledWorkspaceDir = path.join(repoDir, 'packages', 'styled-components');
  if (!safeResolve('@react-pdf/styled-components', { paths: [repoDir] })) {
    if (fileExists(path.join(styledWorkspaceDir, 'package.json'))) {
      aliases['@react-pdf/styled-components'] = styledWorkspaceDir;
    } else {
      aliases['@react-pdf/styled-components'] = styledShimPath;
    }
  } else if (fileExists(path.join(styledWorkspaceDir, 'package.json'))) {
    aliases['@react-pdf/styled-components'] = styledWorkspaceDir;
  }

  return aliases;
}

function installModuleAliases(aliases) {
  const originalResolveFilename = Module._resolveFilename;
  Module._resolveFilename = function patchedResolveFilename(
    request,
    parent,
    isMain,
    options,
  ) {
    try {
      return originalResolveFilename.call(this, request, parent, isMain, options);
    } catch (err) {
      if (Object.prototype.hasOwnProperty.call(aliases, request)) {
        return originalResolveFilename.call(
          this,
          aliases[request],
          parent,
          isMain,
          options,
        );
      }
      throw err;
    }
  };
}

function registerTranspiler(repoDir) {
  const babelRegisterPath =
    safeResolve('@babel/register', { paths: [repoDir] }) ||
    safeResolve('babel-register', { paths: [repoDir] }) ||
    safeResolve('babel-core/register', { paths: [repoDir] });

  if (!babelRegisterPath) {
    die('missing Babel register dependency in repo');
  }

  const register = require(babelRegisterPath);
  const options = {
    extensions: ['.js', '.jsx'],
    ignore: [/node_modules/],
  };

  if (babelRegisterPath.includes('@babel/register')) {
    options.cwd = repoDir;
  }

  register(options);
}

function loadReact(repoDir) {
  const reactPath = safeResolve('react', { paths: [repoDir] });
  if (!reactPath) die('missing react dependency in repo');
  return require(reactPath);
}

function loadRenderer(repoDir) {
  const candidates = [
    safeResolve('@react-pdf/renderer', { paths: [repoDir] }),
    repoDir,
    fileExists(path.join(repoDir, 'dist', 'react-pdf.cjs.js'))
      ? path.join(repoDir, 'dist', 'react-pdf.cjs.js')
      : null,
    fileExists(path.join(repoDir, 'packages', 'renderer', 'lib', 'react-pdf.cjs.js'))
      ? path.join(repoDir, 'packages', 'renderer', 'lib', 'react-pdf.cjs.js')
      : null,
  ].filter(Boolean);

  for (const candidate of candidates) {
    const mod = safeRequire(candidate);
    if (mod) return mod;
  }

  die('unable to load react-pdf renderer from repo');
}

function resolveExport(mod) {
  if (!mod) return null;
  if (mod.default) return mod.default;
  if (typeof mod === 'function') return mod;

  for (const value of Object.values(mod)) {
    if (typeof value === 'function') return value;
  }

  return null;
}

async function main() {
  const args = parseArgs(process.argv);
  const repoDir = path.resolve(args['repo-dir'] || '');
  const modulePath = path.resolve(args['source-module'] || '');
  const pdfOut = path.resolve(args['pdf-out'] || '');

  if (!args['repo-dir']) die('missing --repo-dir');
  if (!args['source-module']) die('missing --source-module');
  if (!args['pdf-out']) die('missing --pdf-out');

  process.env.NODE_PATH = [
    path.join(repoDir, 'node_modules'),
    process.env.NODE_PATH || '',
  ]
    .filter(Boolean)
    .join(path.delimiter);
  Module._initPaths();

  installAssetLoaders();
  installModuleAliases(createRendererAliases(repoDir));

  const React = loadReact(repoDir);
  const ReactPDF = loadRenderer(repoDir);
  registerTranspiler(repoDir);
  const loaded = require(modulePath);
  const exported = resolveExport(loaded);

  if (!exported) {
    die(`no usable export found in ${modulePath}`);
  }

  const element = React.isValidElement(exported)
    ? exported
    : React.createElement(exported);

  if (typeof ReactPDF.renderToFile === 'function') {
    await ReactPDF.renderToFile(element, pdfOut);
  } else if (typeof ReactPDF.render === 'function') {
    await ReactPDF.render(element, pdfOut);
  } else {
    die('renderer does not expose renderToFile/render');
  }
  console.log(pdfOut);
}

main().catch((err) => {
  console.error(err && err.stack ? err.stack : String(err));
  process.exit(1);
});
