"""ERC-8004 registration safety tests (network-free — chain + wallet are mocked).

The guarantees that matter: a DRY RUN broadcasts nothing, a send with an unfunded
wallet refuses to broadcast, an already-registered wallet short-circuits, and a funded
send actually broadcasts exactly once.
"""
from unittest.mock import MagicMock, patch

WALLET = "0x3551f68748AACDd77d28a4149C014f8FFbb95f91"


def _fake_w3(balance_wei=0, gas_price=int(0.05e9), registered_bal=0):
    w3 = MagicMock()
    w3.to_checksum_address.side_effect = lambda a: a
    w3.eth.get_balance.return_value = balance_wei
    w3.eth.gas_price = gas_price
    w3.eth.get_transaction_count.return_value = 1
    w3.to_hex.side_effect = lambda x: "0x" + (x.hex() if hasattr(x, "hex") else str(x))
    contract = MagicMock()
    reg_fn = MagicMock()
    reg_fn.estimate_gas.return_value = 200_000
    reg_fn.build_transaction.return_value = {"to": "0xreg", "data": "0x", "nonce": 1}
    contract.functions.register.return_value = reg_fn
    bal_fn = MagicMock()
    bal_fn.call.return_value = registered_bal
    contract.functions.balanceOf.return_value = bal_fn
    w3.eth.contract.return_value = contract
    rcpt = MagicMock()
    rcpt.status = 1
    rcpt.blockNumber = 123
    w3.eth.wait_for_transaction_receipt.return_value = rcpt
    w3.eth.send_raw_transaction.return_value = b"\xab\xcd"
    return w3, reg_fn


def _fake_wallet():
    w = MagicMock()
    w.address = WALLET
    signed = MagicMock()
    signed.raw_transaction = b"\x01"
    w.account.sign_transaction.return_value = signed
    return w


def _patch(w3):
    return patch("core.erc8004.get_w3", return_value=w3), patch("core.erc8004.get_agent_wallet", return_value=_fake_wallet())


def test_dry_run_broadcasts_nothing():
    w3, reg_fn = _fake_w3()
    p1, p2 = _patch(w3)
    with p1, p2:
        from core.erc8004 import register_agent
        res = register_agent(send=False)
    assert res["ok"] and "DRY RUN" in res["status"]
    assert res["registry"].lower().endswith("4f659d7")
    w3.eth.send_raw_transaction.assert_not_called()
    reg_fn.build_transaction.assert_not_called()


def test_send_refuses_when_unfunded():
    w3, _ = _fake_w3(balance_wei=0)
    p1, p2 = _patch(w3)
    with p1, p2:
        from core.erc8004 import register_agent
        res = register_agent(send=True)
    assert res["ok"] is False and "INSUFFICIENT" in res["status"].upper()
    w3.eth.send_raw_transaction.assert_not_called()


def test_already_registered_short_circuits():
    w3, _ = _fake_w3(registered_bal=1)
    p1, p2 = _patch(w3)
    with p1, p2:
        from core.erc8004 import register_agent
        res = register_agent(send=True)
    assert "ALREADY" in res["status"].upper()
    w3.eth.send_raw_transaction.assert_not_called()


def test_funded_send_broadcasts_once():
    w3, _ = _fake_w3(balance_wei=int(1e16))  # 0.01 BNB — plenty for ~$0.01 gas
    p1, p2 = _patch(w3)
    with p1, p2:
        from core.erc8004 import register_agent
        res = register_agent(send=True)
    assert "REGISTERED" in res["status"].upper()
    assert "txHash" in res
    w3.eth.send_raw_transaction.assert_called_once()
