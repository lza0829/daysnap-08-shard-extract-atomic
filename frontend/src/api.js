async function 请求(path, opts = {}) {
  const r = await fetch(`/api${path}`, {
    headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
    ...opts,
  });
  const text = await r.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = text;
  }
  if (!r.ok) {
    const msg = (data && data.detail) || text || r.statusText;
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return data;
}

export const api = {
  健康: () => 请求("/健康"),
  总览: () => 请求("/总览"),
  台参: () => 请求("/台参"),
  账户: () => 请求("/账户"),
  流水: (q = "") => 请求(`/流水${q}`),
  快照: (q = "") => 请求(`/快照${q}`),
  差额: (q = "") => 请求(`/差额${q}`),
  断档: () => 请求("/断档"),
  跳变: () => 请求("/跳变"),
  旁置: () => 请求("/旁置"),
  夹具: () => 请求("/夹具"),
  装载批次: () => 请求("/装载批次"),
  抽取批次: () => 请求("/抽取批次"),
  回放批次: () => 请求("/回放批次"),
  装载夹具: (名) => 请求("/装载夹具", { method: "POST", body: JSON.stringify({ 名 }) }),
  抽取: (营业日) => 请求("/抽取", { method: "POST", body: JSON.stringify({ 营业日 }) }),
  回放: (营业日, 分片总批编号 = "", 诊断 = false) =>
    请求("/回放", {
      method: "POST",
      body: JSON.stringify({ 营业日, 分片总批编号, 诊断 }),
    }),
  分片总批: () => 请求("/分片总批"),
  取分片总批: (id) => 请求(`/分片总批/${encodeURIComponent(id)}`),
  核证分片总批: (id) => 请求(`/分片总批/${encodeURIComponent(id)}/核证`),
  开分片总批: (营业日, 每片账户数 = 1, 注入失败片序 = "") =>
    请求("/分片总批", {
      method: "POST",
      body: JSON.stringify({ 营业日, 每片账户数, 注入失败片序 }),
    }),
  运行分片: (id, 片序, 注入失败 = "") =>
    请求(`/分片总批/${encodeURIComponent(id)}/运行片`, {
      method: "POST",
      body: JSON.stringify({ 片序, 注入失败 }),
    }),
  封成分片总批: (id) =>
    请求(`/分片总批/${encodeURIComponent(id)}/封成`, { method: "POST", body: "{}" }),
  重置: () => 请求("/重置业务库", { method: "POST", body: "{}" }),
};
