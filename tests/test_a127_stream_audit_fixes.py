"""A127 stream_audit 三缺陷修复针（wave 2，2026-10-30）。

模块零生产导入方（唯一 grep 命中是 audit.py 的同名局部函数），三条均
LATENT、接线即高。本文件把修复语义钉死，接线回归时无静默回退空间。
对台账③的如实更正写在模块头：量纲错配的机制在本基线已不存在（avg 与
占比各比各的阈值），成立的是缺样本下限导致"新窗口一条 PII 即告警"——
按此修复并钉。
"""
import threading
from concurrent.futures import ThreadPoolExecutor

from app.services.stream_audit import (
    StreamAuditAgent,
    AuditChunk,
    WindowStats,
    stream_audit_agent,
)

PHONE = "Phone 13800138000"
CLEAN = "nothing personal here"


class TestPiiRedaction:
    """②：明文 PII 不得离开 process_chunk，更不得驻留滚动窗口。"""

    def test_returned_matches_are_tokened(self):
        agent = StreamAuditAgent()
        result = agent.process_chunk(AuditChunk(data=PHONE))
        assert result.pii_count >= 1
        assert result.redacted_data != PHONE
        for m in result.pii_found:
            assert "13800138000" not in m.value
            assert m.value == f"[REDACTED:{m.pii_type.value}]"
            assert m.start >= 0  # 定位信息保留（start/end/置信度），明文不保留

    def test_window_retains_no_plaintext(self):
        """旧行为：matches 原样入窗——审计模块自己成了 PII 驻留点，与同模块
        产出 redacted_data 的意图自相矛盾。"""
        agent = StreamAuditAgent()
        agent.process_chunk(AuditChunk(data=PHONE))
        agent.process_chunk(AuditChunk(data="email me at jane.doe@example.com"))
        for _, retained in agent._window:
            assert "13800138000" not in repr(retained)
            assert "jane.doe@example.com" not in repr(retained)
        stats = agent.get_window_stats()
        assert stats.total_pii >= 2  # 计数/类型统计不受脱敏影响


class TestSampleFloor:
    """③：默认阈值下新窗口单 chunk 不再即告警；到样本量后必须仍会告警。"""

    def test_single_pii_chunk_does_not_alert_by_default(self):
        fired = []
        agent = StreamAuditAgent()  # 默认 pii_density_threshold=0.1
        agent.on_alert(lambda s: fired.append(s))
        agent.process_chunk(AuditChunk(data=PHONE))
        assert fired == []
        stats = agent.get_window_stats()
        assert stats.alert_triggered is False
        assert stats.pii_chunk_ratio == 1.0  # 口径字段如实暴露，只是不足以告警

    def test_alerts_fire_once_sample_floor_met(self):
        fired = []
        agent = StreamAuditAgent()
        agent.on_alert(lambda s: fired.append(s))
        for _ in range(3):  # min_window_chunks 默认 3
            agent.process_chunk(AuditChunk(data=PHONE))
        assert len(fired) >= 1
        assert isinstance(fired[-1], WindowStats)
        assert fired[-1].chunks_with_pii == 3

    def test_clean_stream_never_alerts(self):
        fired = []
        agent = StreamAuditAgent()
        agent.on_alert(lambda s: fired.append(s))
        for _ in range(10):
            agent.process_chunk(AuditChunk(data=CLEAN))
        assert fired == []

    def test_density_dimension_still_independent(self):
        """两个阈值量纲各自一致（钉住更正后的事实，防回退成单变量混比）。"""
        agent = StreamAuditAgent(
            alert_threshold=100.0,        # 密度维度失效
            pii_density_threshold=0.5,    # 占比维度生效
            min_window_chunks=2,
        )
        fired = []
        agent.on_alert(lambda s: fired.append(s))
        agent.process_chunk(AuditChunk(data=PHONE))
        agent.process_chunk(AuditChunk(data=PHONE))
        stats = agent.get_window_stats()
        assert stats.pii_density <= agent._alert_threshold
        assert stats.pii_chunk_ratio > agent._pii_density_threshold
        assert stats.alert_triggered is True and len(fired) >= 1


class TestConcurrency:
    """①：单例暴露的实例态此前全程无锁。"""

    def test_chunk_indices_dense_under_threads(self):
        """旧行为：append+驱逐+计数非原子，chunk_index 可重复/跳号。"""
        agent = StreamAuditAgent(alert_threshold=1e9, pii_density_threshold=1e9)
        n_threads, per_thread = 4, 25
        with ThreadPoolExecutor(max_workers=n_threads) as pool:
            results = list(pool.map(
                lambda _: [agent.process_chunk(AuditChunk(data=f"chunk {_}-{i}")
                                               ).chunk_index for i in range(per_thread)],
                range(n_threads),
            ))
        flat = sorted(i for part in results for i in part)
        assert flat == list(range(n_threads * per_thread))
        assert agent.window_size == n_threads * per_thread
        assert agent._chunk_counter == n_threads * per_thread

    def test_callback_may_register_during_fire(self):
        """旧行为：_fire_alerts 直接遍历 _callbacks，注册方并发即
        RuntimeError/丢回调。现在快照触发，回调内可安全再注册/查统计。"""
        agent = StreamAuditAgent(min_window_chunks=1)
        seen = []

        def registering_cb(stats):
            seen.append(stats)
            agent.on_alert(lambda s: seen.append("nested"))
            agent.get_window_stats()  # 同锁重入路径不得死锁

        agent.on_alert(registering_cb)
        agent.process_chunk(AuditChunk(data=PHONE))
        assert len(seen) == 1  # 快照语义：本次触发只跑注册时的回调
        agent.process_chunk(AuditChunk(data=PHONE))
        assert "nested" in seen  # 新回调从下一次触发起生效

    def test_lock_exists_and_singleton_kept(self):
        """单例是仓内约定，保留；但线程安全必须是类自带的。"""
        assert isinstance(getattr(stream_audit_agent, "_lock", None), type(threading.Lock()))
        assert isinstance(StreamAuditAgent()._lock, type(threading.Lock()))
