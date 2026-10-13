"""Evaluation pipeline for the Qwen/JARVIS fine-tune. See README.md.

Nothing in this package loads model weights at import time; torch / transformers are imported lazily and only by the HF generator wrapper
and the loss evaluator."""
