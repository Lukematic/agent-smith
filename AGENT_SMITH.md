# Deprecated compatibility pointer

`AGENT_SMITH.md` remains readable for old consumers that still resolve the former
Agent Smith constitution path. It is not a separately maintained constitution.

The canonical A.W.I.N.O. constitution is [`AWINO.md`](AWINO.md). New consumers
must load `AWINO.md` directly. Existing integrations should migrate when practical;
the `src/smith` package and `.smith` state paths remain compatibility implementation
details and are not being renamed.
