/**
 * Resolve tested wheels for a version-tag run of publish-pypi.yml.
 * actions/github-script passes its GitHub client, context and logger to this module.
 * Wait for the same commit's main build instead of compiling three platforms twice;
 * return an empty run ID when a tag needs its own build or retained artifacts expired.
 */
const { setTimeout: delay } = require('node:timers/promises');

/** Select complete wheels from the same commit, with bounded waits for concurrent pushes. */
module.exports = async function resolveReleaseArtifacts({ github, context, core, sleep = delay, now = Date.now }) {
  core.setOutput('artifact-run-id', '');
  if (context.eventName !== 'push' || !context.ref.startsWith('refs/tags/v')) {
    return '';
  }

  const started = now();
  // Cold toolchain/dependency builds can exceed 30 minutes on the first optimized run.
  const deadline = started + 75 * 60 * 1000;
  const expected = new Set(['wheel-Windows', 'wheel-macOS', 'wheel-Linux']);
  let build;
  while (now() < deadline) {
    if (build) {
      // A discovered run can temporarily disappear from filtered list results.
      // Track its stable ID so that an indexing gap cannot trigger duplicate builds.
      const response = await github.rest.actions.getWorkflowRun({
        ...context.repo, run_id: build.id,
      });
      build = response.data;
    } else {
      const response = await github.rest.actions.listWorkflowRuns({
        ...context.repo,
        workflow_id: 'publish-pypi.yml',
        branch: 'main',
        head_sha: context.sha,
        per_page: 100,
      });
      for (const candidate of response.data.workflow_runs) {
        // Defend against stale API results and never wait on this tag run itself.
        if (candidate.id !== context.runId && candidate.head_sha === context.sha
            && candidate.head_branch === 'main'
            && ['push', 'workflow_dispatch'].includes(candidate.event)) {
          build = candidate;
          break;
        }
      }
    }
    if (!build) {
      // Branch and tag events from one push may become visible a few seconds apart.
      if (now() - started >= 90 * 1000) {
        core.info('No matching main build found; building wheels for this tag');
        return '';
      }
    } else if (build.status === 'completed') {
      if (build.conclusion !== 'success') {
        core.warning(`Main build ${build.id} ended with ${build.conclusion}; rebuilding for this tag`);
        return '';
      }
      const artifacts = await github.paginate(github.rest.actions.listWorkflowRunArtifacts, {
        ...context.repo, run_id: build.id, per_page: 100,
      });
      const available = new Set();
      for (const artifact of artifacts) {
        if (!artifact.expired && artifact.size_in_bytes > 0 && expected.has(artifact.name)) {
          available.add(artifact.name);
        }
      }
      if (available.size !== expected.size) {
        core.warning(`Main build ${build.id} has missing or expired wheels; rebuilding for this tag`);
        return '';
      }
      core.info(`Reusing all three tested wheels from ${build.html_url}`);
      core.setOutput('artifact-run-id', String(build.id));
      return String(build.id);
    } else {
      core.info(`Waiting for main build ${build.id}: ${build.status}`);
    }
    await sleep(30 * 1000);
  }
  throw new Error('Timed out waiting for the matching main build; rerun this tag workflow after it completes');
};
