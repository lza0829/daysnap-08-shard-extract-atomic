import React, { useEffect, useState } from "react";
import { api } from "../api.js";

export default function 回放核对() {
  const [日, set日] = useState("2026-11-22");
  const [批, set批] = useState([]);
  const [总批们, set总批们] = useState([]);
  const [总批, set总批] = useState("");
  const [诊断, set诊断] = useState(false);
  const [讯, set讯] = useState("");

  const 刷新 = () => Promise.all([api.回放批次().then(set批), api.分片总批().then(set总批们)]);

  useEffect(() => {
    刷新().catch((e) => set讯(String(e.message || e)));
  }, []);

  const 跑 = () =>
    api
      .回放(日, 总批, 诊断)
      .then((r) =>
        set讯(
          `批次 ${r.批次编号} 通过=${r.通过} 相符=${r.相符} 差额=${r.差额} 无快照=${r.无快照}` +
            (r.诊断 ? `（诊断回放，范围=${r.回放范围.length}户，永不计全量通过）` : "")
        )
      )
      .then(刷新)
      .catch((e) => set讯(String(e.message || e)));

  const 选中批 = 总批们.find((b) => b.编号 === 总批);

  return (
    <div>
      <h1 className="题">回放核对</h1>
      <p className="述">
        用前日快照加当日流水重算，对照周期快照，写出差额行。选定分片总批时，回放范围被强制限定为该总批声明账户集合；未完成总批禁止全量通过，仅可诊断回放。
      </p>
      <div className="行">
        <input value={日} onChange={(e) => set日(e.target.value)} />
        <select value={总批} onChange={(e) => set总批(e.target.value)}>
          <option value="">全量（不分片总批）</option>
          {总批们.map((b) => (
            <option key={b.编号} value={b.编号}>
              {b.编号} · {b.状态} · {b.可当全量回放 ? "可全量" : "不可全量"}
            </option>
          ))}
        </select>
        <label className="述" style={{ display: "flex", alignItems: "center", gap: "0.35rem" }}>
          <input type="checkbox" checked={诊断} onChange={(e) => set诊断(e.target.checked)} />
          诊断回放
        </label>
        <button className="水" onClick={跑}>
          执行回放
        </button>
        <button className="次" onClick={() => 刷新().catch((e) => set讯(String(e.message || e)))}>
          刷新批次
        </button>
      </div>
      {选中批 && !选中批.可当全量回放 ? (
        <div className="提示条">
          该总批状态 <b>{选中批.状态}</b>，不可当全量回放。若不带「诊断回放」会被拒绝；带诊断时结果永不计为通过。
        </div>
      ) : null}
      {讯 ? <p className="讯">{讯}</p> : null}
      <div className="段">
        <h2 className="段题">回放批次</h2>
        {!批.length ? (
          <div className="空态">尚无回放</div>
        ) : (
          <table>
            <thead>
              <tr>
                <th>编号</th>
                <th>营业日</th>
                <th>时刻</th>
                <th>报告</th>
              </tr>
            </thead>
            <tbody>
              {批.map((r) => (
                <tr key={r.编号}>
                  <td>{r.编号}</td>
                  <td>{r.营业日}</td>
                  <td>{r.时刻}</td>
                  <td>{typeof r.报告 === "object" ? JSON.stringify(r.报告) : r.报告}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
