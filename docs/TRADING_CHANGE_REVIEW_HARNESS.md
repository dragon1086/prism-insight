# Trading Change Review Harness

Use this checklist before changing screening, regime, entry, exit, or position-sizing behavior.
It is a development review harness, not a runtime trading component.

## BTC roadmap checkpoint

For BTC work, first read [BTC_ROADMAP_ko.md](BTC_ROADMAP_ko.md).
Record the milestone/task ID, dependencies, acceptance evidence, and rollback scope.
Prioritize unresolved execution/protection defects over parameter optimization.
Do not treat more timeframes, a passing test suite, or a higher backtest return as
proof of live safety or profitability. Use the linked profitability contract.
After work, update the roadmap only where evidence changed; explicitly separate
code/test/deployment/forward-validation states and identify the next task.
Memory stores the pointer and principles, not a competing copy of roadmap status.
This checkpoint adds no runtime process, network query, or LLM call to trading.

## Review lenses

- **William O'Neil / CAN SLIM**: market direction, leadership, accumulation versus
  distribution, proper entry, and cutting losses without truncating winners.
- **Mark Minervini**: trend template, volatility contraction, extension and
  climax risk, and precise entry quality.
- **Stanley Druckenmiller**: regime, liquidity, macro inflection, and whether the
  rule fits the current market rather than only a long-term trend label.
- **Warren Buffett**: business quality, durability, valuation, and the risk of a
  price-only signal selecting a weak underlying business.
- **Quant risk manager**: sample size, expectancy, profit factor, drawdown,
  parameter sensitivity, multiple testing, and out-of-sample stability.
- **Systems reviewer**: operational simplicity, deterministic behavior,
  observability, rollback, and failure modes across KR and US pipelines.

The lenses challenge a proposal; persona consensus does not replace production
data or a falsifiable test.

## Required sequence

1. **State the contract and scope.** Name the market, trigger, regime, and
   behavior the rule is supposed to identify.
2. **Reproduce with production evidence.** Join the decision with its market,
   candidate, entry, exit, and outcome context. Separate code versions where
   possible.
3. **Try to falsify the proposal.** Check the same pattern in other triggers,
   regimes, and profitable counterexamples before calling it universally bad.
4. **Compare smaller alternatives.** Consider no change, logging only,
   screening-only, deterministic gate, and prompt guidance in that order.
5. **Change one decision layer.** Do not encode the same penalty in screening,
   a buy gate, and an LLM prompt unless each layer has a distinct measured job.
6. **Protect both sides with tests.** Add one rejected counterexample and one
   valid retained example. Verify unrelated triggers remain unchanged.
7. **Make the effect observable and reversible.** Log a stable reason code,
   preserve enough identifiers for later outcome analysis, and keep rollback
   to one small code change or feature flag.

## Gotcha: local evidence is not a global gate

A pattern that is harmful inside one trend-following trigger can be profitable
inside reversal, breakout, or capital-inflow triggers. Never promote a single
trade, candle shape, or trigger-local result into a global hard gate or prompt
penalty until cross-trigger and cross-regime counterexamples have been tested.

Prefer a narrow trigger-contract repair when it explains the failure. Expand
the rule only after a larger, version-aware sample shows the same effect.
