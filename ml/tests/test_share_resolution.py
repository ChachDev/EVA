import subprocess
import json
from pathlib import Path

import pytest

from eva_ml.contracts import Contracts
from eva_ml.share_resolution import ArchiveReview, extract_candidate


def test_unrecognized_archive_is_never_extracted(tmp_path):
    archive=tmp_path/"different.zip"
    archive.write_bytes(b"not the evaluated ZIP")
    target=tmp_path/"modules"
    with pytest.raises(ValueError,match="evaluated project archive"):
        extract_candidate(archive,target)
    assert not target.exists()


def test_child_failure_retains_primary_and_identifies_fallback(evaluation_request,monkeypatch):
    original=json.loads((Path(__file__).resolve().parents[2]/"contracts/examples/evaluation-response.json").read_text(encoding="utf8"))
    adapter=object.__new__(ArchiveReview)
    adapter.command=["synthetic-unused"]
    adapter.contracts=Contracts()
    def fail(*args,**kwargs):raise subprocess.TimeoutExpired("fixture",1)
    monkeypatch.setattr(subprocess,"run",fail)
    result=adapter.refine(evaluation_request,original)
    assert result["criteria"]==original["criteria"] and len(result["warnings"])==len(original["warnings"])+1
    assert "недоступна" in result["warnings"][-1]
