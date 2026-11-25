import React, { useEffect, useState } from "react";
import { api } from "../api.js";

// 差额页不得把「未完成总批」当已发布：对落在未完成总批上的差额行显式标注「未完成·未发布」，
// 且页顶统计只把可当全量回放的总批计入「已发布」。
export default function 差额清单() {
  const [行, set行] = useState([]);
  const [总批们, set总批们] = useState([]);
  const [讯, set讯] = useState("");

  const 刷新 = () =>
    Promise.all([api.差额().then(set行), api.分片总批().then(set总批们)]).catch((e) =>
      set讯(String(e.message || e))
    );

  useEffect(() => {
    刷新();
  }, []);

  const 按账户 = {};
  (总批们 || []).forEach((b) => {
    b.声明账户集合.forEach((k) => {
      按账户[k] = 按账户[k] || [];
      按账户[k].push(b);
    });
  });

  const 归属未完成 = (r) => {
    const 候 = (按账户[r.账户键] || []).filter((b) => b.营业日 === r.营业日);
    // 优先取未完成/运行中总批：该行来源不可当已发布。
    return 候.find((b) => !b.可当全量回放) || null;
  };

  const 已发布数 = (总批们 || []).filter((b) => b.可当全量回放).length;

  const 未完成数 = 行.filter((r) => 归属未完成(r)).length;

  return (
    <div>
      <h1 className="题">差额清单</h1>
      <p className="述">
        快照余额与回放余额不一致时落在这里；冒烟通过时应为空或仅历史批。未完成分片总批的快照<b>不算已发布</b>，
        相关差额行标注「未完成·未发布」，不并入已发布口径。
      </p>
      <p className="述">
        总批：已发布(可当全量回放) <b>{已发布数}</b> 个 · 未完成相关差额行 <b>{未完成数}</b> 行
      </p>
      <div className="行">
        <button className="次" onClick={刷新}>
          刷新
        </button>
      </div>
      {讯 ? <p className="讯">{讯}</p> : null}
      {!行.length ? (
        <div className="空态">暂无差额行</div>
      ) : (
        <table>
          <thead>
            <tr>
              <th>回放批次</th>
              <th>账户键</th>
              <th>营业日</th>
              <th>快照余额</th>
              <th>回放余额</th>
              <th>差额</th>
              <th>原因</th>
              <th>发布口径</th>
            </tr>
          </thead>
          <tbody>
            {行.map((r) => {
              const 未 = 归属未完成(r);
              return (
                <tr key={r.编号}>
                  <td>{r.回放批次编号}</td>
                  <td>{r.账户键}</td>
                  <td>{r.营业日}</td>
                  <td>{r.快照余额 ?? "—"}</td>
                  <td>{r.回放余额 ?? "—"}</td>
                  <td>{r.差额}</td>
                  <td>{r.原因}</td>
                  <td>
                    {未 ? (
                      <span className="徽章 败" title={`总批 ${未.编号} 状态=${未.状态}`}>
                        未完成·未发布
                      </span>
                    ) : (
                      <span className="徽章 通">已发布</span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}
