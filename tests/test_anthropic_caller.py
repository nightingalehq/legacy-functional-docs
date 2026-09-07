"""Guards for AnthropicCaller's error-path contract and its configurable
request timeout (#80) -- no real network call or API key is exercised
here, same isolation approach as test_vertex_caller.py: fake the
`anthropic` module entirely via sys.modules so nothing needs the real
package installed to run these."""

from __future__ import annotations

import sys
import types

import pytest

from mfdoc.anthropic_caller import DEFAULT_TIMEOUT_S, AnthropicCaller


def test_missing_anthropic_package_raises_install_hint(monkeypatch):
    monkeypatch.setitem(sys.modules, "anthropic", None)
    with pytest.raises(RuntimeError, match=r"pip install 'mfdoc\[batch\]'"):
        AnthropicCaller()


def test_default_timeout_is_passed_to_the_anthropic_client(monkeypatch):
    constructed = {}

    def fake_anthropic_ctor(**kwargs):
        constructed.update(kwargs)
        return types.SimpleNamespace(messages=types.SimpleNamespace(create=lambda **kw: None))

    monkeypatch.setitem(sys.modules, "anthropic", types.SimpleNamespace(Anthropic=fake_anthropic_ctor))

    caller = AnthropicCaller()
    assert caller.timeout == DEFAULT_TIMEOUT_S == 600
    assert constructed["timeout"] == DEFAULT_TIMEOUT_S


def test_explicit_timeout_overrides_the_default(monkeypatch):
    constructed = {}

    def fake_anthropic_ctor(**kwargs):
        constructed.update(kwargs)
        return types.SimpleNamespace(messages=types.SimpleNamespace(create=lambda **kw: None))

    monkeypatch.setitem(sys.modules, "anthropic", types.SimpleNamespace(Anthropic=fake_anthropic_ctor))

    caller = AnthropicCaller(timeout=30)
    assert caller.timeout == 30
    assert constructed["timeout"] == 30


def test_explicit_timeout_is_passed_alongside_an_api_key(monkeypatch):
    constructed = {}

    def fake_anthropic_ctor(**kwargs):
        constructed.update(kwargs)
        return types.SimpleNamespace(messages=types.SimpleNamespace(create=lambda **kw: None))

    monkeypatch.setitem(sys.modules, "anthropic", types.SimpleNamespace(Anthropic=fake_anthropic_ctor))

    AnthropicCaller(api_key="sk-test", timeout=45)
    assert constructed == {"api_key": "sk-test", "timeout": 45}
