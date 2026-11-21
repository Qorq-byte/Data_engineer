"""Capture document rankings from a frozen business dataset using local embeddings."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

import httpx

from app.evaluation.acceptance_data import SPLITS, business_source_paths, load_business_dataset
from app.evaluation.artifacts import MAX_INPUT_BYTES, protect_inputs, write_atomic
from app.evaluation.retrieval_capture import capture_document_rankings
from app.knowledge.retrieval.embedding import (
    EmbeddingProvider,
    MockEmbeddingProvider,
    OllamaEmbeddingProvider,
)


async def run(args: argparse.Namespace) -> None:
    protect_inputs(args.output, business_source_paths(args.dataset_manifest))
    dataset = load_business_dataset(args.dataset_manifest)
    async with httpx.AsyncClient(timeout=30, trust_env=False, follow_redirects=False) as client:
        provider: EmbeddingProvider = MockEmbeddingProvider()
        if args.ollama_model is not None:
            provider = OllamaEmbeddingProvider(
                model=args.ollama_model, base_url=OllamaEmbeddingProvider.DEFAULT_BASE_URL,
                dimension=args.ollama_dimension, client=client,
            )
        capture = await capture_document_rankings(
            dataset, provider, split=args.split, k=args.k, chunk_top_k=args.chunk_top_k,
        )
    if len(json.dumps(capture, ensure_ascii=False).encode("utf-8")) > MAX_INPUT_BYTES:
        raise ValueError("Rankings exceed the scoring input size limit")
    write_atomic(args.output, capture, indent=None)
    print(json.dumps({"profile": capture["profile"], "split": args.split,
                      "query_count": len(capture["rankings"]),
                      "embedding_model": capture["embedding_model"]}))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--split", choices=SPLITS, default="holdout")
    parser.add_argument("--k", type=int, default=3)
    parser.add_argument("--chunk-top-k", type=int, default=10)
    parser.add_argument("--ollama-model", help="Use this model on the local Ollama server")
    parser.add_argument("--ollama-dimension", type=int, default=OllamaEmbeddingProvider.DEFAULT_DIM)
    args = parser.parse_args(argv)
    previous = logging.root.manager.disable
    logging.disable(max(previous, logging.CRITICAL))
    try:
        if args.ollama_model is not None and (
            not args.ollama_model.strip() or len(args.ollama_model) > 249
        ):
            raise ValueError("Invalid Ollama model name")
        asyncio.run(run(args))
        return 0
    except Exception:
        print("Document ranking capture failed; output was not replaced.", file=sys.stderr)
        return 2
    finally:
        logging.disable(previous)


if __name__ == "__main__":
    raise SystemExit(main())
