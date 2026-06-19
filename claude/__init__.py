"""Claude-driven decision layer for XORR.

A periodic watchlist agent scans the eligible-coin universe, scores each coin on
technical features (volume spike, ATR volatility + expansion, momentum, RSI, stretch
from mean, range position), and asks Claude (the Anthropic API) to pick what to play
and which of our ENABLED strategies fits each pick. This replaces the weak Groq LLM
council as the trade decision-maker.

Spot-only: Claude only ever recommends LONG spot entries on the eligible BEP-20 majors.
"""
