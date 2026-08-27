"""Deliberately small STDIO entry point.

Deployment composition supplies a real ProductService and authenticated identity.
The production CLI is added with the application facade; this module prevents an
accidental unauthenticated HTTP process from being used as a server entry point.
"""

raise SystemExit(
    "Configure Civitas with an application ProductService before starting its MCP transport."
)
