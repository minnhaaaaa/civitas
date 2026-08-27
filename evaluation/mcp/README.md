# MCP evaluation

This package wraps the immutable golden scenarios in the MCP-facing acceptance
path. It requires no MCP SDK, provider, network, model, or database.

`run_mcp_scenario` keeps deterministic solver/Jury/execution correctness in
`decision`. The optional `presentation_is_safe` flag only checks whether a
human-facing summary makes an unsafe approval claim; it never contributes to a
decision score.
