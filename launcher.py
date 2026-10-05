"""Optional frozen executable entrypoint: supervisor by default, explicit child.

Keep the deployed source tree and reviewed .env beside it as documented. Compiled
runtime identity includes the EXE digest; source-only evidence is not reusable.
"""

from __future__ import annotations

import sys


def main(argv=None):
    values = list(sys.argv[1:] if argv is None else argv)
    if "--runtime-child" in values:
        values.remove("--runtime-child")
        from app.bot import main as runtime_main

        return runtime_main(values)
    from watchdog import main as watchdog_main

    return watchdog_main(values)


if __name__ == "__main__":
    raise SystemExit(main())
