"""Temporary-directory removal that tolerates a briefly held file on Windows.

A panel handler thread can still be closing its SQLite connection after the
client has read the response; Windows refuses to delete a file that is open.
"""
import time


def cleanup(temp, timeout=5.0):
    """Call temp.cleanup(), retrying PermissionError until timeout seconds pass."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            temp.cleanup()
            return
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.05)
