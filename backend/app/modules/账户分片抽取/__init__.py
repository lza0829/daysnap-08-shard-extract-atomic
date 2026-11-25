"""账户分片抽取 · 分片任务 + 分片总批。

口径（全局唯一，选定「乙」）：
    部分成功 + 未完成总批。
    - 任一片失败时，其它已成功片写下的周期快照**保留**，不回滚；
    - 总批状态不得为 `成功`，置为 `未完成`，并列出失败片账户集合；
    - `可当全量回放` 仅当全部片成功且快照覆盖与声明一致时为真；
    - 对未完成总批跑回放：不带 `诊断` 一律拒绝；带 `诊断` 只允许诊断回放，
      且 `通过` 强制为假，禁止当全量通过；
    - 失败片重跑补齐、覆盖与声明一致后，总批才可封成 `成功`。

硬约束：
    * 各片状态与总批声明一致（`核证` 逐项校验）；
    * 回放范围必须与总批声明账户集合一致，否则拒绝；
    * 片提交与总批汇总交错（版本不一致）必须失败闭，绝不汇总出缺片的总批。
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Iterable

from app.db import 连接
from app.services.时间 import 前一营业日, 此刻, 标准化营业日

口径 = "乙-部分成功+未完成总批"
启用 = True

片状态_待运行 = "待运行"
片状态_成功 = "成功"
片状态_失败 = "失败"

批状态_运行中 = "运行中"
批状态_未完成 = "未完成"
批状态_成功 = "成功"


def 说明():
    return "账户分片抽取"


# ---------------------------------------------------------------- 小工具


def _读总批(conn, 编号: str):
    行 = conn.execute("SELECT * FROM 分片总批 WHERE 编号=?", (编号,)).fetchone()
    if not 行:
        raise ValueError(f"分片总批不存在: {编号}")
    return 行


def _读片(conn, 总批编号: str) -> list:
    return list(
        conn.execute(
            "SELECT * FROM 分片任务 WHERE 总批编号=? ORDER BY 批内序",
            (总批编号,),
        )
    )


def _列表(文本: str | None) -> list:
    try:
        v = json.loads(文本 or "[]")
        return list(v) if isinstance(v, list) else []
    except Exception:
        return []


def _去重(seq: Iterable[str]) -> list[str]:
    出: list[str] = []
    for x in seq:
        x = str(x)
        if x and x not in 出:
            出.append(x)
    return 出


def _片字典(行) -> dict:
    return {
        "片序": int(行["批内序"]),
        "营业日": 行["营业日"],
        "账户集合": _列表(行["账户集合"]),
        "账户数": int(行["账户数"]),
        "状态": 行["状态"],
        "失败原因": 行["失败原因"],
        "抽取批次编号": 行["抽取批次编号"],
        "快照数": int(行["快照数"]),
        "重试次数": int(行["重试次数"]),
        "开始时刻": 行["开始时刻"],
        "结束时刻": 行["结束时刻"],
    }


def _总批字典(conn, 行) -> dict:
    片们 = [_片字典(r) for r in _读片(conn, 行["编号"])]
    return {
        "编号": 行["编号"],
        "营业日": 行["营业日"],
        "口径": 行["口径"],
        "状态": 行["状态"],
        "分片数": int(行["分片数"]),
        "已成功片数": int(行["已成功片数"]),
        "失败片数": int(行["失败片数"]),
        "声明账户集合": _列表(行["声明账户集合"]),
        "声明账户数": int(行["声明账户数"]),
        "失败账户集合": _列表(行["失败账户集合"]),
        "可当全量回放": bool(行["可当全量回放"]),
        "版本": int(行["版本"]),
        "报告": _解析报告(行["报告"]),
        "时刻": 行["时刻"],
        "汇总时刻": 行["汇总时刻"],
        "各片": 片们,
    }


def _解析报告(文本: str) -> dict:
    try:
        v = json.loads(文本 or "{}")
        return v if isinstance(v, dict) else {}
    except Exception:
        return {}


def _起写事务(conn) -> None:
    conn.isolation_level = None
    conn.execute("BEGIN IMMEDIATE")


# ---------------------------------------------------------------- 建批


def 开总批(
    营业日: str,
    每片账户数: int = 1,
    账户键们: list[str] | None = None,
    自动运行: bool = True,
    注入失败片序: str | int = "",
) -> dict[str, Any]:
    """建分片总批，按账户序切片。默认把全部片跑一遍（失败片不会中止后续片）。"""
    日 = 标准化营业日(营业日)
    try:
        片宽 = int(每片账户数)
    except (TypeError, ValueError):
        raise ValueError("每片账户数须为正整数")
    if 片宽 < 1:
        raise ValueError("每片账户数须为正整数")

    连接_ = 连接()
    try:
        if 账户键们:
            账户们 = [str(k).strip() for k in 账户键们 if str(k).strip()]
        else:
            账户们 = [
                r["账户键"]
                for r in 连接_.execute(
                    "SELECT 账户键 FROM 账户 WHERE 状态='正常' "
                    "AND 开户营业日<=? ORDER BY 账户键",
                    (日,),
                )
            ]
        账户们 = _去重(账户们)
    finally:
        连接_.close()
    if not 账户们:
        raise ValueError("无参与账户，无法开分片总批")

    分片集 = [账户们[i : i + 片宽] for i in range(0, len(账户们), 片宽)]
    总数 = len(分片集)
    失败片序 = 0
    if str(注入失败片序).strip():
        try:
            失败片序 = int(注入失败片序)
        except (TypeError, ValueError):
            raise ValueError("注入失败片序须为整数")
        if not 1 <= 失败片序 <= 总数:
            raise ValueError(f"注入失败片序须在 1..{总数}")

    总批编号 = f"分片-{日}-{uuid.uuid4().hex[:8]}"
    conn = 连接()
    try:
        _起写事务(conn)
        声明 = _去重(账户们)
        conn.execute(
            "INSERT INTO 分片总批(编号, 营业日, 口径, 状态, 分片数, 声明账户集合, "
            "声明账户数, 已成功片数, 失败片数, 可当全量回放, 失败账户集合, 报告, 版本, 时刻) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                总批编号,
                日,
                口径,
                批状态_运行中,
                总数,
                json.dumps(声明, ensure_ascii=False),
                len(声明),
                0,
                0,
                0,
                "[]",
                "{}",
                0,
                此刻(),
            ),
        )
        for 序, 集 in enumerate(分片集, start=1):
            conn.execute(
                "INSERT INTO 分片任务(总批编号, 批内序, 营业日, 账户集合, 账户数, 状态) "
                "VALUES (?,?,?,?,?,?)",
                (
                    总批编号,
                    序,
                    日,
                    json.dumps(集, ensure_ascii=False),
                    len(集),
                    片状态_待运行,
                ),
            )
        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        conn.close()

    if 自动运行:
        for 序 in range(1, 总数 + 1):
            注入 = f"夹具注入：第{序}片抽取失败" if 序 == 失败片序 else ""
            运行片(总批编号, 序, 注入失败=注入)

    return 读总批(总批编号)


# ---------------------------------------------------------------- 跑片


def _算余额(conn, 户, 日: str, 缺前日: str) -> dict:
    """单户余额：前日快照(或缺前日策略) + 当日已入账流水净额。"""
    账户键 = 户["账户键"]
    前 = 前一营业日(日)
    前日快照 = conn.execute(
        "SELECT 余额 FROM 周期快照 WHERE 账户键=? AND 营业日=? AND 状态='有效'",
        (账户键, 前),
    ).fetchone()
    断档 = False
    if 前日快照 is None:
        if 日 == 户["开户营业日"] or 缺前日 == "以零为开端":
            开端 = 0
        else:
            开端 = 0
            断档 = True
    else:
        开端 = int(前日快照["余额"])
    净额 = int(
        conn.execute(
            "SELECT COALESCE(SUM(变动额),0) AS s FROM 变动流水 "
            "WHERE 账户键=? AND 营业日=? AND 命运='已入账'",
            (账户键, 日),
        ).fetchone()["s"]
    )
    return {"账户键": 账户键, "开端": 开端, "净额": 净额, "余额": 开端 + 净额, "断档": 断档}


def 运行片(总批编号: str, 片序: int, 注入失败: str = "") -> dict[str, Any]:
    """单分片一个写事务：成功则落本片快照，失败则本片零残留；不触碰其它片。"""
    try:
        片序 = int(片序)
    except (TypeError, ValueError):
        raise ValueError("片序须为整数")
    注入 = str(注入失败 or "").strip()

    conn = 连接()
    try:
        _起写事务(conn)
        总批 = _读总批(conn, 总批编号)
        日 = 总批["营业日"]
        if 总批["状态"] == 批状态_成功:
            raise ValueError(f"分片总批 {总批编号} 已封成成功，不再接受片提交")
        片 = conn.execute(
            "SELECT * FROM 分片任务 WHERE 总批编号=? AND 批内序=?",
            (总批编号, 片序),
        ).fetchone()
        if not 片:
            raise ValueError(f"分片总批 {总批编号} 无第 {片序} 片")
        期望版本 = int(总批["版本"])
        if 片["状态"] == 片状态_成功 and not 注入:
            conn.execute("COMMIT")
            return {"总批编号": 总批编号, "片序": 片序, "状态": 片状态_成功, "重复": True}

        账户键们 = _列表(片["账户集合"])
        参 = {r["键"]: r["值"] for r in conn.execute("SELECT 键, 值 FROM 台参")}
        缺前日 = 参.get("缺前日策略", "以零为开端")
        重复策略 = 参.get("重复抽取", "盖写同日快照")
        片批 = f"{总批编号}-片{片序}-{uuid.uuid4().hex[:6]}"
        开始 = 此刻()

        # 先整体计算，再统一落库：注入失败时本片一行不写（原子）。
        计划: list[dict] = []
        断档户: list[str] = []
        for 账户键 in 账户键们:
            户 = conn.execute(
                "SELECT * FROM 账户 WHERE 账户键=?", (账户键,)
            ).fetchone()
            if not 户:
                raise ValueError(f"第 {片序} 片声明账户不存在: {账户键}")
            if 日 < 户["开户营业日"]:
                continue
            算 = _算余额(conn, 户, 日, 缺前日)
            计划.append(算)
            if 算["断档"]:
                断档户.append(账户键)

        if 注入:
            # 本片失败：清掉本片既有本批快照（含先前成功后再跑的残留），
            # 记录失败状态，且不写任何新快照；其它已提交片保持不变。
            conn.execute(
                "DELETE FROM 周期快照 WHERE 总批编号=? AND 片序=?",
                (总批编号, 片序),
            )
            conn.execute(
                "UPDATE 分片任务 SET 状态=?, 失败原因=?, 开始时刻=?, 结束时刻=?, "
                "抽取批次编号='', 快照数=0, 重试次数=重试次数+1 "
                "WHERE 总批编号=? AND 批内序=?",
                (片状态_失败, 注入, 开始, 此刻(), 总批编号, 片序),
            )
            _按片重算汇总(conn, 总批编号, 期望版本=期望版本)
            conn.execute("COMMIT")
            return {
                "总批编号": 总批编号,
                "片序": 片序,
                "状态": 片状态_失败,
                "失败原因": 注入,
                "快照数": 0,
            }

        写入 = 0
        盖写 = 0
        旁置 = 0
        for 算 in 计划:
            账户键 = 算["账户键"]
            旧 = conn.execute(
                "SELECT 快照键 FROM 周期快照 WHERE 账户键=? AND 营业日=?",
                (账户键, 日),
            ).fetchone()
            if 旧:
                if 重复策略 != "盖写同日快照":
                    conn.execute(
                        "INSERT INTO 旁置簿(原因, 账户键, 营业日, 载荷, 时刻) VALUES (?,?,?,?,?)",
                        (
                            "同日已有快照",
                            账户键,
                            日,
                            json.dumps({"余额": 算["余额"]}, ensure_ascii=False),
                            此刻(),
                        ),
                    )
                    旁置 += 1
                    continue
                conn.execute(
                    "UPDATE 周期快照 SET 余额=?, 抽取批次编号=?, 来源='日终抽取', "
                    "状态='有效', 总批编号=?, 片序=? WHERE 账户键=? AND 营业日=?",
                    (算["余额"], 片批, 总批编号, 片序, 账户键, 日),
                )
                盖写 += 1
            else:
                conn.execute(
                    "INSERT INTO 周期快照(快照键, 账户键, 营业日, 余额, 抽取批次编号, "
                    "来源, 状态, 总批编号, 片序) VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        f"{账户键}|{日}",
                        账户键,
                        日,
                        算["余额"],
                        片批,
                        "日终抽取",
                        "有效",
                        总批编号,
                        片序,
                    ),
                )
                写入 += 1

        快照数 = 写入 + 盖写
        for 账户键 in 断档户:
            conn.execute(
                "INSERT INTO 断档记录(账户键, 缺营业日, 检出批次编号, 处置, 时刻) "
                "VALUES (?,?,?,?,?)",
                (账户键, 前一营业日(日), 片批, "记断档并继续", 此刻()),
            )

        报告 = {
            "营业日": 日,
            "分片总批": 总批编号,
            "片序": 片序,
            "写入": 写入,
            "盖写": 盖写,
            "旁置": 旁置,
            "断档": len(断档户),
            "账户数": len(计划),
        }
        conn.execute(
            "INSERT INTO 抽取批次(编号, 营业日, 时刻, 报告) VALUES (?,?,?,?)",
            (片批, 日, 此刻(), json.dumps(报告, ensure_ascii=False)),
        )
        conn.execute(
            "UPDATE 分片任务 SET 状态=?, 失败原因='', 抽取批次编号=?, 快照数=?, "
            "开始时刻=?, 结束时刻=?, 重试次数=重试次数+1 WHERE 总批编号=? AND 批内序=?",
            (片状态_成功, 片批, 快照数, 开始, 此刻(), 总批编号, 片序),
        )
        _按片重算汇总(conn, 总批编号, 期望版本=期望版本)
        conn.execute("COMMIT")
        return {
            "总批编号": 总批编号,
            "片序": 片序,
            "状态": 片状态_成功,
            "抽取批次编号": 片批,
            "快照数": 快照数,
            **报告,
        }
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------- 汇总 / 封成


def _快照覆盖(conn, 总批编号: str, 日: str) -> set[str]:
    return {
        r["账户键"]
        for r in conn.execute(
            "SELECT DISTINCT 账户键 FROM 周期快照 WHERE 总批编号=? AND 营业日=? AND 状态='有效'",
            (总批编号, 日),
        )
    }


def _按片重算汇总(
    conn,
    总批编号: str,
    期望版本: int | None = None,
    强制封成: bool = False,
) -> dict[str, Any]:
    """按片实体重算总批（不做盲加），带版本闸；缺片绝不封成成功。"""
    总批 = _读总批(conn, 总批编号)
    片们 = _读片(conn, 总批编号)
    成功数 = sum(1 for r in 片们 if r["状态"] == 片状态_成功)
    失败片 = [r for r in 片们 if r["状态"] == 片状态_失败]
    待运行数 = sum(1 for r in 片们 if r["状态"] == 片状态_待运行)
    失败数 = len(失败片)
    失败账户 = _去重(
        k for r in 失败片 for k in _列表(r["账户集合"])
    )
    声明 = set(_列表(总批["声明账户集合"]))

    if 成功数 == int(总批["分片数"]) and 待运行数 == 0 and 失败数 == 0:
        状态 = 批状态_成功
        可全量 = 1
    elif 失败数 > 0:
        状态 = 批状态_未完成
        可全量 = 0
    else:
        状态 = 批状态_运行中
        可全量 = 0

    if 强制封成 and 状态 != 批状态_成功:
        raise ValueError(
            f"分片未全部成功（成功 {成功数}/{总批['分片数']}，失败 {失败数}，待运行 {待运行数}），"
            "不得封成成功"
        )

    if 状态 == 批状态_成功:
        覆盖 = _快照覆盖(conn, 总批编号, 总批["营业日"])
        if 覆盖 != 声明:
            缺 = sorted(声明 - 覆盖)
            多 = sorted(覆盖 - 声明)
            raise ValueError(
                f"快照覆盖与总批声明不一致，不得封成成功（缺 {缺}，多 {多}）"
            )

    旧 = int(总批["版本"])
    if 期望版本 is not None and 旧 != int(期望版本):
        raise ValueError(
            f"汇总版本冲突：期望版本 {期望版本}，当前版本 {旧}。"
            "片提交与总批汇总交错，已拒绝写入（失败闭）"
        )

    报告 = {
        "营业日": 总批["营业日"],
        "分片数": int(总批["分片数"]),
        "成功片数": 成功数,
        "失败片数": 失败数,
        "待运行片数": 待运行数,
        "声明账户数": len(声明),
        "失败账户集合": 失败账户,
        "成功片": [int(r["批内序"]) for r in 片们 if r["状态"] == 片状态_成功],
        "失败片": [int(r["批内序"]) for r in 失败片],
        "待运行片": [int(r["批内序"]) for r in 片们 if r["状态"] == 片状态_待运行],
    }
    游 = conn.execute(
        "UPDATE 分片总批 SET 状态=?, 已成功片数=?, 失败片数=?, 可当全量回放=?, "
        "失败账户集合=?, 报告=?, 版本=?, 汇总时刻=? WHERE 编号=? AND 版本=?",
        (
            状态,
            成功数,
            失败数,
            可全量,
            json.dumps(失败账户, ensure_ascii=False),
            json.dumps(报告, ensure_ascii=False),
            旧 + 1,
            此刻(),
            总批编号,
            旧,
        ),
    )
    if 游.rowcount != 1:
        raise ValueError("汇总写入版本冲突（行已被并发修改），已拒绝（失败闭）")
    return {"状态": 状态, "可当全量回放": bool(可全量), "成功片数": 成功数, "失败片数": 失败数}


def 补齐并封成(总批编号: str) -> dict[str, Any]:
    """重跑所有未成功片（失败/待运行），覆盖与声明一致后封成成功。"""
    conn = 连接()
    try:
        总批 = _读总批(conn, 总批编号)
        片们 = _读片(conn, 总批编号)
    finally:
        conn.close()
    if 总批["状态"] == 批状态_成功:
        return 读总批(总批编号)
    for 片 in 片们:
        if 片["状态"] != 片状态_成功:
            运行片(总批编号, int(片["批内序"]), 注入失败="")

    conn = 连接()
    try:
        _起写事务(conn)
        _按片重算汇总(conn, 总批编号, 强制封成=True)
        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        conn.close()
    return 读总批(总批编号)


# ---------------------------------------------------------------- 读 / 核证


def 读总批(编号: str) -> dict[str, Any]:
    conn = 连接()
    try:
        return _总批字典(conn, _读总批(conn, 编号))
    finally:
        conn.close()


def 列总批() -> list[dict[str, Any]]:
    conn = 连接()
    try:
        return [
            _总批字典(conn, r)
            for r in conn.execute("SELECT * FROM 分片总批 ORDER BY 时刻 DESC LIMIT 60")
        ]
    finally:
        conn.close()


def 核证(编号: str) -> dict[str, Any]:
    """分片总批核证：各片状态、残留快照、可否当全量回放、valid。"""
    conn = 连接()
    try:
        行 = _读总批(conn, 编号)
        总批 = _总批字典(conn, 行)
        片们 = _读片(conn, 编号)
        声明 = _列表(行["声明账户集合"])
        声明集 = set(声明)
        并集 = _去重(k for r in 片们 for k in _列表(r["账户集合"]))
        并集集 = set(并集)

        失败账户 = _列表(行["失败账户集合"])
        每户 = []
        成功片残留 = 0
        残留总 = 0
        片明细 = []
        for r in 片们:
            集 = _列表(r["账户集合"])
            条 = []
            本片残留 = 0
            for 账户键 in 集:
                快 = conn.execute(
                    "SELECT 余额, 抽取批次编号, 总批编号, 片序 FROM 周期快照 "
                    "WHERE 账户键=? AND 营业日=? AND 状态='有效'",
                    (账户键, 行["营业日"]),
                ).fetchone()
                有 = bool(快 and 快["总批编号"] == 编号)
                本片残留 += 1 if 有 else 0
                条.append(
                    {
                        "账户键": 账户键,
                        "有本批有效快照": 有,
                        "余额": int(快["余额"]) if 有 else None,
                        "片序": int(快["片序"]) if 有 else None,
                    }
                )
            残留总 += 本片残留
            if r["状态"] == 片状态_成功 and 本片残留 > 0:
                成功片残留 += 本片残留
            片明细.append(
                {
                    **_片字典(r),
                    "残留快照数": 本片残留,
                    "账户": 条,
                }
            )
            每户.extend(条)

        覆盖集 = {x["账户键"] for x in 每户 if x["有本批有效快照"]}
        覆盖与声明一致 = 覆盖集 == 声明集
        成功片是否保留快照 = 成功片残留 > 0
        待运行数 = sum(1 for r in 片们 if r["状态"] == 片状态_待运行)

        不一致: list[str] = []
        if 并集集 != 声明集:
            不一致.append("声明账户集合与各片并集不一致")
        if int(行["分片数"]) != len(片们):
            不一致.append("分片数与片实体行数不一致")
        if int(行["已成功片数"]) != sum(1 for r in 片们 if r["状态"] == 片状态_成功):
            不一致.append("总批已成功片数与片实体状态不一致")
        if int(行["失败片数"]) != sum(1 for r in 片们 if r["状态"] == 片状态_失败):
            不一致.append("总批失败片数与片实体状态不一致")
        应失败账户 = _去重(k for r in 片们 if r["状态"] == 片状态_失败 for k in _列表(r["账户集合"]))
        if 失败账户 != 应失败账户:
            不一致.append("失败账户集合与失败片不一致")
        if 行["状态"] == 批状态_成功:
            if 待运行数 or int(行["失败片数"]) or int(行["已成功片数"]) != int(行["分片数"]):
                不一致.append("总批宣称成功但仍有未成功片")
            if not 覆盖与声明一致:
                不一致.append("总批宣称成功但快照覆盖与声明不一致")
            if not 行["可当全量回放"]:
                不一致.append("总批成功却未声明可当全量回放")
        else:
            if 行["可当全量回放"]:
                不一致.append("未完成总批却声明可当全量回放（假成功）")
            if 行["状态"] == 批状态_未完成 and not 失败账户:
                不一致.append("未完成总批缺失败账户集合")
        if int(行["声明账户数"]) != len(声明):
            不一致.append("声明账户数与声明账户集合长度不一致")

        return {
            "编号": 编号,
            "营业日": 行["营业日"],
            "口径": 行["口径"],
            "状态": 行["状态"],
            "分片数": int(行["分片数"]),
            "已成功片数": int(行["已成功片数"]),
            "失败片数": int(行["失败片数"]),
            "待运行片数": 待运行数,
            "声明账户集合": 声明,
            "声明账户数": int(行["声明账户数"]),
            "失败账户集合": 失败账户,
            "各片状态": [
                {"片序": int(r["批内序"]), "状态": r["状态"], "账户集合": _列表(r["账户集合"])}
                for r in 片们
            ],
            "各片": 片明细,
            "残留快照": 每户,
            "残留快照数": 残留总,
            "成功片是否保留快照": 成功片是否保留快照,
            "快照覆盖数": len(覆盖集),
            "覆盖与声明一致": 覆盖与声明一致,
            "可当全量回放": bool(行["可当全量回放"]),
            "是否封成成功": 行["状态"] == 批状态_成功,
            # 补齐并封成会重跑所有未成功片；只要声明分区与片实体一致，重跑即可封成。
            "可封成成功": (
                行["状态"] != 批状态_成功
                and int(行["分片数"]) == len(片们)
                and 并集集 == 声明集
            ),
            # 不重跑、立即汇总即可封成（无失败片/待运行片且覆盖完整）。
            "无需补齐即可封成": (
                行["状态"] != 批状态_成功
                and 待运行数 == 0
                and int(行["失败片数"]) == 0
                and 覆盖与声明一致
            ),
            "valid": not 不一致,
            "不一致项": 不一致,
            "版本": int(行["版本"]),
        }
    finally:
        conn.close()
