/* eslint-disable no-console */
const fs = require('fs');
const path = require('path');
const { captureStaticPage, parseArgs } = require('./common/capture_common.cjs');

const WAIT_FOR_CANVAS =
  "() => { const canvas = document.querySelector('canvas'); return !!canvas && canvas.width > 0 && canvas.height > 0; }";
const DEFAULT_VIEWPORT = [2400, 1600];

function readRuntimeMeta(repoDir) {
  const metaPath = path.resolve(repoDir, 'chartjs_runtime_meta.json');
  try {
    if (!fs.existsSync(metaPath)) {
      return {};
    }
    return JSON.parse(fs.readFileSync(metaPath, 'utf-8')) || {};
  } catch (error) {
    return {};
  }
}

async function main() {
  const args = parseArgs(process.argv);
  const runtimeMeta = readRuntimeMeta(args.repo || '');
  const viewport = [
    Number.isFinite(Number(runtimeMeta.viewport_width)) ? Math.max(320, Number(runtimeMeta.viewport_width)) : DEFAULT_VIEWPORT[0],
    Number.isFinite(Number(runtimeMeta.viewport_height)) ? Math.max(160, Number(runtimeMeta.viewport_height)) : DEFAULT_VIEWPORT[1]
  ];
  const waitAfterLoadMs = Number.isFinite(Number(runtimeMeta.wait_after_ms)) ? Number(runtimeMeta.wait_after_ms) : 300;
  const virtualTimeBudget = Number.isFinite(Number(runtimeMeta.ready_timeout_ms))
    ? Math.max(2500, Number(runtimeMeta.ready_timeout_ms))
    : 2500;
  await captureStaticPage({
    repoDir: args.repo,
    outPath: args.out,
    browserPath: args.chrome || '',
    mode: args.mode || 'auto',
    htmlArg: args.html || '',
    defaultHtmlPath: path.resolve(__dirname, 'code_template', 'chartjs', 'template_chartjs.html'),
    defaultPagePath: '/code.html',
    viewport,
    waitForFunction: WAIT_FOR_CANVAS,
    waitAfterLoadMs,
    virtualTimeBudget,
    logPrefix: 'chartjs-screenshot'
  });
}

main().catch((e) => {
  console.error(e && (e.stack || e.message) ? (e.stack || e.message) : String(e));
  process.exit(1);
});
