"""PyInstaller entry point (the package itself uses relative imports).

``--version`` prints the version; ``--self-test`` checks that the frozen
bundle can load Qt, mutagen, Pillow and the app modules, then exits.  Build
scripts and CI use these to verify a bundle without opening a window.
"""

import os
import sys


def self_test() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import io

    import mutagen  # noqa: F401  (imports are the test)
    from PIL import Image
    from PySide6.QtWidgets import QApplication

    from serato_analyzer.artwork import apply, embed, service  # noqa: F401
    from serato_analyzer.gui import main_window  # noqa: F401
    from serato_analyzer.platforms import current_platform

    buf = io.BytesIO()
    Image.new("RGB", (64, 64)).save(buf, "JPEG")
    app = QApplication([])
    pf = current_platform()
    print(f"self-test ok: {pf.name} {pf.machine_arch()} data={pf.data_dir()}")
    app.quit()
    return 0


if __name__ == "__main__":
    if "--version" in sys.argv:
        from serato_analyzer import __version__

        print(__version__)
        sys.exit(0)
    if "--self-test" in sys.argv:
        sys.exit(self_test())
    from serato_analyzer.gui.app import run

    sys.exit(run(sys.argv))
