/* eslint-disable no-console */
const fs = require('fs');
const path = require('path');
const http = require('http');
const url = require('url');
const os = require('os');
const { spawnSync } = require('child_process');
const { parseArgs } = require('./common/capture_common.cjs');

const DEFAULT_VIEWPORT = { width: 2400, height: 1600 };

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

function die(message, extra = {}) {
  const err = new Error(message);
  Object.assign(err, extra);
  throw err;
}

function ensureDir(dirPath) {
  fs.mkdirSync(dirPath, { recursive: true });
}

function contentTypeFor(filePath) {
  const ext = path.extname(filePath).toLowerCase();
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
    res.writeHead(200, {
      'Content-Type': contentType,
      'Connection': 'close',
    });
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

function defaultHighlightTemplatePath(instanceId) {
  let templateName = 'template_highlightjs1.html';
  if (HIGHLIGHTJS_TEMPLATE2_INSTANCE_IDS.has(instanceId)) {
    templateName = 'template_highlightjs2.html';
  } else if (!HIGHLIGHTJS_TEMPLATE1_INSTANCE_IDS.has(instanceId)) {
    console.warn(`[highlightjs-strict] instance_id not mapped, fallback to template1: ${instanceId || ''}`);
  }
  return path.resolve(__dirname, 'code_template', 'highlightjs', templateName);
}

function resolveHtmlPath(repoDir, htmlArg) {
  if (!htmlArg) {
    return path.join(repoDir, 'code.html');
  }
  if (path.isAbsolute(htmlArg)) {
    return path.resolve(htmlArg);
  }
  return path.join(repoDir, htmlArg);
}

function ensureHtmlFile(repoDir, htmlArg, instanceId, forceTemplate) {
  const htmlPath = resolveHtmlPath(repoDir, htmlArg);
  const templatePath = defaultHighlightTemplatePath(instanceId);
  if (forceTemplate || !fs.existsSync(htmlPath)) {
    ensureDir(path.dirname(htmlPath));
    fs.copyFileSync(templatePath, htmlPath);
  }
  if (!fs.existsSync(htmlPath)) {
    die(`html file not found: ${htmlPath}`);
  }
  return { htmlPath, templatePath };
}

function runBuildCommand(repoDir, buildCmd) {
  if (!buildCmd) {
    return { cmd: '', skipped: true, status: 0, stdout: '', stderr: '' };
  }

  const result = spawnSync(buildCmd, {
    cwd: repoDir,
    shell: true,
    encoding: 'utf-8',
    stdio: 'pipe',
    env: process.env,
  });

  return {
    cmd: buildCmd,
    skipped: false,
    status: typeof result.status === 'number' ? result.status : 1,
    stdout: result.stdout || '',
    stderr: result.stderr || '',
    error: result.error ? String(result.error) : '',
  };
}

function assertBuildArtifacts(repoDir) {
  const required = [
    path.join(repoDir, 'build', 'highlight.js'),
    path.join(repoDir, 'build', 'demo', 'styles', 'rainbow.css'),
  ];
  const missing = required.filter((filePath) => !fs.existsSync(filePath));
  if (missing.length > 0) {
    die('required build artifacts are missing', { missingArtifacts: missing });
  }
  return required;
}

function diagnosticsPathFor(outPath, explicitPath) {
  if (explicitPath) return path.resolve(explicitPath);
  return `${outPath}.diagnostics.json`;
}

function commandExists(command) {
  const resolver = process.platform === 'win32' ? 'where' : 'which';
  const res = spawnSync(resolver, [command], { stdio: 'ignore', shell: false });
  return res.status === 0;
}

function findBrowserExecutable(explicitPath) {
  if (explicitPath) {
    const candidate = path.resolve(explicitPath);
    if (fs.existsSync(candidate)) return candidate;
  }

  const envCandidates = [
    process.env.GUIREPAIR_CHROME,
    process.env.CHROME_PATH,
    process.env.PUPPETEER_EXECUTABLE_PATH,
  ].filter(Boolean);

  for (const candidate of envCandidates) {
    try {
      const abs = path.resolve(candidate);
      if (fs.existsSync(abs)) return abs;
      if (commandExists(candidate)) return candidate;
    } catch (err) {}
  }

  const absoluteCandidates = process.platform === 'darwin'
    ? [
        '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
        '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge',
      ]
    : [];

  for (const candidate of absoluteCandidates) {
    if (fs.existsSync(candidate)) return candidate;
  }

  const pathCandidates = ['chromium', 'chromium-browser', 'google-chrome', 'google-chrome-stable', 'chrome', 'msedge'];
  for (const candidate of pathCandidates) {
    if (commandExists(candidate)) return candidate;
  }

  return '';
}

function stripHtmlText(htmlText) {
  return String(htmlText || '')
    .replace(/<script[\s\S]*?<\/script>/gi, ' ')
    .replace(/<style[\s\S]*?<\/style>/gi, ' ')
    .replace(/<[^>]+>/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

function buildPageStateFromHtml(htmlText) {
  const html = String(htmlText || '');
  return {
    title: '',
    readyState: 'complete',
    codeBlockCount: (html.match(/<code\b/gi) || []).length,
    preBlockCount: (html.match(/<pre\b/gi) || []).length,
    spanCounts: [],
    totalSpanCount: (html.match(/<span\b/gi) || []).length,
    textLengths: [],
    totalTextLength: stripHtmlText(html).length,
    classNames: [],
    bodyTextLength: stripHtmlText(html).length,
  };
}

function collectNoHighlightWarnings(consoleEntries) {
  return (consoleEntries || [])
    .filter((entry) => /Could not find the language|Falling back to no-highlight mode/i.test(entry.text || ''))
    .map((entry) => entry.text || '');
}

function hasExpectedCodeContent(pageState) {
  if (!pageState) return false;
  if ((pageState.preBlockCount || 0) < 1) return false;
  return (pageState.totalTextLength || 0) > 0;
}

async function captureWithPlaywright(pageUrl, outPath, diagnostics, viewport, timeoutMs, waitAfterLoadMs) {
  let playwright = null;
  try {
    playwright = require('playwright');
  } catch (err) {
    die('playwright is not installed');
  }

  const browser = await playwright.chromium.launch({
    headless: true,
    args: ['--no-sandbox', '--disable-dev-shm-usage'],
  });

  const page = await browser.newPage({ viewport });
  page.on('console', (msg) => {
    diagnostics.console.push({
      type: msg.type(),
      text: msg.text(),
    });
  });
  page.on('pageerror', (err) => {
    diagnostics.pageErrors.push(String(err && err.stack ? err.stack : err));
  });
  page.on('requestfailed', (request) => {
    diagnostics.requestFailures.push({
      url: request.url(),
      method: request.method(),
      failure: request.failure() ? request.failure().errorText : 'request failed',
    });
  });
  page.on('response', (response) => {
    if (response.status() >= 400) {
      diagnostics.httpFailures.push({
        url: response.url(),
        status: response.status(),
      });
    }
  });

  try {
    await page.goto(pageUrl, { waitUntil: 'load', timeout: timeoutMs });
    try {
      await page.waitForLoadState('networkidle', { timeout: Math.min(timeoutMs, 5000) });
    } catch (err) {}

    await page.waitForFunction(
      () => typeof window !== 'undefined' && typeof window.hljs !== 'undefined',
      { timeout: timeoutMs }
    );

    await page.waitForFunction(
      () => document.querySelectorAll('pre code').length > 0,
      { timeout: timeoutMs }
    );

    if (waitAfterLoadMs > 0) {
      await page.waitForTimeout(waitAfterLoadMs);
    }

    diagnostics.pageState = await page.evaluate(() => {
      const codeBlocks = Array.from(document.querySelectorAll('pre code'));
      const spanCounts = codeBlocks.map((node) => node.querySelectorAll('span').length);
      const textLengths = codeBlocks.map((node) => (node.innerText || '').length);
      const classNames = codeBlocks.map((node) => node.className || '');
      const preBlocks = Array.from(document.querySelectorAll('pre'));
      return {
        title: document.title || '',
        readyState: document.readyState,
        codeBlockCount: codeBlocks.length,
        preBlockCount: preBlocks.length,
        spanCounts,
        totalSpanCount: spanCounts.reduce((sum, value) => sum + value, 0),
        textLengths,
        totalTextLength: textLengths.reduce((sum, value) => sum + value, 0),
        classNames,
        bodyTextLength: (document.body && document.body.innerText ? document.body.innerText.length : 0),
      };
    });
    diagnostics.noHighlightWarnings = collectNoHighlightWarnings(diagnostics.console);

    if (diagnostics.pageErrors.length > 0) {
      die('page errors detected during capture');
    }
    if (diagnostics.requestFailures.length > 0) {
      die('network request failures detected during capture');
    }
    if (diagnostics.httpFailures.length > 0) {
      die('http failures detected during capture');
    }
    if ((diagnostics.noHighlightWarnings || []).length > 0) {
      die('highlight.js fell back to no-highlight mode');
    }
    if (!hasExpectedCodeContent(diagnostics.pageState)) {
      die('page rendered without the expected code content');
    }

    await page.screenshot({ path: outPath });
    diagnostics.success = true;
  } catch (err) {
    diagnostics.success = false;
    diagnostics.error = String(err && err.stack ? err.stack : err);
    try {
      diagnostics.pageHtml = await page.content();
    } catch (snapshotErr) {
      diagnostics.pageHtml = `<failed to capture page html: ${String(snapshotErr)}>`;
    }
    throw err;
  } finally {
    await browser.close();
  }
}

function captureWithChromiumCli(browserExe, pageUrl, outPath, diagnostics, viewport, fallbackHtmlText = '') {
  diagnostics.pageHtml = String(fallbackHtmlText || '');
  diagnostics.pageState = buildPageStateFromHtml(diagnostics.pageHtml);
  if (!hasExpectedCodeContent(diagnostics.pageState)) {
    die('html source does not contain the expected code content');
  }

  const tmpBase = fs.existsSync(path.dirname(outPath)) ? path.dirname(outPath) : os.tmpdir();
  const userDataDir = fs.mkdtempSync(path.join(tmpBase, 'highlightjs-capture-profile-'));
  const baseArgs = [
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
    `--window-size=${viewport.width},${viewport.height}`,
    '--virtual-time-budget=2500',
  ];
  try {
    const shotRes = spawnSync(browserExe, [...baseArgs, `--screenshot=${outPath}`, pageUrl], {
      shell: false,
      encoding: 'utf-8',
      stdio: 'pipe',
      timeout: 30000,
    });
    if (shotRes.status !== 0) {
      die(`chromium screenshot failed (rc=${shotRes.status})`, {
        chromiumStdout: shotRes.stdout || '',
        chromiumStderr: shotRes.stderr || '',
      });
    }
    diagnostics.success = true;
  } finally {
    try {
      fs.rmSync(userDataDir, { recursive: true, force: true });
    } catch (err) {}
  }
}

async function main() {
  const args = parseArgs(process.argv);
  const repoDir = args.repo ? path.resolve(args.repo) : '';
  const outPath = args.out ? path.resolve(args.out) : '';
  const instanceId = args['instance-id'] ? String(args['instance-id']) : '';
  const htmlArg = args.html ? String(args.html) : 'code.html';
  const buildCmd = args['build-cmd'] ? String(args['build-cmd']) : '';
  const forceTemplate = String(args['force-template'] || '').toLowerCase() === 'true';
  const diagnosticsPath = diagnosticsPathFor(outPath, args.diagnostics || '');
  const browserPath = args.chrome ? String(args.chrome) : '';
  const requestedMode = String(args.mode || '').trim().toLowerCase();
  const timeoutMs = Number.parseInt(String(args['timeout-ms'] || '15000'), 10);
  const waitAfterLoadMs = Number.parseInt(String(args['wait-after-load-ms'] || '500'), 10);

  if (!repoDir) die('missing --repo');
  if (!outPath) die('missing --out');
  if (!fs.existsSync(repoDir)) die(`repo does not exist: ${repoDir}`);

  ensureDir(path.dirname(outPath));
  ensureDir(path.dirname(diagnosticsPath));

  const diagnostics = {
    repoDir,
    outPath,
    instanceId,
    htmlArg,
    diagnosticsPath,
    build: null,
    artifacts: [],
    console: [],
    pageErrors: [],
    requestFailures: [],
    httpFailures: [],
    noHighlightWarnings: [],
    pageState: null,
    success: false,
    timestamp: new Date().toISOString(),
  };

  try {
    const { htmlPath, templatePath } = ensureHtmlFile(repoDir, htmlArg, instanceId, forceTemplate);
    diagnostics.htmlPath = htmlPath;
    diagnostics.templatePath = templatePath;
    diagnostics.sourceHtml = fs.readFileSync(htmlPath, 'utf-8');

    diagnostics.build = runBuildCommand(repoDir, buildCmd);
    if (diagnostics.build.status !== 0) {
      die(`build command failed: ${diagnostics.build.cmd || '(empty)'}`, { build: diagnostics.build });
    }

    diagnostics.artifacts = assertBuildArtifacts(repoDir);

    const staticRoots = Array.from(new Set([repoDir, path.dirname(htmlPath)].map((entry) => path.resolve(entry))));
    const pagePath = `/${path.relative(repoDir, htmlPath).replace(/\\/g, '/')}`;
    const viewport = {
      width: Number.parseInt(String(args['viewport-width'] || DEFAULT_VIEWPORT.width), 10),
      height: Number.parseInt(String(args['viewport-height'] || DEFAULT_VIEWPORT.height), 10),
    };

    const server = http.createServer((req, res) => {
      res.setHeader('Connection', 'close');
      const parsed = url.parse(req.url || '');
      const pathnameRaw = decodeURIComponent(parsed.pathname || '/');
      const pathname = pathnameRaw === '/' ? pagePath : pathnameRaw;

      if (pathname === pagePath) {
        staticFile(res, htmlPath, contentTypeFor(htmlPath));
        return;
      }
      serveFromRoots(res, pathname, staticRoots);
    });
    server.keepAliveTimeout = 1;
    server.headersTimeout = Math.max(timeoutMs, 15000) + 1000;

    await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
    const { port } = server.address();
    const pageUrl = `http://127.0.0.1:${port}${pagePath}`;
    diagnostics.pageUrl = pageUrl;

    try {
      if (requestedMode === 'chromium' || requestedMode === 'chrome' || requestedMode === 'edge') {
        const browserExe = findBrowserExecutable(browserPath);
        if (!browserExe) {
          die('requested chromium mode but no browser executable was found');
        }
        diagnostics.captureMode = 'chromium-cli';
        captureWithChromiumCli(browserExe, pageUrl, outPath, diagnostics, viewport, diagnostics.sourceHtml);
      } else {
        try {
          diagnostics.captureMode = 'playwright';
          await captureWithPlaywright(pageUrl, outPath, diagnostics, viewport, timeoutMs, waitAfterLoadMs);
        } catch (err) {
          if (!String(err && err.message ? err.message : err).includes('playwright is not installed')) {
            throw err;
          }
          const browserExe = findBrowserExecutable(browserPath);
          if (!browserExe) {
            throw err;
          }
          diagnostics.captureMode = 'chromium-cli';
          captureWithChromiumCli(browserExe, pageUrl, outPath, diagnostics, viewport, diagnostics.sourceHtml);
        }
      }
    } finally {
      if (typeof server.closeAllConnections === 'function') {
        server.closeAllConnections();
      }
      if (typeof server.closeIdleConnections === 'function') {
        server.closeIdleConnections();
      }
      await new Promise((resolve) => server.close(resolve));
    }

    fs.writeFileSync(diagnosticsPath, JSON.stringify(diagnostics, null, 2), 'utf-8');
    console.log(`[highlightjs-strict] wrote ${outPath}`);
    console.log(`[highlightjs-strict] diagnostics ${diagnosticsPath}`);
  } catch (err) {
    diagnostics.error = String(err && err.stack ? err.stack : err);
    fs.writeFileSync(diagnosticsPath, JSON.stringify(diagnostics, null, 2), 'utf-8');
    console.error(`[highlightjs-strict] failed: ${diagnostics.error}`);
    console.error(`[highlightjs-strict] diagnostics ${diagnosticsPath}`);
    process.exit(1);
  }
}

main();
