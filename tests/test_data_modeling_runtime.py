"""Tests for DataModelingRuntime (P2-5 structured_modeling scene)."""
import pytest
from app.models.sandbox_session import SandboxMode
from app.services.sandbox_runtime import (
    SceneRuntime, SceneRuntimeFactory, DataModelingRuntime,
)


class TestDataModelingRuntimeRegistration:

    def test_registered_in_factory(self):
        """DataModelingRuntime should be returned for STRUCTURED_MODELING mode."""
        runtime = SceneRuntimeFactory.create(SandboxMode.STRUCTURED_MODELING)
        assert isinstance(runtime, DataModelingRuntime)

    def test_registered_from_string(self):
        runtime = SceneRuntimeFactory.create("structured_modeling")
        assert isinstance(runtime, DataModelingRuntime)

    def test_mode_is_structured_modeling(self):
        runtime = DataModelingRuntime()
        assert runtime.mode == SandboxMode.STRUCTURED_MODELING

    def test_is_subclass_of_scene_runtime(self):
        assert issubclass(DataModelingRuntime, SceneRuntime)


class TestDataModelingPreExecute:

    @pytest.mark.asyncio
    async def test_allows_select(self):
        runtime = DataModelingRuntime()
        result = await runtime.pre_execute("SELECT * FROM t", {})
        assert "SELECT" in result

    @pytest.mark.asyncio
    async def test_allows_create_table(self):
        runtime = DataModelingRuntime()
        result = await runtime.pre_execute("CREATE TABLE model AS SELECT 1", {})
        assert "CREATE TABLE" in result

    @pytest.mark.asyncio
    async def test_allows_insert(self):
        runtime = DataModelingRuntime()
        result = await runtime.pre_execute("INSERT INTO model VALUES (1, 2)", {})
        assert "INSERT" in result

    @pytest.mark.asyncio
    async def test_allows_drop_table(self):
        runtime = DataModelingRuntime()
        result = await runtime.pre_execute("DROP TABLE IF EXISTS tmp", {})
        assert "DROP TABLE" in result

    @pytest.mark.asyncio
    async def test_allows_alter_table(self):
        runtime = DataModelingRuntime()
        result = await runtime.pre_execute("ALTER TABLE t ADD col INT", {})
        assert "ALTER TABLE" in result

    @pytest.mark.asyncio
    async def test_blocks_grant(self):
        runtime = DataModelingRuntime()
        with pytest.raises(ValueError, match="Privilege escalation not allowed"):
            await runtime.pre_execute("GRANT ALL ON t TO public", {})

    @pytest.mark.asyncio
    async def test_blocks_revoke(self):
        runtime = DataModelingRuntime()
        with pytest.raises(ValueError, match="Privilege escalation not allowed"):
            await runtime.pre_execute("REVOKE SELECT ON t FROM user1", {})

    @pytest.mark.asyncio
    async def test_blocks_create_user(self):
        runtime = DataModelingRuntime()
        with pytest.raises(ValueError, match="Privilege escalation not allowed"):
            await runtime.pre_execute("CREATE USER hacker", {})

    @pytest.mark.asyncio
    async def test_blocks_drop_user(self):
        runtime = DataModelingRuntime()
        with pytest.raises(ValueError, match="Privilege escalation not allowed"):
            await runtime.pre_execute("DROP USER someone", {})

    @pytest.mark.asyncio
    async def test_empty_code_passthrough(self):
        runtime = DataModelingRuntime()
        result = await runtime.pre_execute("", {})
        assert result == ""

    @pytest.mark.asyncio
    async def test_whitespace_only_passthrough(self):
        runtime = DataModelingRuntime()
        result = await runtime.pre_execute("   ", {})
        assert result == "   "


class TestDataModelingPostExecute:

    @pytest.mark.asyncio
    async def test_truncates_output_over_limit(self):
        runtime = DataModelingRuntime()
        lines = "\n".join([f"row{i}" for i in range(200)])
        result = await runtime.post_execute({"output": lines}, {"max_output_rows": 50})
        assert result.get("truncated") is True
        assert "truncated, 200 total rows" in result["output"]

    @pytest.mark.asyncio
    async def test_no_truncation_within_limit(self):
        runtime = DataModelingRuntime()
        result = await runtime.post_execute(
            {"output": "row1\nrow2"}, {"max_output_rows": 100},
        )
        assert "truncated" not in result

    @pytest.mark.asyncio
    async def test_default_max_rows(self):
        runtime = DataModelingRuntime()
        lines = "\n".join([f"r{i}" for i in range(10001)])
        result = await runtime.post_execute({"output": lines}, {})
        assert result.get("truncated") is True

    @pytest.mark.asyncio
    async def test_warns_on_select_star_without_limit(self):
        runtime = DataModelingRuntime()
        result = await runtime.post_execute(
            {"output": "SELECT * FROM secrets"}, {},
        )
        assert result.get("warning") is not None
        assert "raw data" in result["warning"].lower()

    @pytest.mark.asyncio
    async def test_no_warning_with_limit_clause(self):
        runtime = DataModelingRuntime()
        result = await runtime.post_execute(
            {"output": "SELECT * FROM t LIMIT 100"}, {},
        )
        assert result.get("warning") is None

    @pytest.mark.asyncio
    async def test_empty_output_passthrough(self):
        runtime = DataModelingRuntime()
        result = await runtime.post_execute({"output": ""}, {})
        assert result == {"output": ""}
