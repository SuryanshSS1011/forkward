# CLAUDE.md for Forkward (HackPSU Fall 2026)

## What we are building

Forkward forwards every upstream fix to a company's fork. A self-improving team of agents resolves the merge conflicts the fork hits when it pulls in upstream, a safety gate decides which resolutions may apply on their own, and everything else waits for a person in a pull request. The benchmark is real Linux kernel vendor forks (NXP, STMicroelectronics, Rockchip), and the answer key is what each vendor's own engineers committed.

The story is that a fix upstream is not yet a fix in your fork. Device kernels fall 29 to 158 stable releases behind, so security and stability fixes stall. The same engine applies to any long-lived fork, such as Android vendor kernels, browser forks, database forks and the internal forks most companies carry.

## The event

1. The Devpost submission must exist by 12:00 AM Sunday, October 25. The hard deadline is 12:00 PM Sunday, and editing closes when hacking ends at 1:45 PM.
2. The video is at most 5 minutes and covers who we are, why we built it, how it works and what comes next.
3. All code is in a public GitHub repository linked from the submission, and every team member is listed.
4. At least one team member presents at the judging expo.
5. Judging weighs implementation, clarity, creativity, growth and impact. Growth asks what the team learned during the event, so the Devpost names what was built this weekend.
6. No em dashes in any user-facing text.

## Tracks

We enter the HackPSU Grand Prize, the Research Track, the Nittany AI Challenge and these MLH prizes: Gemini API, Solana, Tiger Data, Vultr, Auth0 and GoDaddy Registry. We skip ElevenLabs, because voice has no real role in reviewing merge conflicts. We enter the SpaceXSI raffle only if we actually build with Cursor and Grok Bot. We do not enter the AI Strategy Case Competition, the Mystery Stack general track or the FNB Challenge.

## Data (frozen before the event)

The data lives in a separate repository, checked out at `../forkward-data` or wherever `DATA_DIR` points. `split.json` holds the selection rule and a SHA-256 for every file. Verify the hashes before every evaluation run.

| Split | Vendor and merges | Scorable hunks | Keep vendor | Take upstream | Merge both |
|---|---|---|---|---|---|
| Train | NXP lf-6.6.y, 11 stable merges, 2023 to 2024 | 70 | 52 | 7 | 11 |
| Validation | ST v6.6-stm32mp, r1.2 to r3.1 | 45 | 25 | 6 | 14 |
| Test | Rockchip develop-6.6, v6.6.22 to v6.6.89, 2025 | 71 | 51 | 4 | 16 |

1. `<vendor>_blocks.json` holds what an agent may see for each hunk, which is the conflict, 30 lines of context on each side, the upstream change and the vendor change near the conflict.
2. `<vendor>_key.json` holds the reference resolution in `gt`. Only `score.py` may read key files. No agent prompt may ever contain a `gt` field.
3. A hunk with `gt: null` has no unique reference and is excluded from scoring.
4. Raspberry Pi merged 65 consecutive weekly stable releases into rpi-6.12.y with zero conflicts. That shows continuous syncing makes the problem nearly disappear.
5. The Rockchip test split was already evaluated once, before this event. A new held-out claim needs a new vendor, or it must be labeled as a second evaluation.

## Definitions

Every number on the site, the board and in the video uses these exact definitions.

1. **Hunk.** One conflict block between `<<<<<<<` and `>>>>>>>` in a file produced by re-running the vendor's real merge.
2. **Correct.** The resolution equals the vendor's reference after stripping leading and trailing whitespace from each line and dropping blank lines. This is strict, so a defensible alternative counts as wrong.
3. **Class.** Keep vendor means the reference equals the vendor side, take upstream means it equals the upstream side, and merge both means neither. Every metric is reported per class, because always keeping the vendor side already scores 71.8% on the test split.
4. **Accuracy.** Correct hunks divided by scorable hunks.
5. **Accepted.** A hunk the system would merge with no human review, because it passed the safety gate.
6. **Coverage.** Accepted hunks divided by scorable hunks.
7. **Escaped error rate.** Wrong hunks among accepted hunks. This is the safety number.
8. **Certified bound.** The one-sided 95% Clopper-Pearson upper bound on the escaped error rate, computed from the accepted hunks of a calibration split. With zero errors among n accepted hunks it is about 3/n.
9. **Relative improvement.** The change from the round-0 baseline to the frozen final strategy on the test split, with the same resolver model, measured on accuracy and on coverage at the certified bound. Report it with a paired bootstrap 95% interval over hunks.

## The five-nines target, stated honestly

The target is that at most 1 in 100,000 hunks that Forkward merges automatically is wrong, with 95% confidence. The system is non-deterministic, so the guarantee is statistical and applies to accepted hunks only. Everything the gate rejects goes to a person.

1. Certifying a 0.001% error rate at 95% confidence needs about 300,000 accepted hunks with zero errors (n ≥ ln(20) / 0.00001). Today's 186 hunks can certify roughly a 5% to 10% bound at best.
2. A shared sync service is the only realistic way to collect 300,000 verified resolutions. Every customer's merges add calibration data for everyone.
3. The calibration and test splits come from different vendors, so the guarantee assumes they behave alike. The test-vendor escaped error rate is the check on that assumption.
4. Never claim five nines today. Claim the bound actually certified and show the path.

## Design: search, then measure

1. **A small resolution algebra.** Deterministic operators generate candidates without any model call. They are keep vendor, take upstream, vendor then upstream, upstream then vendor, apply the upstream delta onto the vendor side, apply the vendor delta onto the upstream side, and empty.
2. **Many proposers.** LLM personas add free-form candidates, and the team scores every candidate rather than trusting one answer.
3. **Selection by measurement.** A candidate is chosen by agreement across independent proposers and by the learned playbook, and the scorer decides which strategies survive.
4. **A shared cache.** A resolution recorded once is reused when the same upstream change conflicts again, in the way `git rerere` reuses resolutions.

## Architecture

Keep each module in its own file with a narrow interface.

1. **`forkward/data.py`.** Loads blocks and verifies `split.json` hashes. It never loads key files for agents.
2. **`forkward/llm.py`.** Model client for Luminal, DeepSeek, local OpenAI-compatible servers and Anthropic, with retries, backoff, timeouts, token accounting and token log-probabilities where the endpoint supports them. New providers go in `provider()`.
3. **`forkward/algebra.py`.** The deterministic operators above. Each returns a candidate and the operator name.
4. **`forkward/agents.py`.** The classifier, the resolver personas, the reflector, and the curator with its leakage filter.
5. **`forkward/score.py`.** Normalized-line match, class labels, accuracy, coverage, escaped error, Clopper-Pearson bounds and the paired bootstrap. It is the only module that reads key files.
6. **`forkward/gate.py`.** The safety gate. A hunk is accepted only if the chosen candidate wins a clear vote across independent proposers including at least one deterministic operator, the classifier agrees with the candidate's class, and the confidence clears a threshold calibrated on validation per model.
7. **`forkward/search.py`.** The round controller, with the round-0 baseline, the strategy population, the Pareto front and the promotion gate.
8. **`forkward/store.py` and `forkward/db.py`.** JSONL under `runs/`, mirrored to Postgres. Product data lives in Postgres with forced row-level security.
9. **`forkward/sync.py`, `forkward/workflows.py` and `forkward/mcp_server.py`.** The fork-sync engine, the durable DBOS jobs and the MCP interface.
10. **`frontend/`.** The Next.js site and web app. The avatar is `frontend/src/components/AgentMark.tsx`.
11. **`dashboard/`.** The agent observability board.

## Anti-cheating rules

1. Agents never see key files, test blocks during optimization, or test output.
2. The test split is evaluated once, after the strategy is frozen, with the frozen strategy id logged.
3. The curator's leakage filter rejects lessons containing any file path, function name, identifier with an underscore or capital inside it, or hex value from any split.
4. The training curve is labeled "best strategy on train." Improvement claims come only from held-out data.
5. Every number names the model that produced it.

## Working conventions

1. Prefer the simplest thing that runs. No abstractions for problems we do not have.
2. Long runs go in the background, log to `runs/<timestamp>/`, and write events to Postgres when `DATABASE_URL` is set. Never block the session on a run longer than 2 minutes.
3. Every external failure becomes a logged attempt result. One bad hunk must never crash a round.
4. Commit each working step with a message naming what now works.
5. Run `uv run pytest`, `uv run ruff check .` and, in `frontend/`, `npm run typecheck` and `npx vitest run` before pushing.

## Out of scope this weekend

1. Compiling kernel code or cloning kernel trees at the venue.
2. Fine-tuning or weight updates. All learning lives in the playbook and the strategy population.
3. Claims of a five-nines guarantee.

