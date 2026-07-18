"""Harness runtime framework — WorkflowRunner, config loading, permissions.

The Harness is the control plane that enforces the 4 hard invariants:
  1. LLM doesn't decide the next step — node_order does.
  2. Permissions cannot be bypassed.
  3. Read-only by default.
  4. Every execution has a hard retry/iteration limit.
"""
