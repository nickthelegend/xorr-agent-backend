from data.cex_oracle import is_sane

async def passes_cex_sanity(symbol: str, cmc_price: float) -> bool:
    """Sanity check to verify if the CMC quote price is within settings.cex_deviation_bps of CEX price."""
    return await is_sane(symbol, cmc_price)
