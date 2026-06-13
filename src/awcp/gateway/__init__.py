"""AWCP Gateway — a single FastAPI app that exposes two route groups:

  * USER routes (/user/*)             — the human entry point: list + ask.
  * AWCP control-plane routes (/awcp/*) — the registry/governance surfaces.

See awcp.gateway.app for the composed application.
"""
