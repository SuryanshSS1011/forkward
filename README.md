# Forkward

Every upstream fix, forwarded to your fork.

## Why

A fix upstream is not yet a fix in your fork. Device kernels fall 29 to 158 stable releases behind,
because every sync means hand-resolving merge conflicts, so security and stability fixes stall. The
same is true of any long-lived fork: Android vendor kernels, browser forks, database forks and the
internal forks most companies carry.

## What it does

Forkward pulls upstream into your fork, resolves the conflicts, and opens a pull request.

1. A team of agents proposes a resolution for every conflict hunk.
2. A safety gate decides which resolutions may apply on their own.
3. Everything else waits for a person in the pull request, with each side, the proposal and every
   alternative shown.

It runs as a CLI, an MCP server for coding agents, and a hosted service with a GitHub App that
syncs on a schedule.

## Why not just ask an LLM

A single model answer is a guess with no error rate. Forkward searches, then measures.

- **Deterministic candidates first.** A small resolution algebra (keep vendor, take upstream, both
  orders, each side's change replayed onto the other) generates candidates with no model call.
- **Many proposers, one vote.** Several LLM personas add candidates, and a hunk is chosen by
  agreement across independent proposers, not by trusting one.
- **The gate, not the model, decides what merges.** A hunk auto-applies only if it wins a clear vote
  that includes a deterministic operator, a classifier agrees with it, and its confidence clears a
  threshold calibrated per model on held-out data. Model text can never approve itself.
- **It learns without fine-tuning.** A reflector and curator turn mistakes into a playbook, a search
  keeps only strategies that beat the incumbent on validation, and a shared cache reuses a
  resolution whenever the same upstream change conflicts again.
- **Scored against real engineers.** The benchmark is real Linux kernel vendor merges (NXP, ST,
  Rockchip), and the answer key is what each vendor actually committed.

## Results, stated honestly

We taught Forkward on NXP's kernel merges, picked the best version on STMicroelectronics' merges,
locked it, and then ran it once on Rockchip's merges, which it had never seen. A merge conflict
counts as correct only if Forkward's resolution matches, line for line, what Rockchip's own
engineers committed.

The version we locked combines three model personas and the rule-based candidates, which vote on
each conflict, with a short list of lessons the system wrote for itself from its NXP mistakes, such
as "if the vendor's change generalizes the upstream one, take the upstream form and add the vendor's
extra logic." Every number below uses the same model, DeepSeek V4.1 Flash.

| Approach | Conflicts resolved like Rockchip did (of 71) |
|---|---|
| A single model call, no lessons | 33 (46.5%) |
| Always keep the fork's own code | 51 (71.8%) |
| Forkward | 54 (76.1%) |

- Forkward got 21 more conflicts right than a single model call.
- Most Rockchip conflicts are resolved by keeping the fork's code, so that simple rule is hard to
  beat. Forkward beats it by 3 conflicts, which is too few to be statistically meaningful yet.
- Forkward makes about four model calls per conflict, against one for the single call.
- Its safety gate has not yet earned the right to merge anything alone, so today every conflict
  goes to a person. Proving a low error rate takes far more checked resolutions than one benchmark
  holds, which is what a shared sync service collects.

Full numbers are in `results/`.

## Try it

```sh
uv sync && cp .env.example .env   # add LUMINAL_API_KEY or another provider
uv run python -m forkward sync <repo> <owner/name@ref>
```
