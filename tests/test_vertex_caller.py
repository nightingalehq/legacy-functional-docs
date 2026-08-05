"""Guards for the Vertex AI ModelCaller and its --provider wiring (#12).

No real network call or GCP project is exercised here -- these check the
error-path contract (missing dependency, missing project) and that
cmd_batch actually routes to VertexCaller, mirroring how
anthropic_caller.py's own isolation is meant to be verified: the `anthropic`
package stays optional, and a clear RuntimeError with an install hint is the
whole point if it's missing.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from mfdoc import cli
from mfdoc.vertex_caller import VertexCaller

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_missing_anthropic_package_raises_install_hint(monkeypatch):
    monkeypatch.setitem(sys.modules, "anthropic", None)  # force ImportError on `from anthropic import ...`
    with pytest.raises(RuntimeError, match=r"pip install 'mfdoc\[vertex\]'"):
        VertexCaller(project="some-project")


def test_missing_project_raises_before_any_client_construction(monkeypatch):
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.delenv("GCLOUD_PROJECT", raising=False)
    fake_anthropic = SimpleNamespace(AnthropicVertex=lambda **kw: pytest.fail(
        "AnthropicVertex must not be constructed when no project is configured"))
    monkeypatch.setitem(sys.modules, "anthropic", fake_anthropic)
    with pytest.raises(RuntimeError, match="no GCP project configured"):
        VertexCaller()


def test_cmd_batch_routes_to_vertex_caller_when_provider_is_vertex(cli_args, tmp_path, monkeypatch):
    project_dir = Path(cli_args.config).parent
    if not (project_dir / "reference").exists():
        shutil.copytree(REPO_ROOT / "reference", project_dir / "reference")
        shutil.copytree(REPO_ROOT / "templates", project_dir / "templates")

    constructed = {}

    class FakeVertexCaller:
        def __init__(self, model=None, project=None, region=None):
            constructed.update(model=model, project=project, region=region)

        def __call__(self, prompt):
            from mfdoc.batch import ModelResponse
            return ModelResponse(text=prompt, input_tokens=1, output_tokens=1)

    monkeypatch.setattr("mfdoc.vertex_caller.VertexCaller", FakeVertexCaller)

    args = SimpleNamespace(
        config=cli_args.config, out=str(tmp_path / "out"), members="MMP0100",
        model="claude-sonnet-4-5", concurrency=1, state="", caller="anthropic",
        provider="vertex", gcp_project="test-proj", gcp_region="us-east5",
    )
    cli.cmd_batch(args)
    assert constructed == {"model": "claude-sonnet-4-5", "project": "test-proj", "region": "us-east5"}
