"""Static deployment manifest checks do not claim a Docker build succeeded."""

import shlex
from pathlib import Path


def test_team_docker_copy_sources_exist_and_frontend_uses_repository_layout():
    root = Path(__file__).resolve().parents[1]
    source = (root / "deploy/team/Dockerfile").read_text()
    for line in source.splitlines():
        if not line.startswith("COPY ") or "--from=" in line:
            continue
        for pattern in shlex.split(line)[1:-1]:
            assert list(root.glob(pattern)), f"Docker COPY source does not exist: {pattern}"
    assert "WORKDIR /build\n" in source
    assert "COPY --from=frontend /build/dist /app/ui/dist" in source
    assert "uv.lock" in source and "uv sync --locked --extra team --no-dev" in source
