const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');

const model = process.env.AI_MODEL || 'deepseek-v4-flash';
const apiUrl = process.env.AI_URL || 'https://api.ollama.cloud/v1/chat/completions';
const apiKey = process.env.AI_API_KEY;

module.exports = async function(github, context, core) {
  const { GITHUB_WORKSPACE } = process.env;

  function exec(cmd, opts = {}) {
    try {
      return execSync(cmd, { encoding: 'utf-8', timeout: 15000, ...opts }).trim();
    } catch (e) {
      if (opts.fatal) throw e;
      return '';
    }
  }

  function categorizeError(logSnippet, jobs) {
    if (logSnippet) {
      if (/apt-get install.*fail|E: (Package.*has no installation candidate|Unable to locate package)/i.test(logSnippet))
        return { type: 'build-dep', severity: 3, label: 'error:build-dep' };
      if (/error\[E\d+\]|error: aborting|cargo.*failed|make.*\d+Error/i.test(logSnippet))
        return { type: 'compile', severity: 4, label: 'error:compile' };
      if (/test.*FAIL|assertion failed|panic/i.test(logSnippet))
        return { type: 'test', severity: 2, label: 'error:test' };
      if (/timeout|resolve.*host|connection refused/i.test(logSnippet))
        return { type: 'network', severity: 3, label: 'error:network' };
      if (/killed|OOM|memory.*exhausted/i.test(logSnippet))
        return { type: 'oom', severity: 5, label: 'error:oom' };
      if (/The operation was canceled|timed out/i.test(logSnippet))
        return { type: 'timeout', severity: 2, label: 'error:timeout' };
    }
    if (jobs) {
      const failedStepNames = jobs.flatMap(j => (j.steps || [])
        .filter(s => s.conclusion === 'failure').map(s => s.name));
      if (failedStepNames.some(s => /^Install package build dep/i.test(s)))
        return { type: 'build-dep', severity: 3, label: 'error:build-dep' };
      if (failedStepNames.some(s => /^Build /i.test(s)))
        return { type: 'compile', severity: 4, label: 'error:compile' };
      if (failedStepNames.some(s => /test/i.test(s)))
        return { type: 'test', severity: 2, label: 'error:test' };
    }
    return { type: 'unknown', severity: 2, label: 'error:unknown' };
  }

  function getBlameInfo(files) {
    return (files || []).filter(Boolean).map(f => ({
      file: f,
      blame: exec(`git blame -L 1,1 -- "${f}" 2>/dev/null || true`),
      lastCommit: exec(`git log -1 --format="%h %an %ad: %s" -- "${f}" 2>/dev/null || true`),
    }));
  }

  function getCommitDiff() {
    const stat = exec('git diff --stat HEAD~1 HEAD 2>/dev/null || true');
    const patch = exec('git diff HEAD~1 HEAD 2>/dev/null || true');
    return { stat, patch: patch.slice(0, 3000) };
  }

  function getBuildTimes(run) {
    const steps = run.steps || [];
    return steps.filter(s => s.started_at && s.completed_at).map(s => ({
      name: s.name,
      duration: ((new Date(s.completed_at)) - (new Date(s.started_at))) / 1000,
    }));
  }

  function getDependents(pkgName) {
    const pkgsDir = path.join(GITHUB_WORKSPACE, 'packages');
    if (!fs.existsSync(pkgsDir)) return [];
    const packages = fs.readdirSync(pkgsDir).filter(d =>
      fs.statSync(path.join(pkgsDir, d)).isDirectory()
    );
    return packages.filter(p => {
      if (p === pkgName) return false;
      const cfg = readPackageConfig(p);
      return cfg?.raw?.includes(pkgName);
    });
  }

  function extractPackageNames(jobs) {
    const names = new Set();
    const pkgsDir = path.join(GITHUB_WORKSPACE, 'packages');
    if (!fs.existsSync(pkgsDir)) return [];
    const knownDirs = fs.readdirSync(pkgsDir)
      .filter(d => fs.statSync(path.join(pkgsDir, d)).isDirectory());
    for (const job of jobs) {
      const match = job.name.match(/\(([^)]+)\)/);
      if (match && knownDirs.includes(match[1])) names.add(match[1]);
      for (const step of (job.steps || [])) {
        for (const d of knownDirs) {
          if (step.name.toLowerCase().includes(d)) names.add(d);
        }
      }
    }
    return [...names];
  }

  function readPackageConfig(pkgName) {
    const p = path.join(GITHUB_WORKSPACE, 'packages', pkgName, 'package.toml');
    if (!fs.existsSync(p)) return null;
    const raw = fs.readFileSync(p, 'utf-8');
    const name = raw.match(/^name\s*=\s*"([^"]+)"/m)?.[1];
    const ver = raw.match(/^version\s*=\s*"([^"]+)"/m)?.[1];
    const build = raw.match(/^build\s*=\s*"([^"]+)"/m)?.[1];
    const toolchain = raw.match(/^toolchain\s*=\s*"([^"]+)"/m)?.[1];
    const buildDeps = raw.match(/\[depends\]\s*\nbuild\s*=\s*\[([\s\S]*?)\]/)?.[1];
    const upstreamRepo = raw.match(/^repo\s*=\s*"([^"]+)"/m)?.[1];
    return { name, version: ver, build, toolchain, buildDeps, upstreamRepo, raw };
  }

  function getGitHistory(pkgName) {
    const log = exec(`git log --oneline -15 -- packages/${pkgName}/`);
    return log || 'No recent changes';
  }

  function searchCodebase(patterns) {
    const results = [];
    for (const p of patterns) {
      if (p.length < 3) continue;
      const out = exec(`git grep -in "${p.replace(/"/g, '\\"')}" -- packages/ 2>/dev/null || true`);
      if (out) results.push(`### "${p}"\n${out.slice(0, 1000)}`);
    }
    return results.join('\n\n') || 'No relevant matches found.';
  }

  async function getFailedLogs(github, context, runId) {
    const repo = process.env.GITHUB_REPOSITORY;
    const ghResult = exec(`gh run view ${runId} --repo ${repo} 2>&1 || true`, { timeout: 30000 });
    core.notice(`gh view: ${(ghResult || '').slice(0, 500)}`);
    const ghLog = exec(`gh run view ${runId} --log --repo ${repo} 2>&1 || true`, { timeout: 30000 });
    core.notice(`gh log size: ${(ghLog || '').length}`);
    if (ghLog && ghLog.length > 50) {
      const lines = ghLog.split('\n').filter(l => l.trim());
      const failedLines = lines.filter(l => /E:|error|failed|not found/i.test(l));
      core.notice(`gh log found ${failedLines.length} error lines`);
      if (failedLines.length > 0) {
        const snippet = failedLines.slice(0, 20).join('\n');
        core.notice(`gh error snippet: ${snippet.slice(0, 500)}`);
        return ghLog.slice(0, 5000);
      }
    }

    try {
      const { data: jobs } = await github.rest.actions.listJobsForWorkflowRun({
        ...context.repo, run_id: runId,
      });
      core.notice(`total jobs: ${jobs.jobs.length}`);
      const failedJobs = jobs.jobs.filter(j => j.conclusion === 'failure');
      core.notice(`failed jobs: ${failedJobs.length}`);

      const token = process.env.GITHUB_TOKEN;
      core.notice(`GITHUB_TOKEN set: ${!!token}`);

      if (!token) return ghLog || '';

      let allLogs = '';
      for (const job of failedJobs) {
        try {
          core.notice(`fetching logs for job ${job.id} (${job.name?.slice(0, 50)})`);
          const url = `https://api.github.com/repos/${context.repo.owner}/${context.repo.repo}/actions/jobs/${job.id}/logs`;
          const resp = await fetch(url, {
            headers: { Authorization: `Bearer ${token}` },
            redirect: 'follow',
          });
          core.notice(`fetch status: ${resp.status} ${resp.statusText}`);
          if (!resp.ok) {
            const errText = await resp.text().catch(() => '');
            core.notice(`fetch error body: ${errText.slice(0, 200)}`);
            continue;
          }
          const text = await resp.text();
          core.notice(`fetch log size: ${text.length}`);
          const lines = text.split('\n').filter(l => l.trim());
          if (lines.length > 100) {
            allLogs += lines.slice(0, 100).join('\n') + '\n... [truncated]\n';
          } else {
            allLogs += text + '\n';
          }
        } catch (e) {
          core.warning(`Failed to download logs for job ${job.id}: ${e.message}`);
        }
      }
      if (allLogs) return allLogs;
      return ghLog || '';
    } catch (e) {
      core.warning(`Failed to list jobs: ${e.message}`);
      return ghLog || '';
    }
  }

  function getRecentChanges() {
    const diff = exec('git diff --name-only HEAD~5 HEAD 2>/dev/null || true');
    const msg = exec('git log --oneline -5 HEAD 2>/dev/null || true');
    return { changedFiles: diff, recentCommits: msg };
  }

  async function findSimilarIssues(github, context, errorType, pkgNames) {
    try {
      const { data: issues } = await github.rest.issues.listForRepo({
        ...context.repo, state: 'open', labels: 'ci-failure', per_page: 20
      });
      return issues.filter(i =>
        pkgNames.some(p => i.title.includes(p)) ||
        i.body?.includes(errorType)
      ).map(i => ({ number: i.number, title: i.title }));
    } catch {
      return [];
    }
  }

  const jobs = await getFailedJobs(github, context);
  const { data: run } = await github.rest.actions.getWorkflowRun({
    ...context.repo, run_id: context.runId,
  });

  const pkgNames = extractPackageNames(jobs, run);
  const logSnippet = await getFailedLogs(github, context, context.runId);

  const errorInfo = categorizeError(logSnippet, jobs);
  const diffInfo = getCommitDiff();

  const changedFiles = [
    ...(run.head_commit?.modified || []),
    ...(run.head_commit?.added || []),
    ...(run.head_commit?.removed || []),
  ];
  const blameInfo = getBlameInfo(changedFiles);

  const buildTimes = getBuildTimes(run);

  const deps = {};
  for (const n of pkgNames) deps[n] = getDependents(n);

  const similarIssues = await findSimilarIssues(github, context, errorInfo.type, pkgNames);

  const allLabels = ['ci-failure', errorInfo.label];
  const severityLabel = errorInfo.severity >= 4 ? 'severity:critical'
    : errorInfo.severity >= 3 ? 'severity:high' : 'severity:medium';

  const { data: issue } = await github.rest.issues.create({
    ...context.repo,
    title: `CI Failed: ${context.workflow} #${context.runId}`,
    body: buildIssueBody(jobs, run, pkgNames, errorInfo, similarIssues, buildTimes, deps),
    labels: [...new Set(allLabels)],
  });
  core.notice(`Created issue #${issue.number}`);

  try {
    await github.rest.issues.addLabels({
      ...context.repo, issue_number: issue.number,
      labels: [severityLabel],
    });
  } catch {}

  const pkgLabels = pkgNames.map(n => 'package:' + n);
  if (pkgLabels.length) {
    try {
      await github.rest.issues.addLabels({
        ...context.repo, issue_number: issue.number,
        labels: pkgLabels,
      });
    } catch {}
  }

  if (!apiKey) {
    core.notice('No AI_API_KEY set, skipping AI analysis');
    return;
  }

  const instructions = loadInstructions();
  if (!instructions) {
    core.warning('scripts/ai-instructions.md not found, skipping AI analysis');
    return;
  }

  const pkgs = pkgNames.map(n => readPackageConfig(n)).filter(Boolean);
  const gitHistories = {};
  for (const n of pkgNames) gitHistories[n] = getGitHistory(n);

  const errorKeywords = jobs.flatMap(j => {
    const kw = [];
    for (const s of (j.steps || [])) {
      if (s.conclusion === 'failure') kw.push(s.name.split(':')[0].split(/\s+/).filter(w => w.length > 3).slice(0, 3).join(' '));
    }
    return kw;
  }).filter(Boolean);
  const codeMatches = searchCodebase([...new Set(errorKeywords)]);

  const changes = getRecentChanges();
  const commitMsg = (run.head_commit?.message || 'N/A').split('\n')[0];
  const changedFilesList = [...new Set(changedFiles)].join('\n') || 'N/A';

  const userPrompt = [
    `CI workflow "${context.workflow}" failed. Repository: ${context.serverUrl}/${context.repo.owner}/${context.repo.repo}`,
    `Branch: ${context.ref} | Commit: ${context.sha}`,
    `Commit message: ${commitMsg}`,
    ``,
    `### Error info\nType: ${errorInfo.type} (severity: ${errorInfo.severity})`,
    ``,
    `### Files changed (last 5 commits)\n${changes.changedFiles || 'N/A'}`,
    ``,
    `### Recent commits\n${changes.recentCommits || 'N/A'}`,
    ``,
    `### Failed jobs\n${jobs.map(j => `- ${j.name}: ${j.conclusion}${j.steps ? j.steps.filter(s => s.conclusion === 'failure').map(s => `\n  → ${s.name}`).join('') : ''}`).join('\n')}`,
    ``,
    pkgs.length ? `### Package configs\n${pkgs.map(p => [
      `Package: ${p.name || '?'}`,
      `Version: ${p.version || '?'}`,
      `Upstream: ${p.upstreamRepo || '?'}`,
      `Build: ${p.build || '?'}`,
      `Toolchain: ${p.toolchain || 'default'}`,
      `Build deps: ${p.buildDeps ? p.buildDeps.replace(/\n/g, ' ').replace(/"/g, '') : '?'}`,
    ].join('\n')).join('\n\n')}` : '',
    ``,
    pkgNames.length ? `### Git history for affected packages\n${pkgNames.map(n => `${n}:\n${gitHistories[n]}`).join('\n\n')}` : '',
    ``,
    codeMatches ? `### Code search results\n${codeMatches}` : '',
    ``,
    logSnippet ? `### Failed step logs\n\`\`\`\n${logSnippet}\n\`\`\`` : '',
    ``,
    changedFilesList !== 'N/A' ? `### Files changed in this commit\n${changedFilesList}` : '',
    ``,
    diffInfo.stat ? `### Diff stat\n${diffInfo.stat}` : '',
    ``,
    blameInfo.length ? `### Blame info\n${blameInfo.map(b => `${b.file}: ${b.lastCommit}`).join('\n')}` : '',
    ``,
    buildTimes.length ? `### Build step times\n${buildTimes.map(t => `${t.name}: ${t.duration}s`).join('\n')}` : '',
    ``,
    similarIssues.length ? `### Similar open issues\n${similarIssues.map(i => `#${i.number}: ${i.title}`).join('\n')}` : '',
    ``,
    `Phân tích nguyên nhân failure và đề xuất cách fix cụ thể.`,
    `Nếu log không đủ, nêu rõ cần xem log chi tiết step nào.`,
  ].filter(Boolean).join('\n');

  try {
    const analysis = await callAI(instructions, userPrompt);
    if (analysis) {
      await github.rest.issues.createComment({
        ...context.repo,
        issue_number: issue.number,
        body: `## 🤖 AI Analysis\n**Model:** ${model}\n**Error:** ${errorInfo.type} (severity: ${errorInfo.severity})\n\n${analysis}`,
      });
      core.notice('AI analysis posted to issue');
    }
  } catch (e) {
    core.error(`AI analysis failed: ${e.message}`);
    await github.rest.issues.createComment({
      ...context.repo,
      issue_number: issue.number,
      body: `⚠️ AI analysis failed: \`${e.message}\``,
    });
  }
};

async function getFailedJobs(github, context) {
  const { data } = await github.rest.actions.listJobsForWorkflowRun({
    ...context.repo, run_id: context.runId,
  });
  return data.jobs.filter(j => j.conclusion === 'failure');
}

function buildIssueBody(jobs, run, pkgNames, errorInfo, similarIssues, buildTimes, deps) {
  const lines = [
    `## ❌ CI Build Failure`,
    ``,
    `**Error:** ${errorInfo.type} (severity: ${errorInfo.severity})`,
    `**Run:** [${run.id}](${run.html_url})`,
    `**Branch:** \`${run.head_branch}\``,
    `**Commit:** \`${run.head_sha}\``,
    ``,
    `### Failed Jobs`,
    ...jobs.map(j => {
      const steps = (j.steps || []).filter(s => s.conclusion === 'failure').map(s => `  - \`${s.name}\``).join('\n');
      return `- **${j.name}**\n${steps}`;
    }),
    ``,
    pkgNames.length ? `### Affected Packages\n${pkgNames.map(n => {
      const dependents = deps[n];
      const depText = dependents?.length ? ` → affects ${dependents.join(', ')}` : '';
      return `- \`${n}\`${depText}`;
    }).join('\n')}` : '',
    ``,
    buildTimes.length ? `### Build Step Times\n${buildTimes.map(t => `- ${t.name}: \`${t.duration}s\``).join('\n')}` : '',
    ``,
    similarIssues.length ? `### Related Open Issues\n${similarIssues.map(i => `- #${i.number}: ${i.title}`).join('\n')}` : '',
    ``,
    `---`,
    `*Đang chờ AI analysis...*`,
  ];
  return lines.filter(Boolean).join('\n');
}

function loadInstructions() {
  const p = path.join(process.env.GITHUB_WORKSPACE, 'scripts', 'ai-instructions.md');
  try {
    return fs.readFileSync(p, 'utf-8');
  } catch {
    return null;
  }
}

async function callAI(systemPrompt, userPrompt) {
  const resp = await fetch(apiUrl, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${apiKey}`,
    },
    body: JSON.stringify({
      model,
      messages: [
        { role: 'system', content: systemPrompt },
        { role: 'user', content: userPrompt },
      ],
      stream: false,
      temperature: 0.1,
      max_tokens: 2000,
    }),
  });

  if (!resp.ok) {
    const errText = await resp.text();
    throw new Error(`API ${resp.status}: ${errText.slice(0, 200)}`);
  }

  const data = await resp.json();
  return data.choices?.[0]?.message?.content ?? data.message?.content;
}
