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
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

from mfdoc import cli
from mfdoc.vertex_caller import VertexCaller

REPO_ROOT = Path(__file__).resolve().parent.parent


def _fake_google_auth_package(monkeypatch):
    """A `SimpleNamespace(auth=SimpleNamespace())` fake for `google` doesn't
    reliably satisfy `import google.auth`: the import system requires the
    parent (`google`) to actually be a package (have `__path__`), which a
    plain SimpleNamespace doesn't. Real `ModuleType` objects with `__path__`
    set on the parent are what `import google.auth` actually needs -- this
    also registers "google.auth" in sys.modules directly, matching how the
    real import system would leave it after a successful import."""
    google_pkg = types.ModuleType("google")
    google_pkg.__path__ = []
    auth_mod = types.ModuleType("google.auth")
    google_pkg.auth = auth_mod
    monkeypatch.setitem(sys.modules, "google", google_pkg)
    monkeypatch.setitem(sys.modules, "google.auth", auth_mod)


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


def test_call_retries_a_transient_error_and_eventually_succeeds(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "some-project")

    class _RateLimitError(Exception):
        pass

    class _APIConnectionError(Exception):
        pass

    class _InternalServerError(Exception):
        pass

    attempts = {"n": 0}

    class FakeMessages:
        def create(self, **kw):
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise _RateLimitError("simulated rate limit")
            return SimpleNamespace(
                content=[SimpleNamespace(text="ok", type="text")],
                usage=SimpleNamespace(input_tokens=1, output_tokens=1),
            )

    fake_client = SimpleNamespace(messages=FakeMessages())
    fake_anthropic = SimpleNamespace(
        AnthropicVertex=lambda **kw: fake_client,
        RateLimitError=_RateLimitError,
        APIConnectionError=_APIConnectionError,
        InternalServerError=_InternalServerError,
    )
    monkeypatch.setitem(sys.modules, "anthropic", fake_anthropic)
    _fake_google_auth_package(monkeypatch)

    # call_with_retry's real backoff would sleep ~1s+2s here; patch it out
    # so the test doesn't pay for real wall-clock retry delay.
    monkeypatch.setattr("mfdoc.retry.time.sleep", lambda s: None)
    caller = VertexCaller(project="some-project")
    result = caller("some prompt")

    assert attempts["n"] == 3
    assert result.text == "ok"
    # issue #84: 2 failed attempts before the 3rd (successful) one -> 2 retries.
    assert result.retries == 2


def test_call_does_not_retry_a_non_transient_error(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "some-project")

    class _RateLimitError(Exception):
        pass

    class _APIConnectionError(Exception):
        pass

    class _InternalServerError(Exception):
        pass

    class _BadRequestError(Exception):
        pass

    attempts = {"n": 0}

    class FakeMessages:
        def create(self, **kw):
            attempts["n"] += 1
            raise _BadRequestError("malformed prompt, never retry this")

    fake_client = SimpleNamespace(messages=FakeMessages())
    fake_anthropic = SimpleNamespace(
        AnthropicVertex=lambda **kw: fake_client,
        RateLimitError=_RateLimitError,
        APIConnectionError=_APIConnectionError,
        InternalServerError=_InternalServerError,
    )
    monkeypatch.setitem(sys.modules, "anthropic", fake_anthropic)
    _fake_google_auth_package(monkeypatch)

    caller = VertexCaller(project="some-project")
    with pytest.raises(_BadRequestError):
        caller("some prompt")
    assert attempts["n"] == 1


def test_call_sends_a_plain_string_when_no_cache_prefix_is_registered(monkeypatch):
    """Issue #159: same default-unchanged contract as AnthropicCaller -- no
    set_cache_prefixes call means every prompt goes out exactly as before."""
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "some-project")
    _fake_google_auth_package(monkeypatch)
    seen = {}

    class FakeMessages:
        def create(self, **kw):
            seen["content"] = kw["messages"][0]["content"]
            return SimpleNamespace(
                content=[SimpleNamespace(text="ok", type="text")],
                usage=SimpleNamespace(input_tokens=1, output_tokens=1),
            )

    fake_client = SimpleNamespace(messages=FakeMessages())
    monkeypatch.setitem(sys.modules, "anthropic", SimpleNamespace(
        AnthropicVertex=lambda **kw: fake_client, RateLimitError=Exception,
        APIConnectionError=Exception, InternalServerError=Exception,
    ))

    caller = VertexCaller(project="some-project")
    caller("some prompt")
    assert seen["content"] == "some prompt"


def test_call_splits_a_matching_prefix_into_a_cached_content_block(monkeypatch):
    """Issue #159: same cache-block-splitting contract as AnthropicCaller."""
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "some-project")
    _fake_google_auth_package(monkeypatch)
    seen = {}

    class FakeMessages:
        def create(self, **kw):
            seen["content"] = kw["messages"][0]["content"]
            return SimpleNamespace(
                content=[SimpleNamespace(text="ok", type="text")],
                usage=SimpleNamespace(input_tokens=1, output_tokens=1),
            )

    fake_client = SimpleNamespace(messages=FakeMessages())
    monkeypatch.setitem(sys.modules, "anthropic", SimpleNamespace(
        AnthropicVertex=lambda **kw: fake_client, RateLimitError=Exception,
        APIConnectionError=Exception, InternalServerError=Exception,
    ))

    caller = VertexCaller(project="some-project")
    caller.set_cache_prefixes(["stable prefix\n\n---\n\n"])
    caller("stable prefix\n\n---\n\nvariable part")

    assert seen["content"] == [
        {"type": "text", "text": "stable prefix\n\n---\n\n",
         "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": "variable part"},
    ]


def test_set_cache_prefixes_treats_a_single_string_as_one_prefix_not_chars(monkeypatch):
    """A caller passing a bare string (an easy mistake -- `str` is iterable)
    must not have it silently exploded into one-character prefixes, which
    would corrupt prompt splitting. A single string is one whole prefix."""
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "some-project")
    _fake_google_auth_package(monkeypatch)
    seen = {}

    class FakeMessages:
        def create(self, **kw):
            seen["content"] = kw["messages"][0]["content"]
            return SimpleNamespace(
                content=[SimpleNamespace(text="ok", type="text")],
                usage=SimpleNamespace(input_tokens=1, output_tokens=1),
            )

    fake_client = SimpleNamespace(messages=FakeMessages())
    monkeypatch.setitem(sys.modules, "anthropic", SimpleNamespace(
        AnthropicVertex=lambda **kw: fake_client, RateLimitError=Exception,
        APIConnectionError=Exception, InternalServerError=Exception,
    ))

    caller = VertexCaller(project="some-project")
    caller.set_cache_prefixes("stable prefix\n\n---\n\n")
    caller("stable prefix\n\n---\n\nvariable part")

    assert seen["content"] == [
        {"type": "text", "text": "stable prefix\n\n---\n\n",
         "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": "variable part"},
    ]


def test_set_cache_prefixes_accepts_none_as_clear(monkeypatch):
    """`None` clears any previously registered prefixes -- an explicit
    opt-out, distinct from passing an empty list."""
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "some-project")
    _fake_google_auth_package(monkeypatch)
    seen = {}

    class FakeMessages:
        def create(self, **kw):
            seen["content"] = kw["messages"][0]["content"]
            return SimpleNamespace(
                content=[SimpleNamespace(text="ok", type="text")],
                usage=SimpleNamespace(input_tokens=1, output_tokens=1),
            )

    fake_client = SimpleNamespace(messages=FakeMessages())
    monkeypatch.setitem(sys.modules, "anthropic", SimpleNamespace(
        AnthropicVertex=lambda **kw: fake_client, RateLimitError=Exception,
        APIConnectionError=Exception, InternalServerError=Exception,
    ))

    caller = VertexCaller(project="some-project")
    caller.set_cache_prefixes(["stable prefix"])
    caller.set_cache_prefixes(None)
    caller("stable prefix and the rest")

    assert seen["content"] == "stable prefix and the rest"


def test_default_timeout_is_passed_to_the_anthropic_vertex_client(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "some-project")
    _fake_google_auth_package(monkeypatch)

    constructed = {}

    def fake_ctor(**kwargs):
        constructed.update(kwargs)
        return SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: None))

    monkeypatch.setitem(sys.modules, "anthropic", SimpleNamespace(
        AnthropicVertex=fake_ctor, RateLimitError=Exception, APIConnectionError=Exception,
        InternalServerError=Exception,
    ))

    from mfdoc.vertex_caller import DEFAULT_TIMEOUT_S

    caller = VertexCaller(project="some-project")
    assert caller.timeout == DEFAULT_TIMEOUT_S == 600
    assert constructed["timeout"] == DEFAULT_TIMEOUT_S


def test_explicit_timeout_overrides_the_default_for_vertex(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "some-project")
    _fake_google_auth_package(monkeypatch)

    constructed = {}

    def fake_ctor(**kwargs):
        constructed.update(kwargs)
        return SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: None))

    monkeypatch.setitem(sys.modules, "anthropic", SimpleNamespace(
        AnthropicVertex=fake_ctor, RateLimitError=Exception, APIConnectionError=Exception,
        InternalServerError=Exception,
    ))

    caller = VertexCaller(project="some-project", timeout=30)
    assert caller.timeout == 30
    assert constructed["timeout"] == 30


def test_cmd_batch_routes_to_vertex_caller_when_provider_is_vertex(cli_args, tmp_path, monkeypatch):
    project_dir = Path(cli_args.config).parent
    if not (project_dir / "reference").exists():
        shutil.copytree(REPO_ROOT / "reference", project_dir / "reference")
        shutil.copytree(REPO_ROOT / "templates", project_dir / "templates")

    constructed = {}

    class FakeVertexCaller:
        def __init__(self, model=None, project=None, region=None, timeout=None):
            constructed.update(model=model, project=project, region=region, timeout=timeout)

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
    assert constructed == {
        "model": "claude-sonnet-4-5", "project": "test-proj", "region": "us-east5", "timeout": None,
    }
