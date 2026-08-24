"""A.W.I.N.O.: agentic-engineering harness.

The package holds the deterministic half of A.W.I.N.O. Anything a script can do
reliably does not belong in a prompt: that is the ``MODEL_DOES_DETERMINISM``
guard from the tool-authoring gate, applied to A.W.I.N.O. itself.
"""

from smith.paths import SmithPaths

__all__ = ["SmithPaths", "__version__"]
__version__ = "0.3.0"
