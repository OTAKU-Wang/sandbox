"""P2-5: Scene Runtime Factory tests."""
import pytest
from app.models.sandbox_session import SandboxMode
from app.services.sandbox_runtime import (
    SceneRuntime, SceneRuntimeFactory,
    StructuredQueryRuntime, LLMTrainingRuntime,
    ProductDevRuntime, StructuredAppRuntime,
    DataModelingRuntime,
    FederatedRuntime,
)


class TestSandboxMode:

    def test_six_core_modes(self):
        assert SandboxMode.STRUCTURED_QUERY.value == "structured_query"
        assert SandboxMode.STRUCTURED_MODELING.value == "structured_modeling"
        assert SandboxMode.STRUCTURED_APP.value == "structured_app"
        assert SandboxMode.LLM_TRAINING.value == "llm_training"
        assert SandboxMode.PRODUCT_DEV.value == "product_dev"
        assert SandboxMode.JOINT_FEDERATED.value == "joint_federated"

    def test_mode_count(self):
        assert len(SandboxMode) == 6


class TestSceneRuntimeFactory:

    def test_create_query_runtime(self):
        runtime = SceneRuntimeFactory.create(SandboxMode.STRUCTURED_QUERY)
        assert isinstance(runtime, StructuredQueryRuntime)

    def test_create_training_runtime(self):
        runtime = SceneRuntimeFactory.create(SandboxMode.LLM_TRAINING)
        assert isinstance(runtime, LLMTrainingRuntime)

    def test_create_product_dev_runtime(self):
        runtime = SceneRuntimeFactory.create(SandboxMode.PRODUCT_DEV)
        assert isinstance(runtime, ProductDevRuntime)

    def test_create_app_runtime(self):
        runtime = SceneRuntimeFactory.create(SandboxMode.STRUCTURED_APP)
        assert isinstance(runtime, StructuredAppRuntime)

    def test_create_modeling_runtime(self):
        runtime = SceneRuntimeFactory.create(SandboxMode.STRUCTURED_MODELING)
        assert isinstance(runtime, DataModelingRuntime)

    def test_create_federated_runtime(self):
        runtime = SceneRuntimeFactory.create(SandboxMode.JOINT_FEDERATED)
        assert isinstance(runtime, FederatedRuntime)

    def test_create_from_string(self):
        runtime = SceneRuntimeFactory.create("structured_query")
        assert isinstance(runtime, StructuredQueryRuntime)

    def test_register_custom_runtime(self):
        class CustomRuntime(SceneRuntime):
            def __init__(self):
                super().__init__(SandboxMode.JOINT_FEDERATED)

        SceneRuntimeFactory.register("joint_federated", CustomRuntime)
        runtime = SceneRuntimeFactory.create(SandboxMode.JOINT_FEDERATED)
        assert isinstance(runtime, CustomRuntime)

    def test_all_modes_creatable(self):
        for mode in SandboxMode:
            runtime = SceneRuntimeFactory.create(mode)
            assert isinstance(runtime, SceneRuntime)
            assert runtime.mode == mode


class TestStructuredQueryRuntime:

    @pytest.mark.asyncio
    async def test_pre_execute_rejects_drop(self):
        runtime = StructuredQueryRuntime()
        with pytest.raises(ValueError, match="DDL/DML not allowed"):
            await runtime.pre_execute("DROP TABLE users", {})

    @pytest.mark.asyncio
    async def test_pre_execute_rejects_alter(self):
        runtime = StructuredQueryRuntime()
        with pytest.raises(ValueError, match="DDL/DML not allowed"):
            await runtime.pre_execute("ALTER TABLE users ADD col INT", {})

    @pytest.mark.asyncio
    async def test_pre_execute_adds_limit(self):
        runtime = StructuredQueryRuntime()
        result = await runtime.pre_execute("SELECT * FROM users", {})
        assert "LIMIT" in result
        assert "10000" in result

    @pytest.mark.asyncio
    async def test_pre_execute_preserves_existing_limit(self):
        runtime = StructuredQueryRuntime()
        result = await runtime.pre_execute("SELECT * FROM users LIMIT 50", {})
        assert "LIMIT 50" in result
        assert result.count("LIMIT") == 1

    @pytest.mark.asyncio
    async def test_pre_execute_custom_max_rows(self):
        runtime = StructuredQueryRuntime()
        result = await runtime.pre_execute("SELECT * FROM users", {"max_output_rows": 100})
        assert "LIMIT 100" in result

    @pytest.mark.asyncio
    async def test_pre_execute_strips_semicolon(self):
        runtime = StructuredQueryRuntime()
        result = await runtime.pre_execute("SELECT * FROM users;", {})
        assert not result.strip().endswith(";")

    @pytest.mark.asyncio
    async def test_post_execute_truncates_output(self):
        runtime = StructuredQueryRuntime()
        lines = "\n".join([f"row{i}" for i in range(200)])
        result = await runtime.post_execute({"output": lines}, {"max_output_rows": 50})
        assert "truncated" in result
        assert result["truncated"] is True
        assert result["output"].count("\n") < 200

    @pytest.mark.asyncio
    async def test_post_execute_no_truncation_within_limit(self):
        runtime = StructuredQueryRuntime()
        result = await runtime.post_execute({"output": "row1\nrow2"}, {"max_output_rows": 100})
        assert "truncated" not in result


class TestLLMTrainingRuntime:

    @pytest.mark.asyncio
    async def test_pre_execute_injects_epoch_guard(self):
        runtime = LLMTrainingRuntime()
        result = await runtime.pre_execute("print('training')", {"max_epochs": 50})
        assert "CDS_MAX_EPOCHS" in result
        assert "50" in result
        assert "print('training')" in result


class TestProductDevRuntime:

    @pytest.mark.asyncio
    async def test_post_execute_warns_on_raw_data(self):
        runtime = ProductDevRuntime()
        result = await runtime.post_execute(
            {"output": "SELECT * FROM secrets"},
            {},
        )
        assert result.get("warning") is not None


class TestStructuredAppRuntime:

    @pytest.mark.asyncio
    async def test_post_execute_caps_response_size(self):
        runtime = StructuredAppRuntime()
        big_output = "x" * (2 * 1024 * 1024)  # 2MB
        result = await runtime.post_execute(
            {"output": big_output},
            {"max_response_mb": 1},
        )
        assert result.get("truncated") is True
        assert len(result["output"]) < len(big_output)

    @pytest.mark.asyncio
    async def test_post_execute_no_truncation_small_output(self):
        runtime = StructuredAppRuntime()
        result = await runtime.post_execute(
            {"output": "small response"},
            {"max_response_mb": 10},
        )
        assert "truncated" not in result


class TestFederatedRuntime:

    @pytest.mark.asyncio
    async def test_pre_execute_rejects_direct_network_python(self):
        runtime = FederatedRuntime()
        with pytest.raises(ValueError, match="Direct network access"):
            await runtime.pre_execute("import requests\nrequests.get('https://remote')", {"language": "python"})

    @pytest.mark.asyncio
    async def test_pre_execute_blocks_raw_select_star(self):
        runtime = FederatedRuntime()
        with pytest.raises(ValueError, match=r"SELECT \*"):
            await runtime.pre_execute("SELECT * FROM remote_table", {"language": "sql"})

    @pytest.mark.asyncio
    async def test_pre_execute_adds_limit_to_federated_sql(self):
        runtime = FederatedRuntime()
        sql = await runtime.pre_execute(
            "SELECT count(*) FROM remote_table",
            {"language": "sql", "max_output_rows": 100},
        )
        assert sql.endswith("LIMIT 100")

    @pytest.mark.asyncio
    async def test_execute_federation_request_uses_connector(self):
        class Response:
            request_id = "req-1"
            status_code = 200
            data = {"rows": 3}
            error = None
            duration_ms = 12
            source_space = "remote-a"

        class FakeConnector:
            def __init__(self):
                self.calls = []

            def send_request(self, **kwargs):
                self.calls.append(kwargs)
                return Response()

        connector = FakeConnector()
        runtime = FederatedRuntime()
        result = await runtime.execute(
            "unused",
            "",
            "federation",
            {
                "federation_request": {
                    "trust": object(),
                    "operation": "read",
                    "resource": "/catalog/products",
                    "payload": {"q": "demo"},
                    "connector": connector,
                }
            },
        )
        assert result["exit_code"] == 0
        assert result["federated"] is True
        assert result["status_code"] == 200
        assert connector.calls[0]["operation"] == "read"
        assert '"rows": 3' in result["output"]


class TestBaseSceneRuntime:

    @pytest.mark.asyncio
    async def test_passthrough_pre_execute(self):
        runtime = SceneRuntime(SandboxMode.STRUCTURED_MODELING)
        code = "SELECT 1"
        result = await runtime.pre_execute(code, {})
        assert result == code

    @pytest.mark.asyncio
    async def test_passthrough_post_execute(self):
        runtime = SceneRuntime(SandboxMode.STRUCTURED_MODELING)
        result_dict = {"output": "ok"}
        result = await runtime.post_execute(result_dict, {})
        assert result == result_dict
