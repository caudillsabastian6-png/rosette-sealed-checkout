"""eBay reader adapter (INTEGRATIONS.md §4). READ-ONLY in this build.

Wraps the house connectors (custom.ebay Sell APIs + custom.ebay-trading
Trading API) as an engine reader producing listing/order snapshots.
Credentials stay in the house connector layer; nothing is stored here.
Falls back to data/demo/ seeds when EBAY_READ_ENABLED is false — which is
the demo's default and current state: the engine's read() verb serves the
sanitized sandbox copies in data/demo/listings.json.
"""
