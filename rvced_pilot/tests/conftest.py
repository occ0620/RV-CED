"""Workspace-only test artifacts; avoid Windows pytest temporary-dir ACL rewrites."""
from pathlib import Path
import uuid

import pytest


@pytest.fixture
def tmp_path():
    folder = Path(__file__).resolve().parent / '.runtime' / uuid.uuid4().hex
    folder.mkdir(parents=True)
    return folder
