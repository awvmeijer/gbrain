#!/bin/bash
# Moved 2026-09-24 to ~/brains-console/menubar/brains.30s.sh, which renders the
# console API's GET /api/status (gbrain, Postgres, Ether, Straat, sampler, nightly,
# approvals waiting). SwiftBar's PluginDirectory now points there. forge.60s.sh was
# removed: it watched forge2 (:8800), retired 2026-09-21.
#
# Kept as a shim so anything still pointing at this folder shows the same menu.
exec "$HOME/brains-console/menubar/brains.30s.sh"
