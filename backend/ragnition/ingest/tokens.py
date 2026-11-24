"""Count tokens exactly as the embedding model sees them.

We load the BGE tokenizer.json that fastembed already downloaded with the model
(no extra download). BGE uses WordPiece, which first splits on whitespace and
punctuation, so the token count of a text equals the sum of the counts of its
whitespace-separated words. That lets us count per word, with a cache.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import lru_cache
from pathlib import Path

TokenCounter = Callable[[str], int]

# bge-base reads at most 512 tokens, two of which are [CLS] and [SEP].
MODEL_MAX_TOKENS = 510


def find_tokenizer_file(model_cache_dir: Path, pattern: str = "models--Qdrant--bge-base-en-v1.5*") -> Path:
    hits = sorted(model_cache_dir.glob(f"{pattern}/snapshots/*/tokenizer.json"))
    if not hits:
        raise FileNotFoundError(
            f"no BGE tokenizer.json under {model_cache_dir}; run scripts/smoke_providers.py once to download the model"
        )
    return hits[-1]


def load_bge_counter(model_cache_dir: Path) -> TokenCounter:
    from tokenizers import Tokenizer

    tok = Tokenizer.from_file(str(find_tokenizer_file(model_cache_dir)))
    # tokenizer.json may enable truncation at 512, which would hide oversize chunks.
    tok.no_truncation()
    tok.no_padding()

    @lru_cache(maxsize=200_000)
    def count_word(word: str) -> int:
        return len(tok.encode(word, add_special_tokens=False).ids)

    def count(text: str) -> int:
        return sum(count_word(w) for w in text.split())

    return count


def whitespace_counter(text: str) -> int:
    """Stand-in for tests: one token per word."""
    return len(text.split())
