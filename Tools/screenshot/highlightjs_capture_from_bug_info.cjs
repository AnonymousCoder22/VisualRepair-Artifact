/* eslint-disable no-console */
const fs = require('fs');
const path = require('path');
const http = require('http');
const url = require('url');
const { spawnSync } = require('child_process');
const os = require('os');

const HIGHLIGHTJS_TEMPLATE2_INSTANCE_IDS = new Set([
  'highlightjs__highlight.js-2684',
  'highlightjs__highlight.js-2703',
  'highlightjs__highlight.js-2704',
  'highlightjs__highlight.js-2726',
  'highlightjs__highlight.js-2727',
  'highlightjs__highlight.js-2740',
  'highlightjs__highlight.js-2750',
  'highlightjs__highlight.js-2765',
  'highlightjs__highlight.js-2785',
  'highlightjs__highlight.js-2811',
  'highlightjs__highlight.js-2897',
  'highlightjs__highlight.js-2899',
  'highlightjs__highlight.js-2927',
  'highlightjs__highlight.js-2932',
  'highlightjs__highlight.js-2958',
  'highlightjs__highlight.js-2960',
  'highlightjs__highlight.js-2969',
  'highlightjs__highlight.js-2972',
  'highlightjs__highlight.js-3000',
  'highlightjs__highlight.js-3018'
]);

const HIGHLIGHTJS_TEMPLATE1_INSTANCE_IDS = new Set([
  'highlightjs__highlight.js-3070',
  'highlightjs__highlight.js-3154',
  'highlightjs__highlight.js-3203',
  'highlightjs__highlight.js-3207',
  'highlightjs__highlight.js-3212',
  'highlightjs__highlight.js-3249',
  'highlightjs__highlight.js-3278',
  'highlightjs__highlight.js-3287',
  'highlightjs__highlight.js-3301',
  'highlightjs__highlight.js-3312',
  'highlightjs__highlight.js-3316',
  'highlightjs__highlight.js-3367',
  'highlightjs__highlight.js-3381',
  'highlightjs__highlight.js-3411',
  'highlightjs__highlight.js-3438',
  'highlightjs__highlight.js-3457',
  'highlightjs__highlight.js-3516',
  'highlightjs__highlight.js-3559',
  'highlightjs__highlight.js-3644'
]);

function parseArgs(argv) {
  const args = {};
  for (let i = 2; i < argv.length; i++) {
    const a = argv[i];
    if (a.startsWith('--')) {
      const key = a.slice(2);
      const next = argv[i + 1];
      if (next && !next.startsWith('--')) {
        args[key] = next;
        i++;
      } else {
        args[key] = 'true';
      }
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

function defaultHighlightTemplatePath(instanceId) {
  let templateName = 'template_highlightjs1.html';
  if (HIGHLIGHTJS_TEMPLATE2_INSTANCE_IDS.has(instanceId)) {
    templateName = 'template_highlightjs2.html';
  } else if (!HIGHLIGHTJS_TEMPLATE1_INSTANCE_IDS.has(instanceId)) {
    console.warn(`[highlightjs-screenshot] instance_id not mapped, fallback to template1: ${instanceId || ''}`);
  }
  return path.resolve(__dirname, 'code_template', 'highlightjs', templateName);
}

function contentTypeFor(p) {
  const ext = path.extname(p).toLowerCase();
  if (ext === '.js') return 'text/javascript; charset=utf-8';
  if (ext === '.css') return 'text/css; charset=utf-8';
  if (ext === '.svg') return 'image/svg+xml';
  if (ext === '.png') return 'image/png';
  if (ext === '.jpg' || ext === '.jpeg') return 'image/jpeg';
  if (ext === '.woff') return 'font/woff';
  if (ext === '.woff2') return 'font/woff2';
  if (ext === '.ttf') return 'font/ttf';
  if (ext === '.html') return 'text/html; charset=utf-8';
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
      return;
    }
    res.writeHead(200, { 'Content-Type': contentType });
    fs.createReadStream(filePath).pipe(res);
  } catch (err) {
    res.writeHead(404);
    res.end('not found');
  }
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

function commandExists(command) {
  const resolver = process.platform === 'win32' ? 'where' : 'which';
  const res = spawnSync(resolver, [command], { stdio: 'ignore', shell: false });
  return res.status === 0;
}

function tryChromiumScreenshot(browserExe, pageUrl, outPath, viewport) {
  const [vw, vh] = viewport;
  const tmpBase = fs.existsSync(path.dirname(outPath)) ? path.dirname(outPath) : os.tmpdir();
  const userDataDir = fs.mkdtempSync(path.join(tmpBase, 'guirepair-highlightjs-profile-'));

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
    // Give the page a small amount of time to execute scripts (Prism.highlightAll()).
    '--virtual-time-budget=2000',
    `--screenshot=${outPath}`,
    pageUrl
  ];

  // NOTE: Do not use `shell: true` on Windows here; it breaks executable paths with spaces.
  const res = spawnSync(browserExe, args, { stdio: 'inherit', shell: false });
  try {
    fs.rmSync(userDataDir, { recursive: true, force: true });
  } catch (err) {}
  if (res.status !== 0) {
    return { ok: false, reason: `chromium failed (rc=${res.status})` };
  }
  return { ok: true };
}

async function tryPlaywrightScreenshot(pageUrl, outPath, viewport) {
  let playwright = null;
  try {
    // eslint-disable-next-line import/no-dynamic-require, global-require
    playwright = require('playwright');
  } catch (e) {
    return { ok: false, reason: 'playwright not installed' };
  }
  if (!playwright || !playwright.chromium) {
    return { ok: false, reason: 'playwright chromium not available' };
  }

  const [vw, vh] = viewport;
  let browser = null;
  try {
    browser = await playwright.chromium.launch({
      headless: true,
      args: ['--no-sandbox', '--disable-dev-shm-usage']
    });
    const page = await browser.newPage({ viewport: { width: vw, height: vh } });
    await page.goto(pageUrl, { waitUntil: 'load', timeout: 120000 });
    // Wait for Prism highlighting to inject token spans.
    try {
      await page.waitForFunction(
        () => {
          const code = document.querySelector('pre code');
          if (!code) return false;
          if (code.querySelector('span.token')) return true;
          return code.innerHTML.includes('<span');
        },
        { timeout: 5000 }
      );
    } catch (err) {
      // If highlight didn't show up, still capture to aid debugging.
      // This keeps behavior predictable for downstream LLM comparison.
    }
    await page.waitForTimeout(200);
    await page.screenshot({ path: outPath });
  } catch (e) {
    return { ok: false, reason: `playwright failed: ${e && e.message ? e.message : String(e)}` };
  } finally {
    if (browser) {
      try {
        await browser.close();
      } catch (err) {}
    }
  }
  return { ok: true };
}

function resolveHtmlEntry(repoDir, htmlArg, instanceId) {
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

  return {
    htmlPath: defaultHighlightTemplatePath(instanceId),
    pagePath: '/code.html'
  };
}

async function main() {
  const args = parseArgs(process.argv);
  const repoDir = args.repo ? path.resolve(args.repo) : '';
  const outPath = args.out ? path.resolve(args.out) : '';
  const browserPath = findBrowserExecutable(args.chrome || '');
  const mode = normalizeMode(args.mode);
  const htmlArg = args.html ? String(args.html) : '';
  const instanceId = args['instance-id'] ? String(args['instance-id']) : '';

  if (!repoDir) die('missing --repo');
  if (!outPath) die('missing --out');
  if (!fs.existsSync(repoDir)) die(`repo does not exist: ${repoDir}`);

  const htmlEntry = resolveHtmlEntry(repoDir, htmlArg, instanceId);
  ensureDir(path.dirname(outPath));

  const server = http.createServer((req, res) => {
    const parsed = url.parse(req.url || '');
    const pathnameRaw = decodeURIComponent(parsed.pathname || '/');
    const pathname = pathnameRaw === '/' ? htmlEntry.pagePath : pathnameRaw;

    if (pathname === htmlEntry.pagePath) {
      staticFile(res, htmlEntry.htmlPath, contentTypeFor(htmlEntry.htmlPath));
      return;
    }

    const fullPath = safeJoin(repoDir, pathname);
    if (!fullPath) {
      res.writeHead(400);
      res.end('bad request');
      return;
    }
    staticFile(res, fullPath, contentTypeFor(fullPath));
  });

  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const { port } = server.address();
  const pageUrl = `http://127.0.0.1:${port}${htmlEntry.pagePath}`;

  try {
    const viewport = [1200, 800];
    let finalMode = mode;
    if (finalMode === 'auto') {
      finalMode = 'playwright';
    }

    if (finalMode === 'playwright') {
      const pw = await tryPlaywrightScreenshot(pageUrl, outPath, viewport);
      if (!pw.ok) {
        const chrom = tryChromiumScreenshot(browserPath, pageUrl, outPath, viewport);
        if (!chrom.ok) {
          die(`screenshot failed: playwright=${pw.reason}; chromium=${chrom.reason}`);
        }
      }
    } else {
      const chrom = tryChromiumScreenshot(browserPath, pageUrl, outPath, viewport);
      if (!chrom.ok) {
        const pw = await tryPlaywrightScreenshot(pageUrl, outPath, viewport);
        if (!pw.ok) {
          die(`screenshot failed: chromium=${chrom.reason}; playwright=${pw.reason}`);
        }
      }
    }

    if (!fs.existsSync(outPath) || fs.statSync(outPath).size <= 0) {
      die(`screenshot missing/empty: ${outPath}`);
    }
    console.log(`[highlightjs-screenshot] wrote ${outPath}`);
  } finally {
    await new Promise((resolve) => server.close(resolve));
  }
}

main().catch((e) => die(e && (e.stack || e.message) ? (e.stack || e.message) : String(e)));
