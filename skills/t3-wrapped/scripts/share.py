#!/usr/bin/env python3
"""Open a new X post and the cards, selected and ready to drag into it.

X's compose link can't attach images, so this opens the composer in the browser
and the card PNGs in the file manager, already selected in deck order. Nothing
is posted; the user drags the cards in and clicks Post.
"""
from __future__ import annotations

import argparse
import platform
import subprocess
import sys
import webbrowser
from pathlib import Path


def reveal(cards: list[Path], folder: Path) -> None:
    if platform.system() == "Darwin":
        items = ", ".join(f'POSIX file "{p}"' for p in cards)
        script = f'tell application "Finder"\nactivate\nreveal {{{items}}}\nselect {{{items}}}\nend tell'
        if subprocess.run(["osascript", "-e", script], capture_output=True).returncode == 0:
            return
        subprocess.run(["open", str(folder)])
    elif platform.system() == "Windows":
        subprocess.run(["explorer", str(folder)])
    else:
        subprocess.run(["xdg-open", str(folder)])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("folder", help="Output folder that holds cards/")
    folder = Path(ap.parse_args().folder).expanduser()
    cards = sorted((folder / "cards").glob("*.png"))
    if not cards:
        sys.exit(f"No cards in {folder / 'cards'}; run render.py first.")
    url = "https://x.com/intent/post"
    if platform.system() == "Darwin":
        # -g loads the tab without raising the browser, which would otherwise land on top of Finder.
        subprocess.run(["open", "-g", url])
    else:
        webbrowser.open(url)
    reveal(cards, folder / "cards")
    if platform.system() == "Darwin":
        print(f"Drag the {len(cards)} selected cards into the post, then click Post.")
    else:
        print(f"In the cards folder, press Ctrl+A and drag all {len(cards)} cards into the post, then click Post.")


if __name__ == "__main__":
    main()
