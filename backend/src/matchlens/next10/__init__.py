"""Next-10-seconds companion: live fan-out, read access and the video chain around ``store/next10.py``.

Everything here is off unless ``MATCHLENS_NEXT10=1`` (the API) or you run the CLIs in ``vision/live.py`` and
``vision/jobs.py`` yourself. ``hub``, ``repo`` and ``frames`` need nothing beyond the base install; ``chain`` and
``media`` need the ``vision`` dependency group.
"""
