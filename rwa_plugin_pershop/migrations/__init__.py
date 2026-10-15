import os


def versions_path() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "versions")
