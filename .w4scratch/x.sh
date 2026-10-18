#!/bin/bash
cd /d/workspace/sandbox
cd /d/workspace/sandbox
echo '=== esc defs ==='
grep -n 'function esc\|escAttr\|function badge' app/static/js/admin.js
echo '=== onclick lines ==='
grep -rn 'onclick=' app/templates/certificates.html app/templates/connectors.html app/templates/identities.html
echo '=== esc usage counts ==='
grep -c 'esc(' app/templates/*.html
