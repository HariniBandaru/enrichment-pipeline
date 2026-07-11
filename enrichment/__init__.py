"""Company-domain enrichment pipeline.

A small, scale-conscious pipeline that reads company domains, enriches them via
the (mock) provider API, normalises the messy responses, and writes structured
output plus an operator-facing run summary.
"""

__version__ = "0.1.0"
