#!/bin/bash
# S1 acceptance: all services reachable + healthy.
echo "--- /health ---"
curl -s -w "\nHTTP:%{http_code}\n" http://127.0.0.1:8000/health
echo "--- /metrics (first 3 lines) ---"
curl -s http://127.0.0.1:8000/metrics | head -3
echo "--- frontend :3000 ---"
curl -s -o /dev/null -w "HTTP:%{http_code}\n" http://127.0.0.1:3000/
curl -s http://127.0.0.1:3000/ | head -c 220
echo
echo "--- container states ---"
docker ps --format "{{.Names}}: {{.Status}}" | sort
echo S1_CHECK_DONE
