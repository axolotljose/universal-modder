"""Test suite for the Ape Escape 3 co-op mod.

Run with either::

    python -m pytest tests -q
    python -m unittest discover -s tests

Nothing here needs PCSX2, a PlayStation 2, an ISO, or the network beyond
loopback: the emulator is a real socket server that speaks PINE, and the console
is an R5900 interpreter. What they cannot check is whether the addresses in a
user's own table are right -- that needs the game itself.
"""
