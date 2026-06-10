# OPA Policy Directory

This directory is mounted into the local OPA container by `docker-compose.yml`.

Runtime contract policies are pushed to OPA by the application. `system.rego`
keeps the directory non-empty and fails closed by default.
