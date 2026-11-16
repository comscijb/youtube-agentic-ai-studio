"""List the sound-effect tags still missing from assets/sfx/ and the files without a registered licence (Phase 4.2-D).

Run:  python -m scripts.sfx_library
Nothing is downloaded. Use only licences that allow commercial use without attribution (CC0, or the YouTube Audio
Library's sound effects: studio.youtube.com → Audio Library → Sound effects). Put each file in assets/sfx/<tag>/ and
register it in assets/sfx/LICENSES.json:
    {"door/door_close_01.wav": {"source": "YouTube Audio Library", "license": "YouTube Audio Library", "url": "..."}}
"""
from __future__ import annotations

import argparse
import sys


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.parse_args(argv)
    from video import sfx
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass
    have = sfx.library()
    print(f"Pasta: {sfx.folder()}")
    print(f"Etiquetas com som licenciado: {', '.join(f'{tag} ({len(files)})' for tag, files in have.items()) or 'nenhuma'}")
    missing = sfx.missing_tags()
    print(f"Faltam ({len(missing)}): {', '.join(missing) or 'nenhuma'}")
    loose = sfx.unregistered()
    if loose:
        print("Arquivos sem licença registrada em LICENSES.json (não são usados): " + ", ".join(loose))
    print("Baixe à mão (nunca por script): YouTube Audio Library → Efeitos sonoros, ou sons CC0. "
          "Registre cada arquivo em assets/sfx/LICENSES.json.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
