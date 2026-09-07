"""The public analysis path uses exact recorded bytes, not relaxed validation."""
import subprocess

import pytest

from research.softwarex import build_public_release as public


def test_exact_recorded_helper_preserved_across_git_newlines():
    recorded = (public.ROOT / "research/sqj/source-final/tools/product_generalization_benchmark.py").read_bytes()
    normalized = recorded.replace(b"\r\n", b"\n")
    assert public.exact_analysis_helper(normalized.replace(b"\n", b"\r\n")) == recorded


def test_semantic_change_cannot_be_hidden_as_newline_adaptation():
    recorded = (public.ROOT / "research/sqj/source-final/tools/product_generalization_benchmark.py").read_bytes()
    with pytest.raises(ValueError, match="beyond recorded physical newlines"):
        public.exact_analysis_helper(recorded + b"\n# different implementation\n")
