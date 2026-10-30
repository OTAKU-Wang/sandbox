"""A9: health 的 l2c_planned 必须是「L2' 仍有规划中能力」的真值，不是恒 False 死逻辑。

台账：`all(… == STATE_PLANNED …)` 在混合行的 L2C 矩阵上永远 False（sync_exec=REAL 等），
应为 `any(…)`；注释明确 RT-06 / CB-04 仍 planned ⇒ 今天应为 True。
"""
from app.services import capability_matrix as cm


def test_l2c_planned_true_while_any_cell_planned():
    row = cm._MATRIX[cm.LEVEL_L2C]
    planned = [c for c in cm.CAPABILITIES if row[c]["state"] == cm.STATE_PLANNED]
    # 现矩阵按注释仍有 RT-06 / CB-04 planned
    assert planned
    health = cm.health_capability_section()
    assert health["l2c_planned"] is True


def test_flag_reaches_false_only_when_nothing_planned(monkeypatch):
    # 反事实：全部单元格转 real 后 flag 必须落 False —— 证明表达式可达而非死值
    for cap in cm.CAPABILITIES:
        monkeypatch.setitem(cm._MATRIX[cm.LEVEL_L2C][cap], "state", cm.STATE_REAL)
    assert cm.health_capability_section()["l2c_planned"] is False
