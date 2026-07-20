"""Tests for ZDR startup assertion (RFC-011 D6 / ISS-33)."""

import dataclasses
from unittest.mock import patch

import pytest

import pageindex_mcp.server as server_module

from pageindex_mcp.config import _is_zdr_allowlisted


def test_zdr_allowlisted_azure():
    assert _is_zdr_allowlisted("https://my-resource.openai.azure.com/v1") is True


def test_zdr_allowlisted_bedrock():
    assert _is_zdr_allowlisted("https://bedrock-runtime.eu-central-1.amazonaws.com") is True


def test_zdr_allowlisted_openai_eu():
    assert _is_zdr_allowlisted("https://eu.api.openai.com/v1") is True


def test_zdr_rejects_default_openai():
    assert _is_zdr_allowlisted("https://api.openai.com/v1") is False


def test_zdr_rejects_none():
    assert _is_zdr_allowlisted(None) is False


def test_zdr_rejects_empty():
    assert _is_zdr_allowlisted("") is False


async def test_lifespan_raises_when_pii_corpus_with_non_zdr_url():
    bad_settings = dataclasses.replace(
        server_module.settings,
        pii_corpus=True,
        openai_base_url="https://api.openai.com/v1",
    )
    with patch.object(server_module, "settings", bad_settings):
        with pytest.raises(RuntimeError, match="ZDR allow-list"):
            async with server_module._lifespan_with_scrape(None):
                pass
