"""Local media ticket boundary tests; no files, network or customer records."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError

import pytest


class Clock:
    def __init__(self):
        self.value = 100.0

    def __call__(self):
        return self.value


def test_unknown_ticket_fails_without_echoing_the_supplied_secret():
    from creative_agent.media_streams import MediaTicketRegistry
    registry = MediaTicketRegistry()
    supplied = 'Bearer-secret-that-must-not-be-echoed'
    with pytest.raises(ValueError) as failure:
        registry.resolve(supplied)
    assert supplied not in str(failure.value)


def test_ticket_resolves_only_its_frozen_media_identity():
    from creative_agent.media_streams import MediaTicketRegistry
    registry = MediaTicketRegistry()
    ticket = registry.issue('project-a', 'asset-a', 'hash-before-replacement')
    identity = registry.resolve(ticket)
    assert (identity.project_id, identity.asset_id, identity.sha256) == ('project-a', 'asset-a', 'hash-before-replacement')
    with pytest.raises((FrozenInstanceError, AttributeError)):
        identity.asset_id = 'asset-b'
    assert registry.resolve(ticket).asset_id == 'asset-a'


def test_requests_do_not_extend_the_fixed_ticket_expiry():
    from creative_agent.media_streams import MediaTicketRegistry
    clock = Clock()
    registry = MediaTicketRegistry(ttl_seconds=10, clock=clock)
    ticket = registry.issue('project-a', 'asset-a', 'hash-a')
    clock.value = 109.99
    assert registry.resolve(ticket).asset_id == 'asset-a'
    clock.value = 110.0
    with pytest.raises(ValueError) as failure:
        registry.resolve(ticket)
    assert ticket not in str(failure.value)
    with pytest.raises(ValueError):
        registry.resolve(ticket)


def test_capacity_evicts_oldest_ticket_while_retaining_newer_ones():
    from creative_agent.media_streams import MediaTicketRegistry
    registry = MediaTicketRegistry(max_entries=2)
    old = registry.issue('project-a', 'asset-old', 'hash-old')
    newer = registry.issue('project-a', 'asset-newer', 'hash-newer')
    latest = registry.issue('project-a', 'asset-latest', 'hash-latest')
    with pytest.raises(ValueError):
        registry.resolve(old)
    assert registry.resolve(newer).asset_id == 'asset-newer'
    assert registry.resolve(latest).asset_id == 'asset-latest'


def test_expired_tickets_are_pruned_before_discarding_a_live_ticket():
    from creative_agent.media_streams import MediaTicketRegistry
    clock = Clock()
    registry = MediaTicketRegistry(ttl_seconds=10, max_entries=2, clock=clock)
    expired = registry.issue('project-a', 'asset-expired', 'hash-expired')
    clock.value = 105.0
    live = registry.issue('project-a', 'asset-live', 'hash-live')
    clock.value = 110.0
    latest = registry.issue('project-a', 'asset-latest', 'hash-latest')
    with pytest.raises(ValueError):
        registry.resolve(expired)
    assert registry.resolve(live).asset_id == 'asset-live'
    assert registry.resolve(latest).asset_id == 'asset-latest'


def test_concurrent_ticket_creation_and_resolution_preserve_every_identity():
    from creative_agent.media_streams import MediaTicketRegistry
    registry = MediaTicketRegistry(max_entries=256)
    def create(index):
        ticket = registry.issue(f'project-{index}', f'asset-{index}', f'hash-{index}')
        return index, ticket, registry.resolve(ticket)
    with ThreadPoolExecutor(max_workers=16) as workers:
        results = list(workers.map(create, range(128)))
    assert len({ticket for _, ticket, _ in results}) == 128
    for index, ticket, identity in results:
        assert (identity.project_id, identity.asset_id, identity.sha256) == (f'project-{index}', f'asset-{index}', f'hash-{index}')
        assert registry.resolve(ticket) == identity


def test_tokens_are_opaque_and_supplied_scope_objects_are_rejected():
    from creative_agent.media_streams import MediaTicketRegistry
    registry = MediaTicketRegistry()
    ticket = registry.issue('private-project-identity', 'private-asset-identity', 'private-content-hash')
    assert all(value not in ticket for value in ('private-project-identity', 'private-asset-identity', 'private-content-hash'))
    assert ticket not in repr(registry)
    with pytest.raises(ValueError):
        registry.issue({'scope': 'all-projects', 'bearer': 'root-token'}, 'asset-a', 'hash-a')
    with pytest.raises(ValueError):
        registry.resolve({'scope': 'all-projects', 'bearer': 'root-token'})
    with pytest.raises(TypeError):
        registry.issue('project-a', 'asset-a', 'hash-a', bearer='root-token')
