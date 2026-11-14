# Optiver x ETH coding challenge

## Goal and priorities
- This is a short Optibook trading competition. Optimize for useful, verified progress and code the team understands.
- Build a working baseline early, preserve a known working version, and improve it through small experiments.
- Event-day challenge instructions determine instruments, scoring, limits, and permitted tools. Verify them rather than importing assumptions from other events.

## Autonomy and checkpoints
- Implement, debug, and run offline checks autonomously within the agreed task and strategy.
- Before changing strategy, risk policy, or making a substantial addition, pause for Pascal's approval. Present the proposed behavior, reason, and main tradeoff in at most three bullets.
- Strategy changes include fair-value models, inventory skew, order sizing, and hedging rules. Risk changes include position limits and exposure controls. Substantial additions include a new subsystem or broad refactor.
- Once approved, complete that scope without repeatedly asking. Parameter experiments within an explicitly approved range need no further checkpoint.
- Fixing code to match an agreed rule can proceed autonomously. Flag ambiguity if a proposed fix would choose a new trading rule.
- Pascal controls starting trading bots. Never start or restart one, including through imports, tests, notebooks, background processes, or order-writing smoke tests. Prepare commands for Pascal to run.

## Keep the code understandable
- Make one coherent improvement at a time, with a diff a teammate can review in a couple of minutes where practical.
- Prefer straightforward Python functions and an obvious execution path. Add dependencies, concurrency, abstractions, or infrastructure only for a concrete current need.
- Stay within the assigned scope. Avoid speculative features, unrelated cleanup, and broad rewrites.
- Name trading quantities clearly and document units, sign conventions, and non-obvious assumptions beside formulas.
- Assume Pascal knows Python and basic market making. Explain new trading logic and failure modes; skip basic Python explanations.
- After meaningful trading-code changes, briefly explain what triggers an order, how its price and size are chosen, and what limits exposure. Link to the relevant code.

## Communication
- Before meaningful implementation, describe the intended behavior and approach in one or two sentences. Routine fixes need no separate plan.
- Keep updates brief and report new findings, decisions, or blockers rather than narrating tools.
- Default completion messages to about 100 words: changed behavior, checks and results, and consequential uncertainty or decisions. Omit empty sections.
- Expand when Pascal asks or a consequential decision needs explanation. Never hide failed checks, unverified behavior, or exposure changes to meet a word budget.

## Repository and commands
- `src/`: competition implementation. `src/main.py` is the entry point (dry run by default, `--live` sends orders); the strategy plug-in point is `src/strategy.py`.
- `scripts/`: one-off manual scripts.
- `docs/README.md`: links to official Optibook documentation. Read the relevant API sections before implementing calls; verify discrepancies against event materials or the supplied client.
- `references/README.md`: guide to reference snapshots. Read only relevant pieces, adapt small understood parts, and preserve attribution and licenses. Do not modify snapshots as part of strategy work.
- Python 3.11 is pinned in `.python-version`; dependencies are managed by `uv` and `pyproject.toml`.
- Install/sync the environment: `uv sync`.
- Syntax check without executing the bot: `uv run python -m compileall -q src`.
- Offline tests (fake exchange, no `optibook` needed): `uv run pytest -q`. `optibook` is only available in the event's web VS Code, so live runs happen there. Do not claim that syntax checks or offline tests validate live trading behavior.
- Keep credentials out of code, commits, and logs.

## Trading correctness and verification
- Confirm instrument metadata, tick sizes, permitted order types, position/order limits, and request limits before relying on them. Never invent competition limits.
- Verify venue-specific amendment priority, cancellation semantics, self-trade handling, and disconnect behavior before relying on them.
- Check worst-case exposure if outstanding, pending, and proposed orders fill. Do not assume opposite-side orders cancel the risk.
- Respect tick alignment, valid order sizes, and request budgets.
- Track fills independently of order status, including partial fills and fills during cancellation. Cancelling an order does not undo fills.
- Treat ambiguous order responses as unknown state. A timeout does not prove rejection; reconcile before retrying so retries cannot duplicate exposure.
- Handle missing/empty books and uncertain connection or order state explicitly. Do not silently continue normal quoting with unknown exposure.
- Define how stale or missing market-data inputs affect quoting under the approved strategy. Do not treat the last observed book as indefinitely valid.
- Evaluate immediate hedges using the executable side of the book and available volume, including fees where applicable. A midpoint is a valuation reference, not a guaranteed execution price.
- For multi-leg trades, assume one leg can fill while another fails unless the venue guarantees atomic execution. Apply approved exposure controls throughout intermediate states.
- Keep stopping and cancelling quotes straightforward. Cancellation and inventory liquidation are separate actions; do not assume one implies the other.
- Test relevant risk invariants and strategy calculations offline, including boundary cases. Use the smallest useful checks; do not build a full simulator without a demonstrated need.
- For strategy experiments, state the hypothesis and change one meaningful factor where practical. Compare with the baseline and note differing market conditions; one profitable run is insufficient evidence.
- Keep a tiny experiment record when experiments occur: change, parameters, observation, and decision. Include inventory and relevant execution context alongside P&L: profits may leave substantial inventory, and frequent fills may reflect adverse selection. Collect only what helps evaluate the current experiment, and separate measured results from expectations.
- Prioritize strategy correctness and reliable execution. Optimize latency only when measurements show it matters.

## Working with teammates and agents
- Two people and potentially multiple agents work here. Establish task scope and file ownership before parallel edits; coordinate overlapping changes.
- Prefer separate worktrees for independent coding tasks when practical. In a shared checkout, coordinate Git operations and recheck the index before committing.
- Preserve others' uncommitted work. Never overwrite, revert, or include it merely to finish your task. Inspect unexpected changes and coordinate if they overlap.
- Give handoffs a short description of changed behavior, relevant files, checks, and remaining uncertainty.
- Coordinate one active bot process per trading account. Do not assume parallel agents should run parallel bots; Pascal owns bot startup.

## Git and working milestones
- Automatically commit completed, working milestones after relevant checks pass; no separate approval is needed for the commit itself.
- A milestone is one coherent, usable change. If verification is blocked, report the limitation and do not describe the milestone as verified.
- Review the diff and index before committing. Stage only files or hunks belonging to the assigned task; never blindly stage everything or sweep in prepared/untracked teammate files.
- Use Conventional Commits, e.g. `feat(strategy): add approved inventory skew` or `fix(risk): count outstanding bids in exposure`.
- Preserve the configured human author identity. Do not add AI co-author trailers, AI signatures, or generated-by attribution to commit messages.
- Do not amend others' commits, rewrite shared history, or push unless requested.
