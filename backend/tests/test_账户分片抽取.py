# -*- coding: utf-8 -*-
"""账户分片抽取 · 分片总批核证契约测试。

口径：乙-部分成功+未完成总批（全局唯一，选定）。
夹具：三片第二片失败 / 补齐 / 片提交与总批汇总交错。
"""

import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest

os.environ.setdefault("数据目录", "/tmp/daysnap-分片-test")
ROOT = Path(__file__).resolve().parents[2]  # 仓库根
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("夹具目录", str(ROOT / "fixtures"))

from fastapi.testclient import TestClient  # noqa: E402

from app.db import 连接  # noqa: E402
from app.main import app  # noqa: E402
from app.modules import 账户分片抽取 as 分片  # noqa: E402


@pytest.fixture()
def cifra():
    c = TestClient(app)
    with c:
        c.post("/api/重置业务库")
        c.post("/api/装载夹具", json={"名": "三日连环样例.json"})
        yield c


def _开三片(c, 注入=2):
    r = c.post(
        "/api/分片总批",
        json={"营业日": "2026-11-22", "每片账户数": 1, "注入失败片序": str(注入) if 注入 else ""},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _核证(c, 编号):
    r = c.get(f"/api/分片总批/{编号}/核证")
    assert r.status_code == 200, r.text
    return r.json()


# ------------------------------------------------------------ 夹具：三片第二片失败


def test_片二失败_甲成功片亦无本批镜_乙成功片有镜但总批未完成(cifra):
    """一句话区分两口径：成功片是否留有本批快照。"""
    b = _开三片(cifra, 注入=2)
    编号 = b["编号"]

    assert b["口径"] == "乙-部分成功+未完成总批"
    assert b["状态"] == "未完成"
    assert b["可当全量回放"] is False
    assert b["已成功片数"] == 2 and b["失败片数"] == 1
    assert b["失败账户集合"] == ["户乙"]

    核 = _核证(cifra, 编号)
    assert 核["valid"] is True, 核["不一致项"]
    # 与「甲-整票回滚」的可区分断言：成功片的本批快照被保留。
    assert 核["成功片是否保留快照"] is True
    assert 核["残留快照数"] == 2
    残留 = {x["账户键"]: x["有本批有效快照"] for x in 核["残留快照"]}
    assert 残留 == {"户甲": True, "户乙": False, "户丙": True}
    # 成功片(片1/片3)确有本批镜；失败片(片2/户乙)零残留。
    for 片 in 核["各片"]:
        if 片["状态"] == "成功":
            assert 片["残留快照数"] == 片["账户数"]
        else:
            assert 片["残留快照数"] == 0

    # 成功片的本批镜可被数据层直接查到（非仅核证声称）。
    conn = sqlite3.connect(os.environ["数据目录"] + "/日镜账.db")
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM 周期快照 WHERE 总批编号=? AND 状态='有效'", (编号,)
        ).fetchone()[0]
    finally:
        conn.close()
    assert n == 2


def test_未完成总批_全量回放拒绝_诊断回放不通过(cifra):
    b = _开三片(cifra, 注入=2)
    编号 = b["编号"]

    r = cifra.post("/api/回放", json={"营业日": "2026-11-22", "分片总批编号": 编号})
    assert r.status_code == 400
    assert "不可当全量回放" in r.json()["detail"]

    r = cifra.post(
        "/api/回放", json={"营业日": "2026-11-22", "分片总批编号": 编号, "诊断": True}
    )
    assert r.status_code == 200, r.text
    报 = r.json()
    assert 报["诊断"] is True
    assert 报["通过"] is False  # 诊断回放永不当全量通过
    assert 报["总批状态"] == "未完成"
    assert 报["范围与总批声明一致"] is True
    assert sorted(报["回放范围"]) == sorted(b["声明账户集合"])


def test_回放范围必须与总批声明一致_营业日不符即拒(cifra):
    b = _开三片(cifra, 注入=0)
    # 已完成总批：营业日不符拒绝
    r = cifra.post("/api/回放", json={"营业日": "2026-11-21", "分片总批编号": b["编号"]})
    assert r.status_code == 400
    assert "范围不符" in r.json()["detail"]


# ------------------------------------------------------------ 夹具：补齐后封成成功


def test_补齐失败片后总批封成成功且全量回放通过(cifra):
    b = _开三片(cifra, 注入=2)
    编号 = b["编号"]

    核 = _核证(cifra, 编号)
    assert 核["可封成成功"] is True  # 失败片可重跑补齐
    assert 核["无需补齐即可封成"] is False  # 仍有失败片，必须先补齐

    r = cifra.post(f"/api/分片总批/{编号}/封成")
    assert r.status_code == 200, r.text
    封 = r.json()
    assert 封["状态"] == "成功"
    assert 封["可当全量回放"] is True
    assert 封["已成功片数"] == 3 and 封["失败片数"] == 0
    assert 封["失败账户集合"] == []

    核 = _核证(cifra, 编号)
    assert 核["valid"] is True, 核["不一致项"]
    assert 核["是否封成成功"] is True
    assert 核["覆盖与声明一致"] is True
    assert 核["残留快照数"] == 3
    assert 核["可封成成功"] is False  # 已经成功，不再可封

    r = cifra.post("/api/回放", json={"营业日": "2026-11-22", "分片总批编号": 编号})
    assert r.status_code == 200, r.text
    assert r.json()["通过"] is True
    assert r.json()["范围与总批声明一致"] is True


def test_未补齐前不得封成成功(cifra):
    """开批但不跑任何片：不应有片被算成功，纯汇总封成必须被拒。"""
    # 直接经模块建批、关闭自动运行，制造待运行片。
    行 = 分片.开总批("2026-11-22", 每片账户数=1, 自动运行=False)
    编号 = 行["编号"]
    核 = _核证(cifra, 编号)
    assert 核["状态"] == "运行中"
    assert 核["可当全量回放"] is False
    assert 核["valid"] is True

    # 纯汇总（不跑片）尝试对运行中/待运行总批封成，必须拒绝。
    conn = 连接()
    try:
        with pytest.raises(ValueError):
            分片._按片重算汇总(conn, 编号, 强制封成=True)
    finally:
        conn.close()

    # 补齐接口会重跑待运行片，随后合法封成成功。
    r = cifra.post(f"/api/分片总批/{编号}/封成")
    assert r.status_code == 200, r.text
    assert r.json()["状态"] == "成功"
    assert r.json()["可当全量回放"] is True


# ------------------------------------------------------------ 夹具：片提交与总批汇总交错


def test_片提交与总批汇总交错必须失败闭_不出缺片总批(cifra):
    总批 = 分片.开总批("2026-11-22", 每片账户数=1, 自动运行=False)
    编号 = 总批["编号"]
    # 提交片1、片2，片3尚未提交
    分片.运行片(编号, 1)
    分片.运行片(编号, 2)
    陈旧版本 = 总批["版本"]

    # 交错：一个汇总事务先读到陈旧版本，期间片3提交推进版本。
    # 陈旧汇总落库时版本闸必须拒绝（失败闭），不得覆盖成缺片总批。
    分片.运行片(编号, 3)
    conn = 连接()
    try:
        with pytest.raises(ValueError) as ei:
            分片._按片重算汇总(conn, 编号, 期望版本=陈旧版本)
        assert "版本冲突" in str(ei.value)
    finally:
        conn.close()

    # 交错被拒后：总批绝不能被标为「成功但缺片」。
    核 = _核证(cifra, 编号)
    assert 核["valid"] is True, 核["不一致项"]
    assert 核["状态"] == "成功"
    assert 核["覆盖与声明一致"] is True
    assert 核["残留快照数"] == 核["声明账户数"]


def test_并发片提交与汇总交错_真实线程仍失败闭出完整总批(cifra):
    """真线程并发：多片同时提交 + 汇总同时抢占，最终总批成功必须覆盖完整。"""
    import threading

    总批 = 分片.开总批("2026-11-22", 每片账户数=1, 自动运行=False)
    编号 = 总批["编号"]
    错误: list[str] = []

    def 跑(序):
        try:
            分片.运行片(编号, 序)
        except Exception as e:  # noqa: BLE001
            错误.append(f"{序}:{e}")

    线 = [threading.Thread(target=跑, args=(i,)) for i in (1, 2, 3)]
    for t in 线:
        t.start()
    for t in 线:
        t.join()
    assert not 错误, 错误

    核 = _核证(cifra, 编号)
    # 不得因并发汇总产生「成功但缺片」。
    assert 核["valid"] is True, 核["不一致项"]
    if 核["状态"] == "成功":
        assert 核["覆盖与声明一致"] is True
        assert 核["残留快照数"] == 核["声明账户数"]
    else:
        assert 核["可当全量回放"] is False


def test_缺片时新鲜汇总也不得标成功(cifra):
    """片3始终未提交：即便用最新版本汇总，也绝不能标成成功/可全量回放。"""
    总批 = 分片.开总批("2026-11-22", 每片账户数=1, 自动运行=False)
    编号 = 总批["编号"]
    分片.运行片(编号, 1)
    分片.运行片(编号, 2)
    # 片3 仍是待运行。

    conn = 连接()
    try:
        with pytest.raises(ValueError):
            分片._按片重算汇总(conn, 编号, 强制封成=True)
    finally:
        conn.close()

    核 = _核证(cifra, 编号)
    assert 核["状态"] == "运行中"
    assert 核["可当全量回放"] is False
    assert 核["待运行片数"] == 1
    assert 核["valid"] is True
    # 缺片时不得出现无本批镜的「已发布」错觉。
    assert 核["残留快照数"] == 2
    assert 核["覆盖与声明一致"] is False


def test_成功总批不得缺片或超范围(cifra):
    """对总批做篡改（删一片快照），核证必须判 valid=false。"""
    b = _开三片(cifra, 注入=0)
    编号 = b["编号"]
    assert b["状态"] == "成功"

    conn = sqlite3.connect(os.environ["数据目录"] + "/日镜账.db")
    try:
        # 人为删除户乙的本批快照，制造缺片。
        conn.execute("DELETE FROM 周期快照 WHERE 总批编号=? AND 账户键='户乙'", (编号,))
        conn.commit()
    finally:
        conn.close()

    核 = _核证(cifra, 编号)
    assert 核["valid"] is False
    assert "快照覆盖与声明不一致" in " ".join(核["不一致项"])


def test_重置清空分片实体(cifra):
    _开三片(cifra, 注入=2)
    cifra.post("/api/重置业务库")
    assert cifra.get("/api/分片总批").json() == []


def test_夹具文件齐备并声明乙口径():
    for 名, 期望片 in [
        ("账户分片-片二失败样例.json", 3),
        ("账户分片-补齐样例.json", None),
        ("账户分片-交错并发样例.json", None),
    ]:
        数据 = json.loads((ROOT / "fixtures" / 名).read_text(encoding="utf-8"))
        assert 数据.get("营业日") == "2026-11-22"
        预期 = 数据.get("预期", {})
        if 期望片:
            assert 预期["总批状态"] == "未完成"
            assert 预期["成功片是否保留快照"] is True
            assert 预期["可当全量回放"] is False


def test_成功片重跑注入失败后本片零残留(cifra):
    """片先成功后重跑注入失败：本片快照被清空，绝不「片失败却仍有本批镜」。"""
    # 开批不自动跑；片1/3 先成功，片2 仍待运行 → 总批未封成，可再提交。
    总批 = 分片.开总批("2026-11-22", 每片账户数=1, 自动运行=False)
    编号 = 总批["编号"]
    分片.运行片(编号, 1)
    分片.运行片(编号, 3)
    assert 分片.读总批(编号)["状态"] != "成功"

    # 对已成功片3重跑并注入失败
    r = cifra.post(f"/api/分片总批/{编号}/运行片", json={"片序": 3, "注入失败": "夹具注入：重跑失败"})
    assert r.status_code == 200, r.text
    assert r.json()["状态"] == "失败"

    核 = _核证(cifra, 编号)
    assert 核["valid"] is True, 核["不一致项"]
    assert 核["状态"] == "未完成"  # 有失败片即未完成（片2 仍待运行）
    assert 核["待运行片数"] == 1
    assert 核["可当全量回放"] is False
    片3 = next(p for p in 核["各片"] if p["片序"] == 3)
    assert 片3["残留快照数"] == 0
    assert all(not a["有本批有效快照"] for a in 片3["账户"])
    # 其它成功片仍保留本批镜（乙口径）。
    assert 核["成功片是否保留快照"] is True


def test_已封成成功不再接受片提交(cifra):
    b = _开三片(cifra, 注入=0)
    assert b["状态"] == "成功"
    r = cifra.post(
        f"/api/分片总批/{b['编号']}/运行片", json={"片序": 1, "注入失败": "重跑"}
    )
    assert r.status_code == 400
    assert "已封成成功" in r.json()["detail"]
