"""Support ``python -m sahajmails``.

1.x imported the Streamlit app module here and called its ``main()`` directly,
outside ``streamlit run``. That produced "missing ScriptRunContext" warnings and
no UI. This dispatches to the real CLI, so ``python -m sahajmails`` and the
``sahajmails`` console script behave identically.
"""

from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
