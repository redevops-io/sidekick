# Merge upstream LibreChat into the fork — driven by the local self-hosted agent

The flagship task for sidekick's **`selfhosted`** branch: keep arybach's
[LibreChat](https://github.com/arybach/LibreChat) fork current with upstream
[danny-avila/LibreChat](https://github.com/danny-avila/LibreChat) **without losing the
fork's customizations** (the `search-aggregator/` subsystem, OIDC changes, and the
self-hosted model endpoint config) — with all inference running on the local **evo-x2
(Strix Halo)** box via vLLM/llama.cpp. No cloud key, no tokens leaving the machine.

## Run it

```bash
# 0. serve the model on the evo-x2 box (OpenAI /v1 on :8000)
just -f scripts/serve_vllm.justfile fetch          # one-time: download the q4 GGUF
just -f scripts/serve_vllm.justfile serve-llamacpp  # recommended on Strix Halo
just -f scripts/serve_vllm.justfile health          # confirm it's up

# 1. install sidekick once (puts `sidekick` on PATH)
uv tool install /mnt/backup/projects/sidekick

# 2. drive the merge (fetch → merge → agents resolve → you review/push)
examples/merge-librechat-upstream/run.sh
```

## How it works

`run.sh` does the git plumbing the agents are *not* allowed to do (the agent's bash is
scoped to read/build/lint/`git status|diff|add|log` — never `merge`/`checkout`/`push`):

1. fetch upstream + fork, branch `merge/upstream-<date>` off the fork's `main`,
2. `git merge` upstream `main`; on conflict, commit the half-merged state as a real
   2-parent **merge commit** (markers and all) so sidekick's per-agent worktrees — which
   branch off `HEAD` — actually contain the conflicts,
3. `sidekick run` fans out the local Qwen agent(s); each resolves conflict markers in its
   worktree following [`TASK.md`](TASK.md), preserving the fork's customizations, and its
   acceptance check confirms **zero markers remain**; green branches merge back,
4. you review (`git diff --check`, full CI) and `git push` — sidekick never pushes.

## Files

| File | Role |
|------|------|
| [`TASK.md`](TASK.md) | the prompt + acceptance checks handed to the agent(s) |
| [`run.sh`](run.sh)   | the fetch/merge/drive/finalize harness |

See [`../../scripts/serve_vllm.justfile`](../../scripts/serve_vllm.justfile) for the model
server recipes and [`../../scripts/selfhosted.env.example`](../../scripts/selfhosted.env.example)
for the backend env vars.
