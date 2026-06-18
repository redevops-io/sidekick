Resolve the in-progress upstream merge in this LibreChat fork.

CONTEXT
- This repo is arybach's fork of danny-avila/LibreChat. The fork carries deliberate
  customizations that MUST be preserved, notably:
    * the entire `search-aggregator/` subsystem (marketplace scrapers, alerts, notifications),
    * self-hosted model configuration — custom endpoints in `librechat.yaml`, `.env*`,
      `docker-compose*.yml`, and any `deploy/`/`charts/` overrides that point LibreChat at a
      local OpenAI-compatible server instead of the cloud providers,
    * OIDC / auth integration changes (e.g. `src/tests/oidc-integration.test.ts` and related).
- A merge of upstream `main` has already been started and committed WITH CONFLICT MARKERS
  still present (a real 2-parent merge commit). Your job is to resolve every conflict.

WHAT TO DO
1. Find all files that still contain conflict markers (`<<<<<<<`, `=======`, `>>>>>>>`).
2. For each conflicted file, produce the correct merged result:
   - Take upstream's improvements (bug fixes, new features, dependency bumps, refactors).
   - KEEP the fork's customizations listed above. When upstream and the fork changed the
     same lines, integrate both intents — do not blindly discard either side. Never delete
     the fork's self-hosted endpoint config or the `search-aggregator/` wiring just to make
     a conflict go away.
   - For lockfiles (`package-lock.json`, `yarn.lock`, `bun.lockb`): prefer regenerating from
     the merged `package.json` if a package manager is available; otherwise take upstream's
     lockfile and note it.
3. Remove ALL conflict markers. The tree must contain zero markers when you finish.

ACCEPTANCE (must pass)
- `git diff --check` reports no conflict markers.
- `grep -rIl -e '^<<<<<<<' -e '^>>>>>>>' --exclude-dir=node_modules --exclude-dir=.git .`
  returns nothing (no remaining markers anywhere).
- Any package.json you touched is valid JSON (`node -e "require('./<path>')"` or
  `node -e "JSON.parse(require('fs').readFileSync('<path>'))"`).

DO NOT
- Do not run `git merge`, `git rebase`, `git checkout <branch>`, `git push`, or `git reset`.
  The merge is already in place; you only edit files to resolve it. Finalizing and pushing
  is done by the operator after review.
- Do not touch files that have no conflict markers unless required to keep the tree coherent.
