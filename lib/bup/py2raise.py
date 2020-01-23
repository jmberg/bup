
import sys

# This file exists because the raise syntax is completely incompatible
# with Python 3.

def reraise(ex, tb=None):
    if tb is None:
        tb = sys.exc_info()[2]
    raise ex, None, tb
