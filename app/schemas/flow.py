import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.models.notebook import TaskDebugMethod, TaskExportMode

DeliveryMethod = Literal["dataset", "open_api", "result_push"]
ComputeFrequency = Literal["manual", "hourly", "daily"]
WorkloadEnv = Literal["test", "production"]

_RESOURCE_OVERRIDE_KEYS = frozenset({"cpu_cores", "memory_mb", "disk_gb", "timeout_seconds"})


class ResourceSpec(BaseModel):
    cpu_cores: float | None = None
    memory_mb: int | None = None
    disk_gb: int | None = None
    gpu_count: int | None = None
    image_template: str | None = None
    timeout_seconds: int | None = None


class FlowConfigRequest(BaseModel):
    delivery_methods: list[DeliveryMethod] = Field(default_factory=lambda: ["dataset"])
    auto_compute: bool = False
    compute_frequency: ComputeFrequency = "manual"
    compute_minute: int = Field(0, ge=0, le=59)
    compute_hour: int = Field(2, ge=0, le=23)
    compute_tz: str = "Asia/Shanghai"
    sandbox_resource_spec: ResourceSpec | None = None


class NotebookCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    description: str | None = Field(None, max_length=4000)
    path: str = Field(..., min_length=1, max_length=512)
    contract_id: uuid.UUID | None = None
    session_id: uuid.UUID | None = None
    content: dict[str, Any] | str | None = None
    language: str = "python"
    # 开发任务字段（SS-18 §8 P0-①）：任务编号由服务端按当日序号生成，不接受客户端指定。
    debug_method: TaskDebugMethod | None = None
    export_mode: TaskExportMode | None = None
    debug_memory_mb: int | None = Field(None, ge=1)
    exec_memory_mb: int | None = Field(None, ge=1)
    # 图 2-2「已选择数据」：None = 注入合约授权的全部供数；[] = 不注入任何供数；
    # 非空则必须是合约已授权数据资源的子集（越权 422，不做静默裁剪）。
    data_resource_ids: list[uuid.UUID] | None = Field(None, max_length=100)


class NotebookUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=255)
    content: dict[str, Any] | str | None = None
    session_id: uuid.UUID | None = None


class CellExecuteRequest(BaseModel):
    code: str = Field(..., max_length=200_000)
    cell_index: int | None = None
    persist: bool = True


class WorkloadCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    contract_id: uuid.UUID | None = None
    notebook_id: uuid.UUID | None = None
    entrypoint: str | None = None
    language: str = "python"
    resource_spec: ResourceSpec | None = None
    data_product_ids: list[uuid.UUID] | None = None


class WorkloadTestRequest(BaseModel):
    keep_session: bool = False
    timeout_seconds: int = Field(900, ge=60, le=3600)


class ComputeRunCreate(BaseModel):
    contract_id: uuid.UUID
    # 不指定则使用该合约当前 is_latest 的已发布生产版本；指定则必须是本合约的 published 生产版本。
    workload_id: uuid.UUID | None = None
    # 运行参数：写入 compute_runs.params 并以 CDS_PARAMS 注入沙箱内入口代码。
    params: dict | None = None
    # 规格覆盖：只允许向下收窄合约已批准的规格，超出一律 400。
    resource_override: dict | None = None

    @field_validator("params")
    @classmethod
    def params_within_limit(cls, v: dict | None) -> dict | None:
        if v is None:
            return v
        if len(v) > 32:
            raise ValueError("运行参数最多 32 个键")
        import json

        if len(json.dumps(v, ensure_ascii=False)) > 8192:
            raise ValueError("运行参数序列化后不得超过 8KB")
        return v

    @field_validator("resource_override")
    @classmethod
    def override_keys(cls, v: dict | None) -> dict | None:
        if v is None:
            return v
        unknown = set(v) - _RESOURCE_OVERRIDE_KEYS
        if unknown:
            raise ValueError(f"不支持的规格覆盖项：{', '.join(sorted(unknown))}")
        for key, value in v.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
                raise ValueError(f"{key} 必须为正数")
            if key != "cpu_cores" and not float(value).is_integer():
                raise ValueError(f"{key} 必须为整数")
        return v


class DatasetBundleRequest(BaseModel):
    session_id: uuid.UUID
    contract_id: uuid.UUID
    watermark: bool = True


class WebhookCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    url: str = Field(..., min_length=8, max_length=1024)
    events: list[str] = Field(..., min_length=1)
    contract_id: uuid.UUID | None = None


class WebhookUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=128)
    url: str | None = Field(None, min_length=8, max_length=1024)
    events: list[str] | None = None
    status: Literal["active", "paused"] | None = None


class OpenApiEndpointCreate(BaseModel):
    contract_id: uuid.UUID
    workload_id: uuid.UUID | None = None
    name: str = Field(..., min_length=1, max_length=128)
    slug: str | None = Field(None, min_length=1, max_length=64)
    request_schema: dict[str, Any] | None = None
    rate_limit_per_minute: int = Field(60, ge=1, le=10000)
    quota_total: int | None = Field(None, ge=1)


class OpenApiEndpointUpdate(BaseModel):
    status: Literal["enabled", "disabled"] | None = None
    rate_limit_per_minute: int | None = Field(None, ge=1, le=10000)
    quota_total: int | None = Field(None, ge=1)


class NotebookOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    contract_id: uuid.UUID | None
    session_id: uuid.UUID | None
    title: str
    description: str | None = None
    path: str
    kernel: str
    language: str
    content: dict[str, Any] | None
    version: int
    status: str
    execution_backend: str | None
    task_no: str | None = None
    debug_method: str | None = None
    export_mode: str | None = None
    debug_memory_mb: int | None = None
    exec_memory_mb: int | None = None
    data_resource_ids: list[uuid.UUID] | None = None
    last_executed_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class WorkloadOut(BaseModel):
    id: uuid.UUID
    name: str
    user_id: uuid.UUID
    contract_id: uuid.UUID | None
    source_notebook_id: uuid.UUID | None
    language: str
    entrypoint_hash: str | None
    resource_spec: dict | None
    data_product_ids: list | None
    env: str
    status: str
    version: int
    is_latest: bool
    test_run_id: uuid.UUID | None
    published_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class TestRunOut(BaseModel):
    id: uuid.UUID
    workload_id: uuid.UUID
    session_id: uuid.UUID | None
    status: str
    passed: bool
    checks: list | None
    exit_code: int | None
    error_message: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ComputeRunOut(BaseModel):
    id: uuid.UUID
    contract_id: uuid.UUID
    workload_id: uuid.UUID | None
    trigger: str
    scheduled_for: datetime | None
    status: str
    attempt: int
    session_id: uuid.UUID | None
    output_files: list | None
    output_rows: int
    resource_usage: dict | None
    error_message: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}
