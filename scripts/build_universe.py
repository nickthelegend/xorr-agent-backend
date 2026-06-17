"""
Builds tokens.eligible.json for the BNB Hack competition (149 eligible BEP-20 tokens).

Hybrid universe:
  * Every eligible symbol is written (the on-chain eligibility whitelist).
  * `tradable` is set True only when we have BOTH a verified BSC contract
    (from PancakeSwap's curated token lists, which implies real PancakeSwap
    liquidity) AND a Binance USDT spot pair (which gives us price + klines for
    pricing and backtesting). Those are the tokens the agent actually trades.

Contract source: PancakeSwap default + extended token lists (chainId 56). These
are curated lists of tokens with PancakeSwap liquidity, so a symbol match is a
safe, swappable contract. Obscure symbols not on the list are left eligible but
non-tradable (contract="") rather than risking a wrong/fake address.

Optionally augments contracts via CoinMarketCap Pro (CMC_API_KEY) when present.

Run:  python scripts/build_universe.py
"""
import os
import sys
import json
import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# --- The 149 eligible competition symbols (deduped, original casing kept) ---
RAW_SYMBOLS = [
    "ETH", "USDT", "USDC", "XRP", "TRX", "DOGE", "ZEC", "ADA", "LINK", "BCH",
    "DAI", "TON", "USD1", "USDe", "M", "LTC", "AVAX", "SHIB", "XAUt", "WLFI",
    "H", "DOT", "UNI", "ASTER", "DEXE", "USDD", "ETC", "AAVE", "ATOM", "U",
    "STABLE", "FIL", "INJ", "币安人生", "NIGHT", "FET", "TUSD", "BONK", "PENGU", "CAKE",
    "SIREN", "LUNC", "ZRO", "KITE", "FDUSD", "BEAT", "PIEVERSE", "BTT", "NFT", "EDGE",
    "FLOKI", "LDO", "B", "FF", "PENDLE", "NEX", "STG", "AXS", "TWT", "HOME",
    "RAY", "COMP", "GWEI", "XCN", "GENIUS", "XPL", "BAT", "SKYAI", "APE", "IP",
    "SFP", "TAG", "NXPC", "AB", "SAHARA", "1INCH", "CHEEMS", "BANANAS31", "RIVER", "MYX",
    "RAVE", "SNX", "FORM", "LAB", "HTX", "USDf", "CTM", "BDX", "SLX", "UB",
    "DUCKY", "FRAX", "BILL", "WFI", "KOGE", "ALE", "FRXUSD", "USDF", "GOMINING", "VCNT",
    "GUA", "DUSD", "SMILEK", "0G", "BEAM", "MY", "SOON", "REAL", "Q", "AIOZ",
    "ZIG", "YFI", "TAC", "lisUSD", "CYS", "ZAMA", "TRIA", "HUMA", "PLUME", "ZIL",
    "XPR", "ZETA", "BabyDoge", "NILA", "ROSE", "VELO", "UAI", "BRETT", "OPEN", "BSB",
    "TOSHI", "BAS", "ACH", "AXL", "LUR", "ELF", "KAVA", "APR", "IRYS", "EURI",
    "XUSD", "BARD", "DUSK", "SUSHI", "PEAQ", "COAI", "BDCA", "XAUM",
]

# Base / valuation tokens the agent always needs (canonical BSC contracts).
KNOWN_CONTRACTS = {
    "USDT": ("0x55d398326f99059fF775485246999027B3197955", 18),
    "USDC": ("0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d", 18),
    "BTCB": ("0x7130d2A12B9BCbFAe4f2634d864A1Ee1Ce3Ead9c", 18),
    "ETH":  ("0x2170Ed0880ac9A755fd29B2688956BD959F933F8", 18),
    "BNB":  ("0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c", 18),
    "WBNB": ("0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c", 18),
    "CAKE": ("0x0E09FaBB73Bd3Ade0a17ECC321fD13a19e81cE82", 18),
}

# Eligible symbol -> Binance spot base asset, when it differs from the symbol.
BINANCE_BASE_OVERRIDE = {
    "BTCB": "BTC",
}

PCS_LISTS = [
    "https://tokens.pancakeswap.finance/pancakeswap-default.json",
    "https://tokens.pancakeswap.finance/pancakeswap-extended.json",
    "https://tokens.pancakeswap.finance/coingecko.json",
]


def dedupe(symbols):
    seen, out = set(), []
    for s in symbols:
        k = s.upper()
        if k not in seen:
            seen.add(k)
            out.append(s)
    return out


def load_pcs_contracts():
    """symbol(upper) -> (checksum_address, decimals) for BSC (chainId 56)."""
    out = {}
    with httpx.Client(timeout=20.0) as c:
        for url in PCS_LISTS:
            try:
                r = c.get(url)
                if r.status_code != 200:
                    print(f"  [warn] {url} -> {r.status_code}")
                    continue
                for t in r.json().get("tokens", []):
                    if t.get("chainId") != 56:
                        continue
                    sym = (t.get("symbol") or "").upper()
                    addr = t.get("address")
                    dec = int(t.get("decimals", 18))
                    if sym and addr and sym not in out:
                        out[sym] = (addr, dec)
                print(f"  [ok] {url.split('/')[-1]} ({len(out)} cumulative BSC tokens)")
            except Exception as e:
                print(f"  [warn] {url} failed: {e}")
    return out


def load_binance_usdt_bases():
    with httpx.Client(timeout=20.0) as c:
        r = c.get("https://api.binance.com/api/v3/exchangeInfo")
        r.raise_for_status()
        return {
            s["baseAsset"].upper()
            for s in r.json().get("symbols", [])
            if s.get("quoteAsset") == "USDT" and s.get("status") == "TRADING"
        }


def main():
    symbols = dedupe(RAW_SYMBOLS)
    print(f"Eligible symbols (deduped): {len(symbols)}")

    print("Loading PancakeSwap token lists (BSC contracts)...")
    pcs = load_pcs_contracts()
    print("Loading Binance USDT spot pairs...")
    binance_bases = load_binance_usdt_bases()

    entries = []
    tradable_count = 0
    for sym in symbols:
        up = sym.upper()
        # Contract: known base override first, then PancakeSwap list
        if up in KNOWN_CONTRACTS:
            contract, decimals = KNOWN_CONTRACTS[up]
        elif up in pcs:
            contract, decimals = pcs[up]
        else:
            contract, decimals = "", 18

        # Binance pair detection (for price + klines). Only ASCII tickers can map
        # to a Binance spot pair; non-latin symbols are eligible but not tradable.
        binance_base = BINANCE_BASE_OVERRIDE.get(up, up)
        binance_symbol = binance_base if (binance_base.isascii() and binance_base in binance_bases) else ""

        tradable = bool(contract) and bool(binance_symbol)
        if tradable:
            tradable_count += 1

        entries.append({
            "symbol": sym,
            "contract": contract,
            "decimals": decimals,
            "tradable": tradable,
            "binance_symbol": binance_symbol,
        })

    # Ensure base/valuation tokens exist even if not in eligible list (BTCB, BNB)
    have = {e["symbol"].upper() for e in entries}
    for base in ("BTCB", "BNB"):
        if base not in have:
            addr, dec = KNOWN_CONTRACTS[base]
            bsym = BINANCE_BASE_OVERRIDE.get(base, base)
            entries.append({
                "symbol": base,
                "contract": addr,
                "decimals": dec,
                "tradable": (bsym in binance_bases),
                "binance_symbol": bsym if bsym in binance_bases else "",
                "base_asset": True,
            })

    out_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tokens.eligible.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)

    with_contract = sum(1 for e in entries if e["contract"])
    print(f"\nWrote {len(entries)} entries -> {out_path}")
    print(f"  with verified BSC contract: {with_contract}")
    print(f"  tradable (contract + Binance pair): {tradable_count}")
    tradable_syms = ", ".join(e["symbol"] for e in entries if e["tradable"])
    # console-safe (Windows cp1252) print of possibly non-ASCII symbols
    sys.stdout.buffer.write(("  tradable symbols: " + tradable_syms + "\n").encode("utf-8", "replace"))


if __name__ == "__main__":
    main()
