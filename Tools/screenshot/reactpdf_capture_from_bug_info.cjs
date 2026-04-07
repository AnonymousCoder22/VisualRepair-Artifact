#!/usr/bin/env node
/* eslint-disable no-console */
const path = require('path');
const { spawnSync } = require('child_process');

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

function main() {
  const args = parseArgs(process.argv);
  const repoDir = path.resolve(args.repo || '');
  const outPath = args.out ? path.resolve(args.out) : '';
  const baselineOut = args['baseline-out'] ? path.resolve(args['baseline-out']) : '';
  const patchedOut = args['patched-out'] ? path.resolve(args['patched-out']) : '';
  const helperScriptPath = path.join(__dirname, 'reactpdf_helpers.py');

  if (!args.repo) die('missing --repo');
  if (!args.out && !(baselineOut && patchedOut)) {
    die('missing --out or (--baseline-out and --patched-out)');
  }

  const argv = baselineOut && patchedOut
    ? [
        helperScriptPath,
        'pair',
        '--repo-dir',
        repoDir,
        '--baseline-out',
        baselineOut,
        '--patched-out',
        patchedOut,
      ]
    : [
        helperScriptPath,
        'single',
        '--repo-dir',
        repoDir,
        '--out',
        outPath,
      ];

  if (args['instance-root']) {
    argv.push('--instance-root', path.resolve(args['instance-root']));
  }

  if (baselineOut && patchedOut) {
    if (args['keep-worktree'] === 'true') {
      argv.push('--keep-worktree');
    }
  } else {
    if (args['work-dir']) {
      argv.push('--work-dir', path.resolve(args['work-dir']));
    }
    if (args['source-strategy']) {
      argv.push('--source-strategy', args['source-strategy']);
    }
    for (const flag of ['skip-install', 'skip-build', 'force-install', 'force-build']) {
      if (args[flag] === 'true') {
        argv.push(`--${flag}`);
      }
    }
  }

  const proc = spawnSync(
    'python3',
    argv,
    {
      stdio: 'inherit',
      shell: false,
    }
  );

  if (proc.status !== 0) {
    process.exit(proc.status || 1);
  }

  if (baselineOut && patchedOut) {
    console.log(`[reactpdf-capture] wrote ${baselineOut} and ${patchedOut}`);
  } else {
    console.log(`[reactpdf-capture] wrote ${outPath}`);
  }
}

main();
