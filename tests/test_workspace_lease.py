import pytest


def test_same_workspace_has_only_one_writer_and_recovers_after_release(tmp_path):
    from creative_agent.workspace_lease import WorkspaceLease
    first, second = WorkspaceLease(tmp_path), WorkspaceLease(tmp_path)
    first.acquire()
    try:
        with pytest.raises(ValueError,match='已经打开'): second.acquire()
        second.release()  # A rejected instance must not release the first owner's lock.
        with pytest.raises(ValueError): WorkspaceLease(tmp_path).acquire()
    finally: first.release()
    second.acquire();second.release()


def test_distinct_workspaces_and_stale_pid_do_not_block(tmp_path):
    from creative_agent.workspace_lease import WorkspaceLease
    (tmp_path/'.workspace.lock').write_text('stale process data')
    a,b=WorkspaceLease(tmp_path),WorkspaceLease(tmp_path/'other')
    try:
        a.acquire();b.acquire()
    finally:
        a.release();b.release()


def test_repeated_acquire_cannot_leak_an_owners_handle(tmp_path):
    from creative_agent.workspace_lease import WorkspaceLease
    lease=WorkspaceLease(tmp_path)
    lease.acquire();lease.acquire();lease.release()
    other=WorkspaceLease(tmp_path)
    other.acquire();other.release()


def test_writer_is_rejected_before_any_database_or_index_initialization(tmp_path):
    from creative_agent.api import create_app
    from creative_agent.workspace_lease import WorkspaceLease
    from test_desktop_upgrade import Vault
    owner=WorkspaceLease(tmp_path);owner.acquire()
    try:
        with pytest.raises(ValueError,match='已经打开'):
            create_app(tmp_path,secret_store=Vault(),run_jobs=True)
        assert not (tmp_path/'creative.sqlite3').exists()
    finally:owner.release()


def test_factory_initialization_holds_lock_and_releases_it_on_failure(tmp_path,monkeypatch):
    import creative_agent.api as api
    from creative_agent.workspace_lease import WorkspaceLease
    from test_desktop_upgrade import Vault
    observed=[]
    def failing_store(path):
        probe=WorkspaceLease(tmp_path)
        try:probe.acquire()
        except ValueError:observed.append(True)
        else:observed.append(False);probe.release()
        raise RuntimeError('isolated initialization failure')
    monkeypatch.setattr(api,'Store',failing_store)
    with pytest.raises(RuntimeError,match='initialization failure'):
        api.create_app(tmp_path,secret_store=Vault(),run_jobs=True)
    assert observed==[True]
    retry=WorkspaceLease(tmp_path);retry.acquire();retry.release()
