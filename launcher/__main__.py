import sys

if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()

from .main import main

if __name__ == "__main__":
    sys.exit(main())
