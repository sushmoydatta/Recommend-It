"""Run Recommend-It without installing it:  python cli.py --help"""

import sys

from recommend_it.cli import main

if __name__ == "__main__":
    sys.exit(main())
