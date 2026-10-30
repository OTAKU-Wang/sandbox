"""A93 (second half, `app/services/blockchain_service.py`) — a local hash-chain
fallback must not be able to pose as a chain attestation.

`backend-20` moved `contract_service` onto the persisted `blockchain_adapter`;
this block is the half that was left in this file:

* `_client` was assigned `None` exactly once (no setter, no config read), so the
  chain branch of `anchor_to_blockchain()`/`verify_attestation()` was unreachable
  and `_get_client()` returned the same value on both branches;
* the in-process fallback reported the same `success=True` + `tx_hash` shape as a
  real chain receipt, with nothing on the result telling the caller that the
  anchor lives only in this process's dict;
* the chain hash fed on `str(dict)` (insertion-order dependent) instead of a
  canonical encoding;
* `_local_anchors` grew without bound;
* `prev_hash → block_number+1 → write-back` ran with no lock, so two anchors in
  the same process could fork the chain.

Real FISCO BCOS / AntChain hardware is not reachable here: the chain branch is
exercised with an injected fake client (seam-level), never claimed as a real
on-chain anchor. Durable verification remains `blockchain_adapter`.
"""
import json
import logging
import threading

import pytest

from app.services import blockchain_service as bs
from app.services.blockchain_service import BlockchainService

GENESIS = "blockchain-service-genesis"


class _FakeChainClient:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def call_contract(self, contract, method, args):
        self.calls.append((contract, method, args))
        if self.fail:
            raise RuntimeError("node unreachable")
        return "0xdeadbeef" if method == "anchor" else True


# --- the chain branch is reachable only when a client is wired ----------------------

def test_set_client_makes_the_chain_branch_reachable():
    svc = BlockchainService()
    client = _FakeChainClient()
    svc.set_client(client)
    result = svc.anchor_to_blockchain("root-1", {"k": "v"})
    assert result.success is True
    assert result.tx_hash == "0xdeadbeef"
    assert client.calls and client.calls[0][1] == "anchor"
    assert result.is_simulation is False
    assert result.backend == bs.CHAIN_BACKEND


def test_chain_failure_is_not_reported_as_success():
    svc = BlockchainService()
    svc.set_client(_FakeChainClient(fail=True))
    result = svc.anchor_to_blockchain("root-1")
    assert result.success is False
    assert result.tx_hash is None
    assert result.error == "node unreachable"


def test_without_a_client_the_fallback_discloses_itself(caplog):
    svc = BlockchainService()
    with caplog.at_level(logging.WARNING):
        result = svc.anchor_to_blockchain("root-1")
    assert result.success is True
    assert result.is_simulation is True
    assert result.backend == bs.LOCAL_BACKEND
    text = " ".join(r.getMessage() for r in caplog.records)
    assert "not" in text.lower() and "chain" in text.lower(), (
        f"the in-process fallback must warn that it is not a chain anchor, got: {text!r}"
    )


def test_backend_disclosure_says_the_store_is_not_durable():
    svc = BlockchainService()
    d = svc.backend_disclosure()
    assert d["backend"] == bs.LOCAL_BACKEND
    assert d["is_consortium_chain"] is False
    assert d["durable"] is False


# --- canonical hash input -----------------------------------------------------------

def _expected_hash(prev, block_no, root, metadata):
    payload = json.dumps(metadata or {}, sort_keys=True, separators=(",", ":"))
    return bs.crypto_service.sm3_hash(f"{prev}:{block_no}:{root}:{payload}".encode())


def test_metadata_hash_is_canonical_not_str_dict():
    svc = BlockchainService()
    meta = {"b": 2, "a": 1}
    result = svc.anchor_to_blockchain("root-1", meta)
    assert result.tx_hash == "0x" + _expected_hash(GENESIS, 1, "root-1", meta)


def test_equal_metadata_different_insertion_order_hashes_the_same():
    m1 = {"contract_id": "c-1", "kind": "contract"}
    m2 = {"kind": "contract", "contract_id": "c-1"}
    r1 = BlockchainService().anchor_to_blockchain("root-1", m1)
    r2 = BlockchainService().anchor_to_blockchain("root-1", m2)
    assert r1.tx_hash == r2.tx_hash, "str(dict) made the chain hash depend on key order"


# --- bounded store ------------------------------------------------------------------

def test_local_anchor_store_is_bounded_and_discloses_eviction(monkeypatch, caplog):
    monkeypatch.setattr(bs, "MAX_LOCAL_ANCHORS", 3)
    svc = BlockchainService()
    with caplog.at_level(logging.WARNING):
        for i in range(6):
            svc.anchor_to_blockchain(f"root-{i}")
    assert len(svc._local_anchors) == 3
    assert svc.dropped_local_anchors == 3
    assert "capped" in " ".join(r.getMessage() for r in caplog.records)


# --- the chain cannot fork inside one process ---------------------------------------

def test_concurrent_anchor_cannot_fork_the_chain(monkeypatch):
    a_in = threading.Event()
    b_in = threading.Event()
    state = {"first": True}
    real = bs.crypto_service.sm3_hash

    def _slow_first_hash(data):
        if state["first"]:
            state["first"] = False
            a_in.set()
            b_in.wait(0.5)
        else:
            b_in.set()
        return real(data)

    monkeypatch.setattr(bs.crypto_service, "sm3_hash", _slow_first_hash)
    svc = BlockchainService()
    t1 = threading.Thread(target=svc.anchor_to_blockchain, args=("root-A",))
    t2 = threading.Thread(target=svc.anchor_to_blockchain, args=("root-B",))
    t1.start()
    assert a_in.wait(2.0)
    t2.start()
    t1.join(5.0)
    t2.join(5.0)

    entries = sorted(svc._local_anchors.values(), key=lambda e: e["block_number"])
    assert [e["block_number"] for e in entries] == [1, 2]
    assert entries[0]["prev_hash"] == GENESIS
    assert entries[1]["prev_hash"] == entries[0]["chain_hash"], (
        "block 2 does not link to block 1 — two threads read the same prev_hash "
        "and forked the chain"
    )


# --- verification is honestly scoped to this process ---------------------------------

def test_verify_unknown_tx_warns_that_the_store_is_process_local(caplog):
    svc = BlockchainService()
    with caplog.at_level(logging.WARNING):
        assert svc.verify_attestation("0x" + "0" * 64) is False
    text = " ".join(r.getMessage() for r in caplog.records).lower()
    assert "process" in text and "restart" in text


def test_verify_uses_the_chain_client_when_one_is_wired():
    svc = BlockchainService()
    client = _FakeChainClient()
    svc.set_client(client)
    assert svc.verify_attestation("0xdeadbeef") is True
    assert client.calls[0][1] == "exists"


def test_module_singleton_declares_itself_not_a_chain():
    d = bs.blockchain_service.backend_disclosure()
    assert d["is_consortium_chain"] is False
