import React, { useEffect, useState } from "react";
import { api } from "../api.js";

function 徽(r) {
  if (r === "成功") return <span className="徽章 通">成功</span>;
  if (r === "失败") return <span className="徽章 败">失败</span>;
  return <span className="徽章">{r}</span>;
}

export default function 日终抽取() {
  const [日, set日] = useState("2026-11-22");
  const [批, set批] = useState([]);
  const [片宽, set片宽] = useState(1);
  const [注入, set注入] = useState("");
  const [总批们, set总批们] = useState([]);
  const [选中, set选中] = useState(null); // 核证结果
  const [讯, set讯] = useState("");

  const 刷新 = () =>
    Promise.all([api.抽取批次().then(set批), api.分片总批().then(set总批们)]);
  const 选 = (id) =>
    api.核证分片总批(id).then(set选中).catch((e) => set讯(String(e.message || e)));

  useEffect(() => {
    刷新().catch((e) => set讯(String(e.message || e)));
  }, []);

  const 开批 = () =>
    api
      .开分片总批(日, Number(片宽) || 1, 注入.trim())
      .then((r) => {
        set讯(
          `总批 ${r.编号} 状态=${r.状态} 成功片=${r.已成功片数}/${r.分片数}${
            r.失败账户集合.length ? ` 失败账户=${r.失败账户集合.join(",")}` : ""
          }`
        );
        return 选(r.编号);
      })
      .then(刷新)
      .catch((e) => set讯(String(e.message || e)));

  return (
    <div>
      <h1 className="题">日终抽取</h1>
      <p className="述">
        按营业日写周期快照：前日余额 + 当日流水净额。分片总批口径为
        <b> 部分成功 + 未完成总批</b>：任一片失败不回滚其它已成功片，但总批不得标成功，失败片须补齐后才可封成。
      </p>

      <div className="行">
        <input value={日} onChange={(e) => set日(e.target.value)} placeholder="YYYY-MM-DD" />
        <button
          onClick={() =>
            api
              .抽取(日)
              .then((r) => set讯(`批次 ${r.批次编号} 写入=${r.写入} 盖写=${r.盖写} 断档=${r.断档}`))
              .then(刷新)
              .catch((e) => set讯(String(e.message || e)))
          }
        >
          单批抽取
        </button>
        <button className="次" onClick={() => 刷新().catch((e) => set讯(String(e.message || e)))}>
          刷新
        </button>
      </div>
      {讯 ? <p className="讯">{讯}</p> : null}

      <div className="段">
        <h2 className="段题">账户分片抽取 · 分片总批</h2>
        <div className="行">
          <span className="述">每片账户数</span>
          <input
            style={{ width: "5rem" }}
            value={片宽}
            onChange={(e) => set片宽(e.target.value)}
            placeholder="1"
          />
          <span className="述">注入失败片序(空=不注入)</span>
          <input
            style={{ width: "5rem" }}
            value={注入}
            onChange={(e) => set注入(e.target.value)}
            placeholder="2"
          />
          <button onClick={开批}>开分片总批并跑片</button>
        </div>

        {!总批们.length ? (
          <div className="空态">尚无分片总批</div>
        ) : (
          <table>
            <thead>
              <tr>
                <th>编号</th>
                <th>营业日</th>
                <th>状态</th>
                <th>成功/失败片</th>
                <th>失败账户</th>
                <th>可当全量回放</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {总批们.map((r) => (
                <tr key={r.编号}>
                  <td>{r.编号}</td>
                  <td>{r.营业日}</td>
                  <td>{徽(r.状态)}</td>
                  <td>
                    {r.已成功片数}/{r.分片数} · 失败 {r.失败片数}
                  </td>
                  <td>{r.失败账户集合.length ? r.失败账户集合.join("、") : "—"}</td>
                  <td>
                    {r.可当全量回放 ? (
                      <span className="徽章 通">是</span>
                    ) : (
                      <span className="徽章 败">否</span>
                    )}
                  </td>
                  <td>
                    <button className="次" onClick={() => 选(r.编号)}>
                      核证
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {选中 ? <核证面板 核={选中} onRefresh={() => 选(选中.编号)} on改为={set选中} set讯={set讯} /> : null}
    </div>
  );
}

function 核证面板({ 核, onRefresh, set讯 }) {
  const 补 = () =>
    api
      .封成分片总批(核.编号)
      .then((r) => {
        set讯(`总批 ${r.编号} 已封成：状态=${r.状态} 可当全量回放=${r.可当全量回放}`);
        return onRefresh();
      })
      .catch((e) => set讯(String(e.message || e)));

  const 单跑 = (片序) =>
    api
      .运行分片(核.编号, 片序)
      .then((r) => {
        set讯(`片 ${片序} 运行=${r.状态}${r.失败原因 ? `：${r.失败原因}` : ""}`);
        return onRefresh();
      })
      .catch((e) => set讯(String(e.message || e)));

  const 全量回放 = () =>
    api
      .回放(核.营业日, 核.编号, false)
      .then((r) => set讯(`回放通过=${r.通过} 相符=${r.相符} 差额=${r.差额}`))
      .catch((e) => set讯(String(e.message || e)));

  const 诊断回放 = () =>
    api
      .回放(核.营业日, 核.编号, true)
      .then((r) => set讯(`诊断回放：通过=${r.通过}（诊断永不计全量通过）范围=${r.回放范围.length}户`))
      .catch((e) => set讯(String(e.message || e)));

  return (
    <div className="段">
      <h2 className="段题">分片总批核证 · {核.编号}</h2>
      <p className="述">
        口径 {核.口径} · 状态 {核.状态} · {核.valid ? <span className="徽章 通">valid</span> : <span className="徽章 败">invalid</span>}
        {核.不一致项?.length ? `：${核.不一致项.join("；")}` : ""}
      </p>
      <p className="述">
        成功片是否保留快照：
        <b>{核.成功片是否保留快照 ? "是" : "否"}</b>
        （「乙-部分成功」口径下为是；整票回滚口径应为否）· 残留快照 {核.残留快照数} · 声明 {核.声明账户数} 户 ·
        覆盖与声明一致：{核.覆盖与声明一致 ? "是" : "否"}
      </p>
      <div className="行">
        <button className="水" onClick={全量回放}>
          全量回放
        </button>
        <button className="次" onClick={诊断回放}>
          诊断回放
        </button>
        <button onClick={补} disabled={!核.可封成成功}>
          {核.可封成成功 ? "补齐失败/待运行片并封成成功" : "已成功 / 不可封成"}
        </button>
        <button className="次" onClick={onRefresh}>
          重新核证
        </button>
      </div>

      <table>
        <thead>
          <tr>
            <th>片序</th>
            <th>账户</th>
            <th>状态</th>
            <th>本批快照</th>
            <th>失败原因</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {核.各片.map((p) => (
            <tr key={p.片序}>
              <td>{p.片序}</td>
              <td>{p.账户集合.join("、")}</td>
              <td>{徽(p.状态)}</td>
              <td>
                {p.账户.map((a) => (
                  <span key={a.账户键} style={{ marginRight: "0.6rem" }}>
                    {a.账户键}：
                    {a.有本批有效快照 ? (
                      <span className="徽章 通">{a.余额}</span>
                    ) : (
                      <span className="徽章 败">无</span>
                    )}
                  </span>
                ))}
              </td>
              <td>{p.失败原因 || "—"}</td>
              <td>
                {p.状态 !== "成功" ? (
                  <button className="次" onClick={() => 单跑(p.片序)}>
                    重跑
                  </button>
                ) : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
