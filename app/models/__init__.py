"""Model package init — deliberately minimal (the repo imports models by full path).

The ONLY registration this barrel performs is ``refresh_session``: the CR
§4.10 refresh-revocation table must be visible to ``Base.metadata.create_all``
and to alembic autogenerate/env. The repo convention is to add each new model
to ``app/main.py``'s import block; doing it here instead keeps the registration
transitive (any ``app.models.*`` import runs this file, including main.py's
block and alembic's env), so no second edit site can drift. Do not grow this
into a general barrel — every other model stays on the main.py convention.
"""
from app.models.refresh_session import RefreshSession  # noqa: F401
