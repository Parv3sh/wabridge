"""Entry point for `python -m wabridge` and for the PyInstaller-frozen sidecar.

Absolute import on purpose: PyInstaller runs this file as a top-level script (no parent
package), where a relative import would raise ImportError.
"""

import sys

from wabridge.cli import main

if __name__ == "__main__":
    sys.exit(main())
