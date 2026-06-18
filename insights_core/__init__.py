"""Shared core for every firm's insights scanner.

Firm packages (`firms/<slug>/<slug>_insights/`) are thin shims that point
`insights_core` at their own `firm_root` (for `sources.yaml` + `data/`) and pass
in their adapter registry. All ingest / normalize / dedup / enrich / persistence
logic lives here, so a change is made once, not 31 times.
"""
