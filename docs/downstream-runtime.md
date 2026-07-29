# Downstream Runtime Branch

Last verified: 2026-07-29

This fork carries a deliberately small runtime patch set on top of
`NousResearch/hermes-agent`. It is a deployment branch, not a second gateway
or an alternative profile.

## Canonical topology

The stable VPS checkout is `~/.hermes/hermes-agent` with:

- branch `runtime`;
- `origin` set to `wwpinner/hermes-agent`;
- `upstream` set to `NousResearch/hermes-agent`;
- the standard Hermes virtual environment under that checkout;
- no service-level `PYTHONPATH` or `WorkingDirectory` override pointing at a
  feature worktree.

Feature worktrees are development artifacts only. The CLI, Desktop `serve`
backend, Dashboard, messaging gateway, and gateway-owned cron scheduler must
all resolve code from the stable runtime checkout.

The GitHub fork is public. Never commit config files, credentials, session
state, personal skills, private plugins, logs, infrastructure addresses, or
other user data.

## Current patch decisions

| Work family | Runtime decision | Reason |
| --- | --- | --- |
| API/async compression continuation | Carry | Current upstream did not route closed pre-compression API session IDs to a verified live continuation. |
| Closed-session persistence re-raise | Drop | Current upstream stops the turn before tool execution when incremental persistence fails and has a direct regression test. |
| Cron DST/wall-clock behavior | Carry | The preserved behavior suite reproduced 14 failures on current upstream. |
| OpenRouter account policy catalog | Carry | Current upstream used the public catalog and did not fail closed against account privacy/provider policy. |
| OpenRouter request ZDR | Carry | Current upstream did not enforce `provider.zdr=true` across primary, auxiliary, delegated, summary, and mini-SWE request paths. |
| Codex `max` to `xhigh` clamp | Drop | A bounded live GPT-5.6 Codex request succeeded through the current `ultra` to `max` wire path; the clamp would reduce requested reasoning. |
| Historical npm lock refresh | Replace | The old lock patch was stale. Compatible current DOMPurify, PostCSS, tar, fast-uri, concurrently, and shell-quote updates were resolved against the current lock instead. |

## Release procedure

1. Fetch `upstream/main` and `origin/runtime`.
2. Create an isolated integration worktree from current `upstream/main`.
3. Reproduce every carried behavior against untouched upstream. Drop a patch
   when upstream now satisfies its behavior contract.
4. Port only the remaining behavior as separate conventional commits and
   update `.downstream-runtime-base` to that fetched upstream commit.
5. Run targeted Python suites with `scripts/run_tests.sh`; run relevant JS
   checks, tests, and builds from the root workspace.
6. Obtain an immutable frontier-model review of the exact candidate commit.
7. Push the reviewed candidate to `origin/runtime` and verify the server SHA.
8. Tag the previously deployed commit before updating the stable checkout.
9. Update the stable checkout, refresh the supported gateway unit, restart
   once, and run the post-deploy checks below.

For routine upstream integration, merge current `upstream/main` into
`runtime`, resolve against the behavior tests, and push the resulting
fast-forwardable history. Do not silently rebase the deployed branch: the
stable checkout must be able to update with a fast-forward. When upstream
implements a carried patch, remove its active behavior with an explicit,
reviewed commit and delete obsolete tests only when upstream has equivalent
coverage.

Do not run plain `hermes update` in the runtime checkout because its default
branch is `main`. Use:

```bash
hermes update --branch runtime
```

The update is not operationally complete until the runtime guard passes.

## Runtime integrity gate

After fetching current remote refs, run:

```bash
python scripts/downstream_runtime_check.py \
  --repo ~/.hermes/hermes-agent \
  --branch runtime \
  --origin wwpinner/hermes-agent \
  --upstream NousResearch/hermes-agent \
  --service hermes-gateway.service \
  --fetch
```

The guard fails when:

- the checkout is dirty, detached, or on the wrong branch;
- the remotes do not match the fork topology;
- local `HEAD` differs from `origin/runtime`;
- the candidate does not contain the upstream commit pinned in
  `.downstream-runtime-base`;
- the gateway is inactive;
- the unit contains `PYTHONPATH` or a `/worktrees/` path;
- the gateway process is not launched by the stable checkout's Python.

The guard never reads process environments or credential files.

## Deployment and smoke verification

1. Back up the installed unit and all drop-ins.
2. Remove only the superseded worktree-routing drop-in.
3. Run `systemctl --user daemon-reload`.
4. Run `hermes gateway refresh` from the stable runtime checkout.
5. Restart the gateway once.
6. Verify:
   - the runtime integrity gate passes;
   - `hermes gateway status` reports active with no outdated-unit warning;
   - the process command path resolves to the stable checkout;
   - no active process has a worktree cwd or command path;
   - configured messaging adapters connect without new errors;
   - an API/Desktop session can resume through its live compression continuation;
   - async completion delivery targets the live continuation;
   - cron jobs load and their next-run calculations preserve local wall-clock semantics across DST folds and gaps;
   - OpenRouter's authenticated policy catalog loads with the configured account;
   - when `openrouter.zdr: true`, final request payloads cannot weaken `provider.zdr`;
   - Desktop/serve and Dashboard use the same stable checkout;
   - a service restart preserves the same runtime identity.

## Rollback

Before every deployment, tag the current known-good runtime commit locally and
on the fork. If post-deploy verification fails:

1. stop after the failed smoke; do not improvise destructive recovery;
2. switch the stable checkout to the known-good tag in detached mode;
3. refresh/restart the affected service using that checkout;
4. repeat the live smoke checks;
5. preserve the failed candidate and logs for diagnosis;
6. return to branch `runtime` only after a corrected reviewed commit exists.

Never delete the previous tag or reset an unclean runtime checkout.

## Known dependency residuals

The 2026-07-29 production dependency audit retains the React Router
`GHSA-qwww-vcr4-c8h2` advisory because the only patched core package is
`react-router@8.3.0`, while no matching `react-router-dom` release exists.
The advisory explicitly affects only unstable RSC APIs; Hermes Dashboard is a
Vite SPA and does not use those APIs. Do not force npm's suggested downgrade
to `react-router-dom@7.11.0` merely to clear the counter. Reassess when a
compatible patched release is published.

Build-only Electron Builder and ESLint transitive advisories likewise require
compatible parent releases or major upgrades. Keep them visible in audit
output and update the parent packages when supported; do not bypass peer
constraints with `npm audit fix --force`.
