/* eslint-disable no-console */
const fs = require('fs');
const path = require('path');
const http = require('http');
const url = require('url');
const { spawnSync } = require('child_process');
const os = require('os');

function parseArgs(argv) {
  const args = {};
  for (let i = 2; i < argv.length; i++) {
    const a = argv[i];
    if (!a.startsWith('--')) continue;
    const key = a.slice(2);
    const next = argv[i + 1];
    if (next && !next.startsWith('--')) {
      args[key] = next;
      i++;
    } else {
      args[key] = 'true';
    }
  }
  return args;
}

function die(msg) {
  console.error(`ERROR: ${msg}`);
  process.exit(1);
}

function ensureDir(p) {
  fs.mkdirSync(p, { recursive: true });
}

function normalizeMode(modeRaw) {
  const mode = String(modeRaw || '').trim().toLowerCase();
  if (!mode || mode === 'auto') return 'auto';
  if (mode === 'playwright' || mode === 'pw') return 'playwright';
  if (mode === 'chromium' || mode === 'chrome' || mode === 'edge') return 'chromium';
  if (mode === 'browsergui' || mode === 'gui' || mode === 'window') return 'chromium';
  return 'auto';
}

function contentTypeFor(p) {
  const ext = path.extname(p).toLowerCase();
  if (ext === '.js' || ext === '.mjs' || ext === '.cjs') return 'text/javascript; charset=utf-8';
  if (ext === '.css') return 'text/css; charset=utf-8';
  if (ext === '.svg') return 'image/svg+xml';
  if (ext === '.png') return 'image/png';
  if (ext === '.jpg' || ext === '.jpeg') return 'image/jpeg';
  if (ext === '.woff') return 'font/woff';
  if (ext === '.woff2') return 'font/woff2';
  if (ext === '.ttf') return 'font/ttf';
  if (ext === '.html') return 'text/html; charset=utf-8';
  if (ext === '.json') return 'application/json; charset=utf-8';
  if (ext === '.map') return 'application/json; charset=utf-8';
  return 'application/octet-stream';
}

function safeJoin(rootDir, reqPath) {
  const rel = reqPath.replace(/^[\\/]+/, '');
  const full = path.resolve(rootDir, rel);
  const root = path.resolve(rootDir);
  if (full === root) return full;
  if (!full.startsWith(root + path.sep)) return null;
  return full;
}

function staticFile(res, filePath, contentType) {
  try {
    const st = fs.statSync(filePath);
    if (!st.isFile()) {
      res.writeHead(404);
      res.end('not found');
      return false;
    }
    res.writeHead(200, { 'Content-Type': contentType });
    fs.createReadStream(filePath).pipe(res);
    return true;
  } catch (err) {
    res.writeHead(404);
    res.end('not found');
    return false;
  }
}

function serveFromRoots(res, reqPath, roots) {
  for (const root of roots) {
    const fullPath = safeJoin(root, reqPath);
    if (!fullPath) continue;
    try {
      if (fs.existsSync(fullPath) && fs.statSync(fullPath).isFile()) {
        return staticFile(res, fullPath, contentTypeFor(fullPath));
      }
    } catch (err) {}
  }
  res.writeHead(404);
  res.end('not found');
  return false;
}

function commandExists(command) {
  const resolver = process.platform === 'win32' ? 'where' : 'which';
  const res = spawnSync(resolver, [command], { stdio: 'ignore', shell: false });
  return res.status === 0;
}

function findBrowserExecutable(explicitPath) {
  if (explicitPath) {
    const p = path.resolve(explicitPath);
    if (fs.existsSync(p)) return p;
  }

  const envCandidates = [
    process.env.GUIREPAIR_CHROME,
    process.env.CHROME_PATH,
    process.env.PUPPETEER_EXECUTABLE_PATH
  ].filter(Boolean);

  for (const p of envCandidates) {
    try {
      const abs = path.resolve(p);
      if (fs.existsSync(abs)) return abs;
      if (commandExists(p)) return p;
    } catch (err) {}
  }

  const absoluteCandidates = process.platform === 'darwin'
    ? [
        '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
        '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge'
      ]
    : [];

  for (const p of absoluteCandidates) {
    if (fs.existsSync(p)) return p;
  }

  const pathCandidates = ['chromium', 'chromium-browser', 'google-chrome', 'google-chrome-stable', 'chrome', 'msedge'];
  for (const candidate of pathCandidates) {
    if (commandExists(candidate)) return candidate;
  }

  return 'chromium';
}

function tryChromiumScreenshot(browserExe, pageUrl, outPath, viewport, virtualTimeBudget = 2500) {
  const [vw, vh] = viewport;
  const tmpBase = fs.existsSync(path.dirname(outPath)) ? path.dirname(outPath) : os.tmpdir();
  const userDataDir = fs.mkdtempSync(path.join(tmpBase, 'visual-lab-profile-'));

  const args = [
    '--headless',
    '--disable-gpu',
    '--hide-scrollbars',
    '--no-sandbox',
    '--disable-dev-shm-usage',
    '--no-first-run',
    '--no-default-browser-check',
    '--disable-extensions',
    '--disable-crash-reporter',
    '--remote-debugging-port=0',
    `--user-data-dir=${userDataDir}`,
    `--window-size=${vw},${vh}`,
    `--virtual-time-budget=${virtualTimeBudget}`,
    `--screenshot=${outPath}`,
    pageUrl
  ];

  const res = spawnSync(browserExe, args, { stdio: 'inherit', shell: false });
  try {
    fs.rmSync(userDataDir, { recursive: true, force: true });
  } catch (err) {}

  if (res.status !== 0) {
    return { ok: false, reason: `chromium failed (rc=${res.status})` };
  }
  return { ok: true };
}

async function tryPlaywrightScreenshot(pageUrl, outPath, viewport, options = {}) {
  let playwright = null;
  try {
    playwright = require('playwright');
  } catch (err) {
    return { ok: false, reason: 'playwright not installed' };
  }
  if (!playwright || !playwright.chromium) {
    return { ok: false, reason: 'playwright chromium not available' };
  }

  const [vw, vh] = viewport;
  const waitForSelector = options.waitForSelector || '';
  const waitForFunction = options.waitForFunction || '';
  const waitAfterLoadMs = Number.isFinite(options.waitAfterLoadMs) ? options.waitAfterLoadMs : 250;
  let browser = null;

  try {
    browser = await playwright.chromium.launch({
      headless: true,
      args: ['--no-sandbox', '--disable-dev-shm-usage']
    });

    const page = await browser.newPage({ viewport: { width: vw, height: vh } });
    await page.goto(pageUrl, { waitUntil: 'load', timeout: 120000 });

    if (waitForSelector) {
      try {
        await page.waitForSelector(waitForSelector, { timeout: 5000 });
      } catch (err) {}
    }

    if (waitForFunction) {
      try {
        await page.waitForFunction(waitForFunction, { timeout: 5000 });
      } catch (err) {}
    }

    if (waitAfterLoadMs > 0) {
      await page.waitForTimeout(waitAfterLoadMs);
    }

    await page.screenshot({ path: outPath });
  } catch (err) {
    return {
      ok: false,
      reason: `playwright failed: ${err && err.message ? err.message : String(err)}`
    };
  } finally {
    if (browser) {
      try {
        await browser.close();
      } catch (err) {}
    }
  }

  return { ok: true };
}

function resolveHtmlEntry(repoDir, htmlArg, fallbackHtmlPath = '', fallbackPagePath = '/code.html') {
  if (htmlArg) {
    if (path.isAbsolute(htmlArg)) {
      return {
        htmlPath: path.resolve(htmlArg),
        pagePath: `/${path.basename(htmlArg)}`
      };
    }
    const rel = htmlArg.replace(/^[\\/]+/, '');
    return {
      htmlPath: path.join(repoDir, rel),
      pagePath: `/${rel.replace(/\\/g, '/')}`
    };
  }

  if (fallbackHtmlPath) {
    return {
      htmlPath: path.resolve(fallbackHtmlPath),
      pagePath: fallbackPagePath
    };
  }

  for (const defaultName of ['code.html', 'index.html']) {
    const candidate = path.join(repoDir, defaultName);
    if (fs.existsSync(candidate)) {
      return {
        htmlPath: candidate,
        pagePath: `/${defaultName}`
      };
    }
  }

  return null;
}

async function captureStaticPage(options) {
  const repoDir = options.repoDir ? path.resolve(options.repoDir) : '';
  const outPath = options.outPath ? path.resolve(options.outPath) : '';
  const browserPath = findBrowserExecutable(options.browserPath || '');
  const mode = normalizeMode(options.mode || '');
  const htmlEntry = resolveHtmlEntry(
    repoDir,
    options.htmlArg || '',
    options.defaultHtmlPath || '',
    options.defaultPagePath || '/code.html'
  );
  const viewport = options.viewport || [2400, 1600];
  const logPrefix = options.logPrefix || 'visual-lab';
  const waitForSelector = options.waitForSelector || '';
  const waitForFunction = options.waitForFunction || '';
  const waitAfterLoadMs = Number.isFinite(options.waitAfterLoadMs) ? options.waitAfterLoadMs : 250;
  const virtualTimeBudget = Number.isFinite(options.virtualTimeBudget) ? options.virtualTimeBudget : 2500;

  if (!repoDir) die('missing repoDir');
  if (!outPath) die('missing outPath');
  if (!fs.existsSync(repoDir)) die(`repo does not exist: ${repoDir}`);
  if (!htmlEntry || !fs.existsSync(htmlEntry.htmlPath)) {
    die(`html entry not found. repo=${repoDir} html=${options.htmlArg || options.defaultHtmlPath || '(none)'}`);
  }

  ensureDir(path.dirname(outPath));

  const staticRoots = Array.from(
    new Set([repoDir, path.dirname(htmlEntry.htmlPath)].filter(Boolean).map((p) => path.resolve(p)))
  );

  const server = http.createServer((req, res) => {
    const parsed = url.parse(req.url || '');
    const pathnameRaw = decodeURIComponent(parsed.pathname || '/');
    const pathname = pathnameRaw === '/' ? htmlEntry.pagePath : pathnameRaw;

    if (pathname === htmlEntry.pagePath) {
      staticFile(res, htmlEntry.htmlPath, contentTypeFor(htmlEntry.htmlPath));
      return;
    }

    serveFromRoots(res, pathname, staticRoots);
  });

  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const { port } = server.address();
  const pageUrl = `http://127.0.0.1:${port}${htmlEntry.pagePath}`;

  try {
    let finalMode = mode;
    if (finalMode === 'auto') finalMode = 'playwright';

    if (finalMode === 'playwright') {
      const pw = await tryPlaywrightScreenshot(pageUrl, outPath, viewport, {
        waitForSelector,
        waitForFunction,
        waitAfterLoadMs
      });
      if (!pw.ok) {
        const chrom = tryChromiumScreenshot(browserPath, pageUrl, outPath, viewport, virtualTimeBudget);
        if (!chrom.ok) {
          die(`screenshot failed: playwright=${pw.reason}; chromium=${chrom.reason}`);
        }
      }
    } else {
      const chrom = tryChromiumScreenshot(browserPath, pageUrl, outPath, viewport, virtualTimeBudget);
      if (!chrom.ok) {
        const pw = await tryPlaywrightScreenshot(pageUrl, outPath, viewport, {
          waitForSelector,
          waitForFunction,
          waitAfterLoadMs
        });
        if (!pw.ok) {
          die(`screenshot failed: chromium=${chrom.reason}; playwright=${pw.reason}`);
        }
      }
    }

    if (!fs.existsSync(outPath) || fs.statSync(outPath).size <= 0) {
      die(`screenshot missing/empty: ${outPath}`);
    }
    console.log(`[${logPrefix}] wrote ${outPath}`);
  } finally {
    await new Promise((resolve) => server.close(resolve));
  }
}

module.exports = {
  captureStaticPage,
  die,
  parseArgs
};
