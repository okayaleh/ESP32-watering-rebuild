"""Restore normal script-directory lookup for this isolated embedded Python."""
import os
import sys

sys.path.insert(0, os.getcwd())
if sys.argv and sys.argv[0] and not sys.argv[0].startswith("-"):
    sys.path.insert(0, os.path.dirname(os.path.abspath(sys.argv[0])))
