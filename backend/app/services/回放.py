import json
import uuid
from typing import Any

from app.db import 连接
from app.services.时间 import 此刻, 前一营业日, 标准化营业日


def _前日(营业日: str) -> str:
    return 前一营业日(营业日)


def _读总批范围(日: str, 总批编号: str, 诊断: bool) -> tuple[set[str] | None, dict | None]:
    """按分片总批确定回放范围；未完成总批拒绝非诊断全量回放。"""
    from app.modules.账户分片抽取 import 读总批

    总批 = 读总批(总批编号)
    if 总批["营业日"] != 日:
        raise ValueError(
            f"回放营业日 {日} 与分片总批 {总批编号} 营业日 {总批['营业日']} 不一致，范围不符，已拒绝"
        )
    范围 = set(总批["声明账户集合"])
    if not 总批["可当全量回放"]:
        if not 诊断:
            raise ValueError(
                f"分片总批 {总批编号} 状态={总批['状态']}，不可当全量回放；"
                "禁止全量通过（如仅诊断，请带 诊断=真）"
            )
    elif 诊断:
        raise ValueError("已完成总批无需诊断回放，请直接全量回放")
    return 范围, 总批


def 回放核对(
    营业日: str,
    批次编号: str = "",
    分片总批编号: str = "",
    诊断: bool = False,
) -> dict[str, Any]:
    """用前日快照+当日流水重算，对照当日周期快照，写入差额/跳变。

    带 `分片总批编号` 时，回放范围被强制限定为该总批 `声明账户集合`；
    范围不符或未完成总批做全量回放均直接拒绝。
    """
    日 = 标准化营业日(营业日)
    批 = 批次编号.strip() or f"回放-{日}-{uuid.uuid4().hex[:8]}"
    总批编号 = (分片总批编号 or "").strip()
    范围: set[str] | None = None
    总批元: dict | None = None
    诊断模式 = False
    if 总批编号:
        范围, 总批元 = _读总批范围(日, 总批编号, 诊断)
        诊断模式 = bool(诊断)

    conn = 连接()
    try:
        if 总批元 is not None and 总批元["可当全量回放"]:
            覆盖 = {
                r["账户键"]
                for r in conn.execute(
                    "SELECT DISTINCT 账户键 FROM 周期快照 "
                    "WHERE 总批编号=? AND 营业日=? AND 状态='有效'",
                    (总批编号, 日),
                )
            }
            if 覆盖 != 范围:
                raise ValueError(
                    "快照覆盖与总批声明账户集合不一致，回放范围不符，已拒绝（缺 "
                    f"{sorted(范围 - 覆盖)}，多 {sorted(覆盖 - 范围)}）"
                )

        参 = {r["键"]: r["值"] for r in conn.execute("SELECT 键, 值 FROM 台参")}
        try:
            阈值 = int(参.get("跳变阈值分", "0") or "0")
        except ValueError:
            阈值 = 0
        账户们 = [dict(r) for r in conn.execute("SELECT * FROM 账户 WHERE 状态='正常'")]
        if 范围 is not None:
            账户们 = [户 for 户 in 账户们 if 户["账户键"] in 范围]
        相符 = 0
        差额数 = 0
        跳变数 = 0
        无快照 = 0
        for 户 in 账户们:
            账户键 = 户["账户键"]
            if 日 < 户["开户营业日"]:
                continue
            快照 = conn.execute(
                "SELECT 余额 FROM 周期快照 WHERE 账户键=? AND 营业日=? AND 状态='有效'",
                (账户键, 日),
            ).fetchone()
            if not 快照:
                无快照 += 1
                conn.execute(
                    "INSERT INTO 差额行(回放批次编号, 账户键, 营业日, 快照余额, 回放余额, 差额, 原因, 时刻) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (批, 账户键, 日, None, None, 0, "缺当日快照", 此刻()),
                )
                差额数 += 1
                continue
            前 = _前日(日)
            前日快照 = conn.execute(
                "SELECT 余额 FROM 周期快照 WHERE 账户键=? AND 营业日=? AND 状态='有效'",
                (账户键, 前),
            ).fetchone()
            开端 = 0 if 前日快照 is None else int(前日快照["余额"])
            净额 = int(
                conn.execute(
                    "SELECT COALESCE(SUM(变动额),0) AS s FROM 变动流水 "
                    "WHERE 账户键=? AND 营业日=? AND 命运='已入账'",
                    (账户键, 日),
                ).fetchone()["s"]
            )
            回放余额 = 开端 + 净额
            快照余额 = int(快照["余额"])
            差额 = 快照余额 - 回放余额
            if 差额 == 0:
                相符 += 1
            else:
                conn.execute(
                    "INSERT INTO 差额行(回放批次编号, 账户键, 营业日, 快照余额, 回放余额, 差额, 原因, 时刻) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (批, 账户键, 日, 快照余额, 回放余额, 差额, "快照与回放不一致", 此刻()),
                )
                差额数 += 1
            偏离 = abs(快照余额 - (开端 + 净额))
            if 阈值 > 0 and 偏离 >= 阈值:
                conn.execute(
                    "INSERT INTO 跳变记录(账户键, 营业日, 前日余额, 当日余额, 流水净额, 偏离, 检出批次编号, 时刻) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (账户键, 日, 开端, 快照余额, 净额, 偏离, 批, 此刻()),
                )
                跳变数 += 1
        # 诊断回放永不通过，禁止被当成全量通过。
        通过 = 差额数 == 0 and 无快照 == 0 and not 诊断模式
        报告 = {
            "营业日": 日,
            "相符": 相符,
            "差额": 差额数,
            "跳变": 跳变数,
            "无快照": 无快照,
            "通过": 通过,
            "分片总批编号": 总批编号,
            "诊断": 诊断模式,
            "回放账户数": len(账户们),
            "回放范围": sorted(范围) if 范围 is not None else "全量",
        }
        if 总批元 is not None:
            报告["口径"] = 总批元["口径"]
            报告["总批状态"] = 总批元["状态"]
            报告["总批可当全量回放"] = bool(总批元["可当全量回放"])
            报告["范围与总批声明一致"] = True
        conn.execute(
            "INSERT INTO 回放批次(编号, 营业日, 时刻, 报告) VALUES (?,?,?,?)",
            (批, 日, 此刻(), json.dumps(报告, ensure_ascii=False)),
        )
        conn.commit()
        return {"批次编号": 批, **报告}
    finally:
        conn.close()
