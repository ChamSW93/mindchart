#!/bin/bash
# Run MindChart's tests with Apple's built-in Python (the one installed copies use), so nothing
# that only works with a newer Python can slip through.
cd "$(dirname "$0")/.."
/usr/bin/python3 -m unittest discover -s tests -t tests "$@"
