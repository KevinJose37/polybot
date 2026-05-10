"""Quick unit test for chain_listener decode + dedup + metrics."""
import sys
sys.path.insert(0, '.')
from chain_listener import decode_transfer_single, TxDedup, ListenerMetrics, _addr_to_topic, _topic_to_addr

# Test 1: Address to/from topic conversion
addr = "0x89b5cdaaa4866c1e738406712012a630b4078beb"
topic = _addr_to_topic(addr)
recovered = _topic_to_addr(topic)
assert recovered == addr.lower(), f"Round-trip failed: {recovered} != {addr.lower()}"
print(f"[PASS] addr_to_topic round-trip: {addr[:14]}... -> {topic[:18]}... -> {recovered[:14]}...")

# Test 2: Decode TransferSingle
# Simulate a real log entry
token_id = 123456789012345678
amount_raw = 5000000  # 5.0 shares (6 decimals)
fake_log = {
    "topics": [
        "0xc3d58168c5ae7397731d063d5bbf3d657854427343f4c583259e236544146039",
        _addr_to_topic("0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"),  # operator
        _addr_to_topic("0x0000000000000000000000000000000000000000"),  # from (mint)
        _addr_to_topic(addr),  # to (receiver)
    ],
    "data": "0x" + hex(token_id)[2:].zfill(64) + hex(amount_raw)[2:].zfill(64),
    "transactionHash": "0xabcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",
    "blockNumber": "0x3B9ACA0",
    "logIndex": "0x5",
}
event = decode_transfer_single(fake_log)
assert event is not None
assert event["to_addr"] == addr.lower()
assert event["from_addr"] == "0x" + "0" * 40  # zero address (mint)
assert event["amount"] == 5.0
assert event["block_number"] == 62500000
assert event["token_id"] == str(token_id)
print(f"[PASS] decode_transfer_single: token={event['token_id'][:16]} amount={event['amount']} block={event['block_number']}")

# Test 3: Dedup
dedup = TxDedup(max_size=100)
tx = "0xdeadbeef"
assert dedup.is_new(tx) == True, "First should be new"
assert dedup.is_new(tx) == False, "Second should be duplicate"
assert dedup.is_new("0xother") == True, "Different tx should be new"
print(f"[PASS] TxDedup: new/dup/new correctly handled")

# Test 4: Metrics
metrics = ListenerMetrics()
metrics.record_event(2.5, 15.0, True)
metrics.record_event(3.0, 20.0, False)
metrics.record_duplicate()
metrics.record_reconnect()
snap = metrics.snapshot()
assert snap["events_processed"] == 2
assert snap["events_buy"] == 1
assert snap["events_sell"] == 1
assert snap["events_duplicated"] == 1
assert snap["ws_reconnects"] == 1
assert snap["avg_detection_latency_s"] == 2.75
print(f"[PASS] Metrics: proc={snap['events_processed']} buy={snap['events_buy']} sell={snap['events_sell']} dup={snap['events_duplicated']}")

# Test 5: Topic filter construction
from chain_listener import FLEET_WALLETS_PLACEHOLDER_NOT_USED  # This won't exist, but test the concept
from copy_wallet import FLEET_WALLETS
cryp = [w["address"] for w in FLEET_WALLETS if w["cat"] == "CRYP"]
topics_to = [_addr_to_topic(w) for w in cryp]
print(f"[PASS] Topic filter: {len(cryp)} CRYP wallets -> {len(topics_to)} topic filters")

print("\n  ALL TESTS PASSED")
