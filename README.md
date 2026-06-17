# XORR Agent Backend

Autonomous Trading Agent for BNB Chain, powered by CoinMarketCap and Trust Wallet Agent Kit (TWAK).

## Prerequisites

- Python 3.10+
- Node.js & TWAK CLI (`npm i -g @trustwallet/cli`)
- CoinMarketCap API Key
- Groq API Key

## Setup

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
2. Copy `.env.example` to `.env` and fill in your keys.
3. Start the FastAPI app:
   ```bash
   uvicorn main:app --reload
   ```
