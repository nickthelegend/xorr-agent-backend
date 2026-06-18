"""
Multi-strategy ensemble combiner.

When several independent strategies fire on the SAME token in the same scan, that
agreement is real conviction. We collapse those signals into a single high-
conviction entry: the lead (highest-confidence) strategy carries it, but its
confidence is boosted per agreeing strategy, the stop is tightened to the safest
proposed level, and `n_agree` is recorded so position sizing scales the trade up
(within the existing risk caps). Strategies that fire alone pass through unchanged.
"""
from typing import List, Dict
from core.types import Signal

# confidence added per *additional* agreeing strategy (capped)
AGREE_CONF_BONUS = 0.05
MAX_CONF = 0.97


def combine_signals(signals: List[Signal]) -> List[Signal]:
    # Group by (symbol, direction, venue) so only TRULY-agreeing signals merge.
    # A long-spot and a short-perp on the same symbol must never collapse into one.
    by_symbol: Dict[tuple, List[Signal]] = {}
    for s in signals:
        key = (s.symbol.upper(), getattr(s, "direction", "long"), getattr(s, "venue", "spot"))
        by_symbol.setdefault(key, []).append(s)

    combined: List[Signal] = []
    for _key, sigs in by_symbol.items():
        if len(sigs) == 1:
            sigs[0].n_agree = 1
            combined.append(sigs[0])
            continue

        n = len(sigs)
        lead = max(sigs, key=lambda x: x.confidence)        # carry the strongest
        agree_names = sorted({s.strategy_name for s in sigs})

        lead.confidence = min(MAX_CONF, lead.confidence + AGREE_CONF_BONUS * (n - 1))
        lead.stop_loss_pct = min(s.stop_loss_pct for s in sigs)   # safest stop
        # take the most ambitious target among the agreeing strategies (let it run)
        lead.take_profit_pct = max(s.take_profit_pct for s in sigs)
        lead.max_hold_min = max(s.max_hold_min for s in sigs)
        lead.n_agree = n
        lead.rationale = f"ENSEMBLE x{n} ({', '.join(agree_names)}): " + lead.rationale
        combined.append(lead)

    return combined
