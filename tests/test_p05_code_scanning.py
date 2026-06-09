"""P0-5: CODE_SCANNING integration tests.

Tests verify that:
1. CodeScanner correctly identifies dangerous code
2. Task state machine includes CODE_SCANNING state
3. Task worker should call code_scanner before execution (GAP — currently NOT_IMPL)
4. Scan results should be audit-logged

P0-5 gap: task_worker._execute_task transitions PENDING → RUNNING directly,
skipping CODE_SCANNING. Code scanner exists but is not wired into task execution.
"""
import pytest
from app.services.code_scanner import (
    CodeScanner, SandboxMode, ScanResult, ScanSeverity,
)


class TestCodeScannerPython:
    """Test CodeScanner Python AST analysis."""

    def setup_method(self):
        self.scanner = CodeScanner()

    def test_clean_code_passes(self):
        code = "import numpy as np\nx = np.array([1,2,3])\nprint(x.mean())"
        result = self.scanner.scan(code, "python", "structured_query")
        assert result.passed is True

    def test_os_system_rejected(self):
        code = 'import os\nos.system("rm -rf /")'
        result = self.scanner.scan(code, "python", "structured_query")
        assert result.passed is False
        assert any(i.code == "NOT_WHITELISTED" for i in result.issues)

    def test_subprocess_rejected(self):
        code = 'import subprocess\nsubprocess.call(["ls"])'
        result = self.scanner.scan(code, "python", "structured_query")
        assert result.passed is False

    def test_exec_rejected(self):
        code = 'exec("import os; os.system(\'id\')")'
        result = self.scanner.scan(code, "python", "structured_query")
        assert result.passed is False
        assert any(i.code == "DANGEROUS_BUILTIN" for i in result.issues)

    def test_eval_rejected(self):
        code = 'x = eval("__import__(\'os\').system(\'id\')")'
        result = self.scanner.scan(code, "python", "structured_query")
        assert result.passed is False

    def test_compile_rejected(self):
        code = 'c = compile("x=1", "<test>", "exec")'
        result = self.scanner.scan(code, "python", "structured_query")
        assert result.passed is False

    def test_importlib_rejected(self):
        code = 'import importlib\nm = importlib.import_module("os")'
        result = self.scanner.scan(code, "python", "structured_query")
        assert result.passed is False

    def test_open_builtin_rejected(self):
        code = 'f = open("/etc/passwd", "r")'
        result = self.scanner.scan(code, "python", "structured_query")
        assert result.passed is False

    def test_globals_locals_rejected(self):
        code = 'g = globals()'
        result = self.scanner.scan(code, "python", "structured_query")
        assert result.passed is False

    def test_breakpoint_rejected(self):
        code = 'breakpoint()'
        result = self.scanner.scan(code, "python", "structured_query")
        assert result.passed is False

    def test_syntax_error_detected(self):
        code = 'def foo(\n  pass'
        result = self.scanner.scan(code, "python", "structured_query")
        assert result.passed is False

    def test_unknown_mode_rejected(self):
        code = 'x = 1'
        result = self.scanner.scan(code, "python", "invalid_mode")
        assert result.passed is False

    def test_unknown_language_rejected(self):
        code = 'console.log("hi")'
        result = self.scanner.scan(code, "javascript", "structured_query")
        assert result.passed is False

    def test_allowed_data_science_imports(self):
        code = "import pandas as pd\nimport torch\nfrom sklearn import metrics"
        result = self.scanner.scan(code, "python", "llm_sft")
        assert result.passed is True

    def test_save_path_restriction_gpu_mode(self):
        code = 'model.save("/tmp/evil_path")'
        result = self.scanner.scan(code, "python", "llm_sft")
        assert result.passed is False
        assert any(i.code == "INVALID_SAVE_PATH" for i in result.issues)

    def test_save_to_sandbox_output_ok(self):
        code = 'model.save("/sandbox/output/model.bin")'
        result = self.scanner.scan(code, "python", "llm_sft")
        assert result.passed is True


class TestCodeScannerSQL:
    """Test CodeScanner SQL validation."""

    def setup_method(self):
        self.scanner = CodeScanner()

    def test_select_allowed(self):
        sql = "SELECT id, name FROM users WHERE age > 18"
        result = self.scanner.scan(sql, "sql", "structured_query")
        assert result.passed is True

    def test_drop_rejected(self):
        sql = "DROP TABLE users"
        result = self.scanner.scan(sql, "sql", "structured_query")
        assert result.passed is False

    def test_delete_rejected(self):
        sql = "DELETE FROM users WHERE id = 1"
        result = self.scanner.scan(sql, "sql", "structured_query")
        assert result.passed is False

    def test_insert_rejected(self):
        sql = "INSERT INTO users VALUES (1, 'admin')"
        result = self.scanner.scan(sql, "sql", "structured_query")
        assert result.passed is False

    def test_update_rejected(self):
        sql = "UPDATE users SET role = 'admin' WHERE id = 1"
        result = self.scanner.scan(sql, "sql", "structured_query")
        assert result.passed is False


class TestTaskStateMachineCodeScanning:
    """Test that task state machine supports CODE_SCANNING state."""

    def test_code_scanning_state_exists(self):
        from app.services.task_state_machine import TaskStatus
        assert hasattr(TaskStatus, 'CODE_SCANNING')

    def test_queued_to_code_scanning_valid(self):
        from app.services.task_state_machine import task_state_machine, TaskStatus
        result = task_state_machine.validate_transition(
            TaskStatus.QUEUED, TaskStatus.CODE_SCANNING
        )
        assert result.success is True

    def test_code_scanning_to_preparing_valid(self):
        from app.services.task_state_machine import task_state_machine, TaskStatus
        result = task_state_machine.validate_transition(
            TaskStatus.CODE_SCANNING, TaskStatus.PREPARING
        )
        assert result.success is True

    def test_code_scanning_to_failed_valid(self):
        """Scan failure should transition to FAILED."""
        from app.services.task_state_machine import task_state_machine, TaskStatus
        result = task_state_machine.validate_transition(
            TaskStatus.CODE_SCANNING, TaskStatus.FAILED
        )
        assert result.success is True


class TestTaskWorkerCodeScanningIntegration:
    """Test that task worker integrates code scanning.

    GAP: task_worker._execute_task currently skips CODE_SCANNING.
    These tests document the expected behavior after P0-5 fix.
    """

    def test_code_scanner_importable_in_worker(self):
        """CodeScanner should be importable from task_worker context."""
        from app.services.code_scanner import CodeScanner
        scanner = CodeScanner()
        assert scanner is not None

    def test_worker_execute_task_skips_scanning(self):
        """GAP: _execute_task goes PENDING → RUNNING without scanning.

        After P0-5 fix, it should:
        1. Transition PENDING → CODE_SCANNING
        2. Call code_scanner.scan(task.code, task.language, task.sandbox_mode)
        3. If FAIL → transition to FAILED, set error_message
        4. If PASS → transition CODE_SCANNING → PREPARING → RUNNING
        """
        import inspect
        from app.services.task_worker import TaskWorker
        source = inspect.getsource(TaskWorker._execute_task)
        has_scanning = 'code_scanner' in source or 'CodeScanner' in source or 'scan' in source
        if not has_scanning:
            pytest.xfail("P0-5: task_worker._execute_task does not call code_scanner")

    def test_scan_result_should_be_audit_logged(self):
        """Scan results should be written to audit log via _code_scanning_handler.

        P0-5 fix: handler calls audit_service.log() with scan pass/fail + issues.
        """
        import inspect
        from app.services.task_pipeline import _code_scanning_handler
        source = inspect.getsource(_code_scanning_handler)
        assert 'audit_service' in source, "Handler should write scan results to audit_service"
        assert 'audit_service.log' in source, "Handler should call audit_service.log()"


class TestPipelineCodeScanningHandler:
    """Test that task_pipeline CODE_SCANNING handler works correctly.

    P0-5 fix: Andrew registered _code_scanning_handler at task_pipeline line 394.
    This handler calls CodeScanner.scan() and rejects tasks with dangerous code.
    """

    def test_pipeline_has_code_scanning_handler(self):
        """task_pipeline should have a registered CODE_SCANNING handler."""
        from app.services.task_pipeline import task_pipeline
        from app.services.task_state_machine import TaskStatus
        registered = task_pipeline._handlers.get(TaskStatus.CODE_SCANNING)
        assert registered is not None, "CODE_SCANNING handler not registered in task_pipeline"

    def test_pipeline_has_preparing_handler(self):
        """task_pipeline should validate PREPARING instead of silently skipping it."""
        from app.services.task_pipeline import task_pipeline
        from app.services.task_state_machine import TaskStatus
        registered = task_pipeline._handlers.get(TaskStatus.PREPARING)
        assert registered is not None, "PREPARING handler not registered in task_pipeline"

    def test_handler_rejects_dangerous_code(self):
        """CODE_SCANNING handler should reject tasks with dangerous code."""
        import asyncio
        from app.services.task_pipeline import task_pipeline, PipelineTask, TaskStatus

        handler = task_pipeline._handlers[TaskStatus.CODE_SCANNING]
        task = PipelineTask(
            task_id="test-scan-1",
            session_id="sess-1",
            task_type="code_execution",
            payload={"code": 'import os\nos.system("rm -rf /")', "language": "python", "sandbox_mode": "structured_query"},
        )
        result = asyncio.get_event_loop().run_until_complete(handler(task))
        assert result is not None
        status, data = result
        assert status == TaskStatus.REJECTED
        assert "issues" in data

    def test_handler_passes_clean_code(self):
        """CODE_SCANNING handler should pass clean code."""
        import asyncio
        from app.services.task_pipeline import task_pipeline, PipelineTask, TaskStatus

        handler = task_pipeline._handlers[TaskStatus.CODE_SCANNING]
        task = PipelineTask(
            task_id="test-scan-2",
            session_id="sess-2",
            task_type="code_execution",
            payload={"code": "import numpy as np\nx = np.array([1,2,3])", "language": "python", "sandbox_mode": "structured_query"},
        )
        result = asyncio.get_event_loop().run_until_complete(handler(task))
        assert result is not None
        status, data = result
        assert status == TaskStatus.CODE_SCANNING
        assert data["scan"] == "passed"

    def test_handler_skips_empty_code(self):
        """CODE_SCANNING handler should skip when no code provided."""
        import asyncio
        from app.services.task_pipeline import task_pipeline, PipelineTask, TaskStatus

        handler = task_pipeline._handlers[TaskStatus.CODE_SCANNING]
        task = PipelineTask(
            task_id="test-scan-3",
            session_id="sess-3",
            task_type="code_execution",
            payload={},
        )
        result = asyncio.get_event_loop().run_until_complete(handler(task))
        assert result is not None
        status, data = result
        assert status == TaskStatus.CODE_SCANNING
        assert data["scan"] == "skipped"

    def test_preparing_handler_validates_payload(self):
        """PREPARING handler should produce explicit prepared metadata."""
        import asyncio
        from app.services.task_pipeline import task_pipeline, PipelineTask, TaskStatus

        handler = task_pipeline._handlers[TaskStatus.PREPARING]
        task = PipelineTask(
            task_id="test-prepare-1",
            session_id="sess-1",
            task_type="code_execution",
            payload={"task_id": "orm-task-1", "code": "print(1)", "language": "Python", "timeout_seconds": "30"},
        )
        result = asyncio.get_event_loop().run_until_complete(handler(task))

        assert result is not None
        status, data = result
        assert status == TaskStatus.PREPARING
        assert data["prepared"] is True
        assert data["language"] == "python"
        assert data["timeout_seconds"] == 30

    @pytest.mark.asyncio
    async def test_missing_stage_handler_fails_closed(self):
        """A missing stage handler is an implementation error, not a successful no-op."""
        from app.services.task_pipeline import TaskPipeline, PipelineTask
        from app.services.task_state_machine import TaskStatus

        pipeline = TaskPipeline()
        task = PipelineTask(
            task_id="test-no-handler",
            session_id="sess-1",
            task_type="code_execution",
            payload={"task_id": "orm-task-1"},
        )

        result = await pipeline._run_stage(task, TaskStatus.CODE_SCANNING)

        assert result is None
        assert task.status == TaskStatus.FAILED
        assert "No handler registered" in task.error

    @pytest.mark.asyncio
    async def test_output_inspection_rejects_sensitive_output_and_removes_raw(self):
        """OUTPUT_INSPECTING should reject sensitive stdout and avoid final raw output storage."""
        from app.services.task_pipeline import PipelineTask, _output_inspecting_handler
        from app.services.task_state_machine import TaskStatus

        task = PipelineTask(
            task_id="test-output-sensitive",
            session_id="session-1",
            task_type="code_execution",
            payload={"sandbox_mode": "structured_query"},
            result={
                "output": "phone=13812345678",
                "user_id": "user-1",
                "sandbox_session_id": "session-1",
            },
        )

        status, data = await _output_inspecting_handler(task)

        assert status == TaskStatus.REJECTED
        assert data["inspection"] == "failed"
        assert "output" not in task.result
        assert "13812345678" not in data["redacted_output"]
        assert "[REDACTED:phone]" in data["redacted_output"]

    @pytest.mark.asyncio
    async def test_output_inspection_passes_clean_output_and_removes_raw(self):
        from app.services.task_pipeline import PipelineTask, _output_inspecting_handler
        from app.services.task_state_machine import TaskStatus

        task = PipelineTask(
            task_id="test-output-clean",
            session_id="session-1",
            task_type="code_execution",
            payload={"sandbox_mode": "structured_query"},
            result={
                "output": "aggregate mean is 42",
                "user_id": "user-1",
                "sandbox_session_id": "session-1",
            },
        )

        status, data = await _output_inspecting_handler(task)

        assert status == TaskStatus.OUTPUT_INSPECTING
        assert data["inspection"] == "passed"
        assert "output" not in task.result
        assert data["inspection_report"]["signature"]
