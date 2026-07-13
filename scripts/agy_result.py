"""Pure result classification shared by AGY dispatch tests."""

import argparse
import sys

ERROR_PREFIX = "AGY_PTY_ERROR:"


def classify(exit_code, output):
    if exit_code != 0 or ERROR_PREFIX in (output or ""):
        return "failed"
    if not (output or "").strip():
        return "failed"
    return "done"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--exit-code", type=int, required=True)
    args = parser.parse_args()
    print(classify(args.exit_code, sys.stdin.read()))


if __name__ == "__main__":
    main()
