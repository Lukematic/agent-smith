"""Agent Smith: agentic-engineering harness.

The package holds the deterministic half of Smith. Anything a script can do
reliably does not belong in a prompt: that is the ``MODEL_DOES_DETERMINISM``
guard from the tool-authoring gate, applied to Smith itself.
"""

from smith.paths import SmithPaths

__all__ = ["SmithPaths", "__version__"]
__version__ = "0.3.0"
