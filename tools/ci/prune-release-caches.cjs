/**
 * Keep one current release cache per platform and cache kind on main.
 * Invoke from actions/github-script after every platform build has succeeded.
 * Only this workflow's native, Pandoc and toolchain cache namespaces are pruned;
 * legacy snapshots are included so migration does not retain multiple multi-GB copies.
 */

/** Group release cache generations without touching unrelated workflows or tag scopes. */
function cacheGroup(cache) {
  if (cache.ref !== 'refs/heads/main') return undefined;
  const native = /^native-v[123]-(windows-latest-X64|macos-14-ARM64|manylinux_2_28-x86_64)-rust-/.exec(cache.key);
  if (native) return `rust:${native[1]}`;
  const worker = /^pandoc-worker-v[123]-(Windows-X64|macOS-ARM64|manylinux_2_28-x86_64)-ghc-/.exec(cache.key);
  if (worker) return `worker:${worker[1]}`;
  const toolchain = /^release-toolchain-v[12]-(macOS-ARM64|manylinux_2_28-x86_64)-/.exec(cache.key);
  if (toolchain) return `toolchain:${toolchain[1]}`;
  return undefined;
}

/** Delete superseded snapshots after success, retaining the newest cache in each group. */
module.exports = async function pruneReleaseCaches({ github, context, core }) {
  const caches = await github.paginate(github.rest.actions.getActionsCacheList, {
    ...context.repo, ref: 'refs/heads/main', per_page: 100,
  });
  const latest = new Map();
  const obsolete = [];
  for (const cache of caches) {
    const group = cacheGroup(cache);
    if (!group) continue;
    const previous = latest.get(group);
    if (!previous) {
      latest.set(group, cache);
    } else if (Date.parse(cache.created_at) > Date.parse(previous.created_at)
               || (cache.created_at === previous.created_at && cache.id > previous.id)) {
      obsolete.push(previous);
      latest.set(group, cache);
    } else {
      obsolete.push(cache);
    }
  }
  for (const cache of obsolete) {
    try {
      await github.rest.actions.deleteActionsCacheById({ ...context.repo, cache_id: cache.id });
      core.info(`Removed superseded release cache ${cache.key} (${Math.round(cache.size_in_bytes / 1024 / 1024)} MiB)`);
    } catch (error) {
      // GitHub may evict a listed cache before our deletion reaches the service.
      if (error.status !== 404) throw error;
    }
  }
  core.info(`Retained ${latest.size} current release cache groups`);
};
