"""A96 会话密钥与 DEK 不得走 argv（bwrap argv 全世界可读：/proc/<pid>/cmdline）。

取证边界（诚实声明）：本机没有安装 bwrap，本文件**不**声称跑过真 bwrap。断言钉的是
可观察后果：`build_bwrap_args()` 构造出的 argv 列表里不得出现密钥值，密钥改由客侧
bootstrap 经 stdin 带外送达 —— bootstrap 脚本本身用 `sh` + 替身子进程真实执行过。

可达性口径：调用链 `BwrapAdapter`（sandbox_runtime.py）只在 legacy 门后注册，默认生产
姿态不可达，dev/e2e 开 L3 时可达。
"""
import subprocess
import sys

import pytest

if sys.platform not in ("linux", "darwin"):
    pytest.skip("sandbox_security imports POSIX-only 'resource'", allow_module_level=True)

from app.services.sandbox_security import (
    EncryptionConfig,
    SandboxSecurityConfig,
    build_bwrap_args,
)

SESSION_KEY = "sk-session-0123456789abcdef0123456789abcdef"
DEK_HEX = "de" * 16
KEY_ID = "key-id-sm4-00000001"
GUEST_CODE = "/workspace/tmp/exec.py"
BOOTSTRAP_ARGV0 = "cds-secret-init"
SECRET_NAMES = ("CDS_SESSION_KEY", "CDS_DEK_HEX", "CDS_KEY_ID")


def _workspace(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "input").mkdir()
    (ws / "output").mkdir()
    (ws / "tmp").mkdir()
    return ws


def _config():
    """seccomp 关掉：本机 libseccomp 可用性与本条缺陷无关，别把环境差异混进读数。"""
    return SandboxSecurityConfig(use_seccomp=False)


def _args_of(result):
    return result[0] if isinstance(result, tuple) else result


def _joined(args):
    """/proc/<pid>/cmdline 看到的就是 argv 按 NUL 拼接的样子。"""
    return "\0".join(args)


def _bootstrap(args):
    """从 argv 里取出客侧 bootstrap：返回 (完整客侧 argv, 脚本, 真命令, 变量个数)。"""
    i = args.index(BOOTSTRAP_ARGV0)
    assert args[i - 3] == "sh" and args[i - 2] == "-c", f"bootstrap 前缀形状不对: {args[i-3:i+2]}"
    return args[i - 3 :], args[i - 1], args[i + 2 :], int(args[i + 1])


def _env_probe():
    """替身「客侧用户代码」：打印自己拿到的环境变量与自己真实的 argv（/proc/self/cmdline）。"""
    return [
        "python3",
        "-c",
        "import os;"
        "print('|'.join(os.environ.get(n, '<missing>') for n in"
        " ['CDS_SESSION_KEY','CDS_DEK_HEX','CDS_KEY_ID']));"
        "print('CMDLINE=' + open('/proc/self/cmdline','rb').read().decode('utf-8','replace'))",
    ]


class TestA96SecretsNeverInArgv:
    def test_build_bwrap_args_never_puts_plaintext_secret_in_argv(self, tmp_path):
        """调用方没有带外通道时的姿态：拒绝执行，而不是把明文写进 argv。

        改前（红）：这里正常返回，且 argv 里直接是 session key / DEK 明文。
        """
        ws = _workspace(tmp_path)
        try:
            args = _args_of(
                build_bwrap_args(
                    workspace=ws,
                    code_file=GUEST_CODE,
                    language="python",
                    config=_config(),
                    session_key=SESSION_KEY,
                    encryption_config=EncryptionConfig(dek_hex=DEK_HEX, key_id=KEY_ID),
                )
            )
        except RuntimeError as exc:
            assert "argv" in str(exc), f"拒绝原因没说清楚: {exc}"
            return
        leaked = [v for v in (SESSION_KEY, DEK_HEX, KEY_ID) if v in _joined(args)]
        assert not leaked, f"A96: 明文密钥出现在 bwrap argv 里（同 UID/任意进程可读 /proc/<pid>/cmdline）: {leaked}"

    def test_dek_arriving_via_env_vars_is_not_argv_either(self, tmp_path):
        """`_workspace_dek_env()` 把 CDS_DEK_HEX 塞进 env_vars —— 那条路同样不得入 argv。"""
        ws = _workspace(tmp_path)
        try:
            args = _args_of(
                build_bwrap_args(
                    workspace=ws,
                    code_file=GUEST_CODE,
                    language="python",
                    config=_config(),
                    env_vars={"CDS_DEK_HEX": DEK_HEX, "CDS_SANDBOX_LEVEL": "L3"},
                )
            )
        except RuntimeError as exc:
            assert "argv" in str(exc), f"拒绝原因没说清楚: {exc}"
            return
        assert DEK_HEX not in _joined(args), "A96: env_vars 里的 CDS_DEK_HEX 仍然被拼进了 argv"
        assert "CDS_DEK_HEX" not in args, "A96: 密钥变量名连同 --setenv 一起出现在 argv 中"


class TestA96OutOfBandDelivery:
    def test_argv_clean_and_values_delivered_via_stdin(self, tmp_path):
        """argv 零密钥 + 带外投递真能到位（本机无 bwrap：直接跑 bootstrap 与替身子进程）。"""
        from app.services.sandbox_security import GuestSecrets

        ws = _workspace(tmp_path)
        secrets = GuestSecrets()
        probe = _env_probe()
        args = _args_of(
            build_bwrap_args(
                workspace=ws,
                code_file=GUEST_CODE,
                language="python",
                config=_config(),
                session_key=SESSION_KEY,
                encryption_config=EncryptionConfig(dek_hex=DEK_HEX, key_id=KEY_ID),
                env_vars={"CDS_SANDBOX_LEVEL": "L3"},
                command=probe,
                secrets=secrets,
            )
        )

        joined = _joined(args)
        for value in (SESSION_KEY, DEK_HEX, KEY_ID):
            assert value not in joined, f"A96: 值仍然出现在 argv 里: {value}"
        for name in SECRET_NAMES:
            assert name not in args, f"A96: argv 里仍然出现密钥变量名 {name}"

        setenv = [args[i + 1] for i, a in enumerate(args) if a == "--setenv"]
        assert "CDS_SANDBOX_LEVEL" in setenv and "PATH" in setenv

        guest_argv, script, guest_cmd, count = _bootstrap(args)
        assert count == 3, f"bootstrap 应当声明 3 个变量，实际 {count}"
        assert guest_cmd == probe, f"真实命令被挪动了: {guest_cmd}"
        assert "exec" in script and "read" in script

        proc = subprocess.Popen(
            guest_argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        secrets.deliver(proc)
        out, err = proc.communicate()
        assert proc.returncode == 0, f"bootstrap 执行失败 rc={proc.returncode}: {err}"
        values, cmdline = out.splitlines()[0], out.splitlines()[1]
        assert values == f"{SESSION_KEY}|{DEK_HEX}|{KEY_ID}", f"带外投递没到位: {values!r} err={err!r}"
        for value in (SESSION_KEY, DEK_HEX, KEY_ID):
            assert value not in cmdline, f"子进程自身 argv 暴露了密钥: {cmdline!r}"
        secrets.close()

    def test_unwired_channel_fails_loudly_instead_of_running_keyless(self, tmp_path):
        """父进程忘记接线（没给 stdin）时：客侧 bootstrap 必须明确报错退出，不能静默无密钥运行。"""
        from app.services.sandbox_security import GuestSecrets

        ws = _workspace(tmp_path)
        secrets = GuestSecrets()
        args = _args_of(
            build_bwrap_args(
                workspace=ws,
                code_file=GUEST_CODE,
                language="python",
                config=_config(),
                session_key=SESSION_KEY,
                command=_env_probe(),
                secrets=secrets,
            )
        )
        guest_argv, _, _, _ = _bootstrap(args)
        proc = subprocess.run(
            guest_argv,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
        )
        assert proc.returncode != 0, "通道没接线却正常跑完了 —— 会变成「无密钥静默执行」"
        assert "A96" in (proc.stderr + proc.stdout), f"报错没自报家门: {proc.stderr!r}"
        secrets.close()

    def test_secret_values_must_be_single_line(self):
        """投递协议按行分帧：带换行的值会让整条通道错位，必须当场拒绝。"""
        from app.services.sandbox_security import GuestSecrets

        secrets = GuestSecrets()
        with pytest.raises(ValueError):
            secrets.set("CDS_SESSION_KEY", "line1\nline2")
        with pytest.raises(ValueError):
            secrets.set("CDS_SESSION_KEY", "line1\n")
        secrets.close()
