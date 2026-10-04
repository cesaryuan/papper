/**
 * Verify release artifact selection and cache retention at the GitHub API boundary.
 * Run with `node --test tools/ci/release-artifacts.test.cjs`; no live requests or sleeps.
 * These cases prevent publishing another commit's wheels and repeating a concurrent build.
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const resolve = require('./release-artifacts.cjs');
const prune = require('./prune-release-caches.cjs');

/** Model GitHub's mutable run/artifact state and clock without contacting the service. */
function releaseService({ snapshots = [[]], artifacts = [], ref = 'refs/tags/v1.2.3' } = {}) {
  let clock = 0;
  let position = 0;
  const outputs = {};
  return {
    outputs,
    context: { eventName: 'push', ref, sha: 'release-commit', runId: 99, repo: { owner: 'owner', repo: 'repo' } },
    core: {
      /** Record the downstream decision to reuse wheels or schedule a build. */
      setOutput(name, value) { outputs[name] = value; },
      /** Suppress CI progress messages during the local behavioral checks. */
      info() {},
      /** Suppress expected fallback warnings during the local behavioral checks. */
      warning() {},
    },
    github: {
      rest: { actions: {
        /** Return the next externally observed workflow state. */
        async listWorkflowRuns() {
          const runs = snapshots[Math.min(position++, snapshots.length - 1)];
          return { data: { workflow_runs: runs } };
        },
        /** Return the retained wheel artifacts exposed by GitHub. */
        async listWorkflowRunArtifacts() { return artifacts; },
      } },
      /** Resolve the fake API collection through the same pagination boundary. */
      async paginate(endpoint, params) { return endpoint(params); },
    },
    /** Advance the external clock instead of introducing real waiting time. */
    async sleep(milliseconds) { clock += milliseconds; },
    /** Expose elapsed time for registration races and stuck builds. */
    now() { return clock; },
  };
}

/** Describe a successful main build of exactly the release commit. */
function mainRun(overrides = {}) {
  return { id: 42, head_sha: 'release-commit', head_branch: 'main', event: 'push',
    status: 'completed', conclusion: 'success', html_url: 'https://example.test/runs/42', ...overrides };
}

/** Describe three complete, nonexpired wheels as uploaded by the platform builds. */
function wheels() {
  return [
    { name: 'wheel-Windows', expired: false, size_in_bytes: 100 },
    { name: 'wheel-macOS', expired: false, size_in_bytes: 100 },
    { name: 'wheel-Linux', expired: false, size_in_bytes: 100 },
  ];
}

/** A normal branch push must build rather than wait for itself. */
test('main schedules platform builds', async () => {
  const service = releaseService({ ref: 'refs/heads/main' });
  assert.equal(await resolve(service), '');
  assert.equal(service.outputs['artifact-run-id'], '');
});

/** Both event registration and an unfinished main build must avoid duplicate compilation. */
test('a simultaneous tag waits for main and reuses all tested wheels', async () => {
  const service = releaseService({ snapshots: [[], [mainRun({ status: 'in_progress', conclusion: null })], [mainRun()]], artifacts: wheels() });
  assert.equal(await resolve(service), '42');
  assert.equal(service.outputs['artifact-run-id'], '42');
  assert.equal(service.now(), 60_000);
});

/** A successful build of another commit or branch can never supply release wheels. */
test('stale or unrelated API results fall back to a fresh tag build', async () => {
  const service = releaseService({ snapshots: [[mainRun({ head_sha: 'old-commit' }), mainRun({ head_branch: 'feature' })]], artifacts: wheels() });
  assert.equal(await resolve(service), '');
  assert.equal(service.outputs['artifact-run-id'], '');
  assert.equal(service.now(), 90_000);
});

/** Expired or incomplete retained artifacts must not produce a partial release. */
test('missing and expired wheels cause a fresh build', async () => {
  for (const artifacts of [wheels().slice(0, 2), [...wheels().slice(0, 2), { name: 'wheel-Linux', expired: true, size_in_bytes: 100 }]]) {
    const service = releaseService({ snapshots: [[mainRun()]], artifacts });
    assert.equal(await resolve(service), '');
    assert.equal(service.outputs['artifact-run-id'], '');
  }
});

/** A failed branch build is recoverable by compiling in the tag run. */
test('a failed main build allows tag recovery', async () => {
  const service = releaseService({ snapshots: [[mainRun({ conclusion: 'failure' })]], artifacts: wheels() });
  assert.equal(await resolve(service), '');
});

/** A permanently queued build must stop with a useful failure instead of waiting forever. */
test('a stuck main build reaches the bounded wait failure', async () => {
  const service = releaseService({ snapshots: [[mainRun({ status: 'queued', conclusion: null })]] });
  await assert.rejects(resolve(service), /Timed out waiting/);
  assert.equal(service.outputs['artifact-run-id'], '');
});

/** Layout migration must discard incomplete toolchains while preserving current snapshots and tag scopes. */
test('cache pruning retains one newest snapshot per kind and platform', async () => {
  let stored = [
    { id: 1, key: 'native-v2-macos-14-ARM64-rust-1.98.0-old', ref: 'refs/heads/main', created_at: '2026-10-01T00:00:00Z', size_in_bytes: 100 },
    { id: 2, key: 'native-v3-macos-14-ARM64-rust-1.98.0-new', ref: 'refs/heads/main', created_at: '2026-10-02T00:00:00Z', size_in_bytes: 100 },
    { id: 3, key: 'native-v3-windows-latest-X64-rust-1.98.0-new', ref: 'refs/heads/main', created_at: '2026-10-02T00:00:00Z', size_in_bytes: 100 },
    { id: 4, key: 'native-v3-macos-14-ARM64-rust-1.98.0-tag', ref: 'refs/tags/v1', created_at: '2026-10-01T00:00:00Z', size_in_bytes: 100 },
    { id: 5, key: 'unrelated-workflow', ref: 'refs/heads/main', created_at: '2026-10-01T00:00:00Z', size_in_bytes: 100 },
    { id: 6, key: 'pandoc-worker-v2-macOS-ARM64-ghc-9.14.1-old', ref: 'refs/heads/main', created_at: '2026-10-01T00:00:00Z', size_in_bytes: 100 },
    { id: 7, key: 'pandoc-worker-v3-macOS-ARM64-ghc-9.14.1-new', ref: 'refs/heads/main', created_at: '2026-10-02T00:00:00Z', size_in_bytes: 100 },
    { id: 8, key: 'release-toolchain-v1-macOS-ARM64-ghc-9.14.1-cabal-3.18.1.0', ref: 'refs/heads/main', created_at: '2026-10-01T00:00:00Z', size_in_bytes: 100 },
    { id: 9, key: 'release-toolchain-v2-macOS-ARM64-ghc-9.14.1-cabal-3.18.1.0', ref: 'refs/heads/main', created_at: '2026-10-02T00:00:00Z', size_in_bytes: 100 },
    { id: 10, key: 'release-toolchain-v1-manylinux_2_28-x86_64-rust-1.98.0-ghc-9.14.1-cabal-3.18.1.0-old', ref: 'refs/heads/main', created_at: '2026-10-01T00:00:00Z', size_in_bytes: 100 },
    { id: 11, key: 'release-toolchain-v2-manylinux_2_28-x86_64-rust-1.98.0-ghc-9.14.1-cabal-3.18.1.0-new', ref: 'refs/heads/main', created_at: '2026-10-02T00:00:00Z', size_in_bytes: 100 },
    { id: 12, key: 'release-toolchain-v1-macOS-ARM64-ghc-9.14.1-cabal-3.18.1.0', ref: 'refs/tags/v1', created_at: '2026-10-01T00:00:00Z', size_in_bytes: 100 },
  ];
  const service = releaseService();
  /** Return actual cache service state, independent of the retention implementation. */
  service.github.rest.actions.getActionsCacheList = async function listCaches() { return [...stored]; };
  /** Apply a cache deletion to the externally observable inventory. */
  service.github.rest.actions.deleteActionsCacheById = async function deleteCache({ cache_id }) {
    const remaining = [];
    for (const cache of stored) if (cache.id !== cache_id) remaining.push(cache);
    stored = remaining;
  };
  await prune(service);
  const retained = [];
  for (const cache of stored) retained.push(cache.id);
  assert.deepEqual(retained, [2, 3, 4, 5, 7, 9, 11, 12]);
});
