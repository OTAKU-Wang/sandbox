#!/bin/bash
# Usage: run.sh <name> -- <bash commands on following lines>
cd /d/workspace/sandbox || exit 3
shift 2>/dev/null
cat "$0".cmd 2>/dev/null
