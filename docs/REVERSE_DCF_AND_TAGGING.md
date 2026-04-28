# Reverse DCF + Data Provenance Tagging

> Investment Strategist guard rails added on `feat/reverse-dcf-and-tagging`.
> Motivation: PR #265 lowered the KR `min_score` from 5 → 4 in the **strong_bull**
> regime, so the strategist now needs an explicit valuation/data-quality defence
> against momentum-fueled overvaluation traps.

## What changed

The Investment Strategist prompt
(`cores/agents/trading_agents.py` for KR, `prism-us/cores/agents/trading_agents.py`
for US) now contains two new mandatory sections **and** two new top-level JSON
keys in the response contract:

1. **Reverse DCF Sanity Check (Mandatory)** — derive the implied revenue CAGR,
   operating margin, and discount rate that the *current* price already prices
   in, then compare them to the past 5-year actuals.
2. **Data Provenance Tagging (Mandatory)** — every numeric / factual claim in
   `valuation_analysis`, `sector_outlook`, `rationale`, and `rejection_reason`
   must carry one of `[actual]`, `[inference]`, `[assumption]`, `[unavailable]`.

Existing entry-score thresholds (6 / 7-point rules, regime-adaptive `min_score`)
are **unchanged**. The new fields only add justifications the strategist may
cite when declining a setup.

## New JSON keys

```json
{
  "reverse_dcf": {
    "implied_revenue_cagr_pct": 18.0,
    "implied_op_margin_pct": 32.0,
    "discount_rate_used_pct": 9.0,
    "past_5y_revenue_cagr_pct": 12.0,
    "past_5y_op_margin_range_pct": [22.0, 28.0],
    "verdict": "stretched",
    "comment": "Implied CAGR 1.5x past 5y; margin near historical max",
    "data_tags": {
      "implied_revenue_cagr_pct": "[inference]",
      "past_5y_op_margin_range_pct": "[actual]"
    }
  },
  "data_quality_check": {
    "actual_count": 7,
    "inference_count": 4,
    "assumption_count": 2,
    "unavailable_count": 1,
    "flag": "ok"
  }
}
```

`verdict` ∈ `{"reasonable", "stretched", "unrealistic", "insufficient_data"}`.
`flag` becomes `"too_many_assumptions"` when `assumption_count > 3`.

## How the strategist uses these

In strong-bull regimes (`min_score = 4` US / KR) the strategist may reject an
otherwise-passing setup with one of two new justifications:

* `Reverse DCF verdict = "unrealistic"` — must quote the implied CAGR vs the
  past 5y CAGR, with `[actual]` / `[inference]` tags.
* `data_quality_check.flag = "too_many_assumptions"` — must quote the
  `assumption_count`.

`"insufficient_data"` is **not** a rejection trigger on its own — the LLM is
told not to penalise honest data gaps.

## Discount rate convention

* KR equity: 9% (slightly higher equity risk premium)
* US equity: 8%
* Terminal growth: 2.5% in both markets

## Files touched

* `cores/agents/trading_agents.py` — KR strategist (KO + EN blocks)
* `prism-us/cores/agents/trading_agents.py` — US strategist (KO + EN blocks)
* `tests/test_trading_agents_prompt_rules.py` — KR prompt-rule tests (+8)
* `prism-us/tests/test_trading_agents_prompt_rules.py` — US prompt-rule tests (+8)

## Verification

```bash
pytest tests/test_trading_agents_prompt_rules.py \
       prism-us/tests/test_trading_agents_prompt_rules.py -v
# 20 passed
```

The test suite locks down every section header, verdict band, tag type, JSON
sub-key, and rejection-justification phrasing in both languages so future
prompt edits cannot silently drop the guard rail.

## Why no separate Reverse-DCF agent?

Token cost. Adding a second LLM round-trip per ticker would roughly double
the strategist spend. The Reverse DCF block lives inside the existing
strategist call; the model already has every input it needs from the 6
upstream report sections.
