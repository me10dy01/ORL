import pytest

from app.domain.tickets import InvalidTransition, transition


def test_claim_open_ticket():
    assert transition("open", "claim") == "in_progress"


def test_close_requires_resolved():
    with pytest.raises(InvalidTransition):
        transition("open", "close")
