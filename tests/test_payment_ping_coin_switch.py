"""HANDOFF §34 QA-1: a customer who agreed to switch coins ("Yes send me address
to send") was misclassified as a payment ping because _is_payment_ping only saw
that one message, with no idea it followed Lily offering a BTC switch. That routed
Lily into the post-payment "checking with finance" script before anything was
paid, and she never sent fresh BTC instructions — the customer got stalled twice
and left.

Fix: _is_payment_ping now takes the recent conversation so the classifier can see
a coin-switch offer/agreement and route it to OTHER (the ordinary ordering path,
which re-quotes in the new coin) instead of the payment-verify branch.
"""
from __future__ import annotations
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _response(text: str):
    return SimpleNamespace(content=[SimpleNamespace(text=text)])


def test_history_is_sent_to_the_classifier():
    import agents.messaging_agent as ma
    history = [
        {"role": "assistant", "content": "Would you like to switch to BTC then, dear?"},
        {"role": "user", "content": "Walk me through how"},
        {"role": "assistant", "content": "... Shall I switch your order to BTC now?"},
    ]
    with mock.patch.object(ma.claude, "create", return_value=_response("OTHER")) as create:
        ma._is_payment_ping("Yes send me address to send", history)
    sent = create.call_args.kwargs["messages"][0]["content"]
    assert "switch to BTC" in sent
    assert "Yes send me address to send" in sent


def test_coin_switch_agreement_is_not_a_payment_ping():
    """The exact QA-1 message, with the coin-switch context that caused the bug."""
    import agents.messaging_agent as ma
    history = [
        {"role": "assistant", "content": "Would you like to switch to BTC then, dear?"},
        {"role": "user", "content": "Walk me through how"},
        {"role": "assistant", "content": "... Shall I switch your order to BTC now?"},
    ]
    with mock.patch.object(ma.claude, "create", return_value=_response("OTHER")):
        assert ma._is_payment_ping("Yes send me address to send", history) is False


def test_plain_i_paid_message_is_still_a_payment_ping():
    import agents.messaging_agent as ma
    with mock.patch.object(ma.claude, "create", return_value=_response("PAYMENT")):
        assert ma._is_payment_ping("I sent it, please check", []) is True


def test_classify_failure_defaults_to_payment_ping():
    import agents.messaging_agent as ma
    with mock.patch.object(ma.claude, "create", side_effect=Exception("network error")):
        assert ma._is_payment_ping("anything", None) is True


def test_empty_message_defaults_to_payment_ping_without_calling_claude():
    import agents.messaging_agent as ma
    with mock.patch.object(ma.claude, "create") as create:
        assert ma._is_payment_ping("   ", None) is True
    create.assert_not_called()


def test_no_history_still_classifies_via_claude():
    """No conversation available (e.g. very first turn) — falls back to classifying
    the bare message, same as before this fix."""
    import agents.messaging_agent as ma
    with mock.patch.object(ma.claude, "create", return_value=_response("PAYMENT")) as create:
        assert ma._is_payment_ping("send me the address again", None) is True
    sent = create.call_args.kwargs["messages"][0]["content"]
    assert sent == "Newest message: send me the address again"
