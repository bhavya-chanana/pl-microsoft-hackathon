"""Football Intelligence: sensor-first match data (vests, ball sensor, video AI), ingest, ledger and as-of views.

Layers, in data-flow order:
- ``ingest``: idempotent write paths shared by real hardware and the simulator.
- ``fusion``: turns ball impacts, vision observations and operator input into ledger revisions.
- ``engine``: as-of reads. Nothing returned is later than the viewer's playhead.
- ``api``: the HTTP surface under ``/api/fi``.
"""
