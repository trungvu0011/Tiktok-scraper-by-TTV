/* TikTok Scraper dashboard — React (UMD) + htm, no build step.
 * State lives in <App/> and is mirrored to localStorage, so switching tabs
 * and reloading / going back keeps every result on screen. */
const html = htm.bind(React.createElement);
const { useState, useEffect, useRef, useMemo } = React;

/* ----------------------------------------------------------------- helpers */
const nf = new Intl.NumberFormat("en-US");
const fmtNum = (n) => nf.format(Math.round(n || 0));
const fmtCompact = (n) => {
  n = n || 0;
  if (n >= 1e9) return (n / 1e9).toFixed(n % 1e9 ? 1 : 0) + "B";
  if (n >= 1e6) return (n / 1e6).toFixed(n % 1e6 ? 1 : 0) + "M";
  if (n >= 1e3) return (n / 1e3).toFixed(n % 1e3 ? 1 : 0) + "K";
  return String(n);
};
const fmtDate = (s) => (s ? s.slice(0, 10) : "");
const fmtSec = (s) => { s = Math.floor(s || 0); return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0"); };
/* relative time, vi — "14 phút trước", "3 giờ trước", "2 ngày trước" */
const fmtAgo = (ms) => {
  const s = Math.floor((Date.now() - (ms || 0)) / 1000);
  if (s < 60) return "vừa xong";
  const m = Math.floor(s / 60); if (m < 60) return m + " phút trước";
  const h = Math.floor(m / 60); if (h < 24) return h + " giờ trước";
  const d = Math.floor(h / 24); if (d < 30) return d + " ngày trước";
  return new Date(ms).toLocaleDateString("vi-VN");
};
/* job-type metadata (history + overview) */
const JOB_TYPES = {
  account: { label: "Tài khoản", icon: "@" },
  link: { label: "Link", icon: "↗" },
  social: { label: "Social", icon: "S" },
  transcript: { label: "Transcript", icon: "T" },
};
const er = (v) => (v.views ? (v.likes + v.comments + v.shares) / v.views * 100 : 0);
const erColor = (x) => (x >= 10 ? "var(--green)" : x >= 5 ? "var(--amber)" : "var(--muted)");
const weekdayIdx = (s) => { const d = new Date(s); return isNaN(d) ? -1 : (d.getUTCDay() + 6) % 7; };
const WD_NAMES = ["T2", "T3", "T4", "T5", "T6", "T7", "CN"];
const isVideoUrl = (u) => /\/(video|photo)\/\d+/.test(u || "");
/* smooth SVG path through points [[x,y],…] via Catmull-Rom → cubic bézier */
const smoothPath = (pts) => {
  if (!pts || pts.length < 2) return "";
  let d = "M" + pts[0][0] + "," + pts[0][1];
  for (let i = 0; i < pts.length - 1; i++) {
    const p0 = pts[i - 1] || pts[i], p1 = pts[i], p2 = pts[i + 1], p3 = pts[i + 2] || p2;
    const c1x = p1[0] + (p2[0] - p0[0]) / 6, c1y = p1[1] + (p2[1] - p0[1]) / 6;
    const c2x = p2[0] - (p3[0] - p1[0]) / 6, c2y = p2[1] - (p3[1] - p1[1]) / 6;
    d += " C" + c1x.toFixed(2) + "," + c1y.toFixed(2) + " " + c2x.toFixed(2) + "," + c2y.toFixed(2) + " " + p2[0] + "," + p2[1];
  }
  return d;
};

/* keyword-cloud tokenizer — drop Vietnamese/English filler + emoji + numbers */
const STOPWORDS = new Set((
  "và là của có không được các những một người cho này đã khi với để ở ra thì mà nên cũng " +
  "rất lại nếu vì bị đi em anh chị mình bạn ơi nhé nha vậy gì sao thế đó đây kia nó họ tôi " +
  "ta chúng cái con do từ theo về lên xuống vào qua tại bởi hay hoặc nhưng còn chỉ đều vẫn " +
  "sẽ đang rồi quá lắm ừ ờ ok oke ạ à á ad ý mn thôi luôn cứ chứ chưa hơn như trên dưới giờ " +
  "ngày làm muốn biết nói xem ai bao mới nè vl vcl trời ời mà ạ the and you for that this " +
  "with are was its has have but not all can our your his her their"
).split(/\s+/).filter(Boolean));

function tokenize(text) {
  const out = [];
  const m = (text || "").toLowerCase().match(/[\p{L}\p{N}]+/gu);
  if (m) for (const w of m) {
    if (w.length < 2 || /^\d+$/.test(w) || STOPWORDS.has(w)) continue;
    out.push(w);
  }
  return out;
}

/* social-link platforms (auto-detect) */
const PLATFORMS = {
  youtube:   { label: "YouTube",   color: "#ff0033" },
  tiktok:    { label: "TikTok",    color: "#fe2c55" },
  facebook:  { label: "Facebook",  color: "#1877f2" },
  instagram: { label: "Instagram", color: "#e1306c" },
};
const detectPlatform = (u) => {
  u = (u || "").toLowerCase();
  if (/youtube\.com|youtu\.be/.test(u)) return "youtube";
  if (/tiktok\.com/.test(u)) return "tiktok";
  if (/facebook\.com|fb\.watch|fb\.com/.test(u)) return "facebook";
  if (/instagram\.com/.test(u)) return "instagram";
  return null;
};

async function api(type, target, extra) {
  let url = "/api/scrape?type=" + type + "&target=" + encodeURIComponent(target);
  if (extra) for (const k in extra) if (extra[k]) url += "&" + k + "=" + encodeURIComponent(extra[k]);
  const res = await fetch(url);
  return res.json();
}

function download(filename, text, type) {
  const blob = new Blob([text], { type });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename; a.click();
  URL.revokeObjectURL(a.href);
}

function csvOf(rows, cols) {
  const head = cols.join(",");
  const body = rows.map((r) => cols.map((c) => {
    let val = r[c];
    if (Array.isArray(val)) val = val.join(" ");
    val = String(val == null ? "" : val).replace(/"/g, '""');
    return `"${val}"`;
  }).join(","));
  return [head, ...body].join("\r\n");
}

function segText(t, withTimes) {
  if (!t) return "";
  if (withTimes) return (t.segments || []).map((s) => "[" + fmtSec(s.start_s) + "] " + s.text).join("\n");
  return t.text || (t.segments || []).map((s) => s.text).join("\n");
}

function segSrt(t) {
  if (!t) return "";
  const pad = (n, l = 2) => String(n).padStart(l, "0");
  const ts = (sec) => {
    sec = sec || 0;
    const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60),
      s = Math.floor(sec % 60), ms = Math.round((sec - Math.floor(sec)) * 1000);
    return pad(h) + ":" + pad(m) + ":" + pad(s) + "," + pad(ms, 3);
  };
  return (t.segments || []).map((s, i) =>
    (i + 1) + "\n" + ts(s.start_s) + " --> " + ts(s.end_s) + "\n" + s.text + "\n").join("\n");
}

/* localStorage-backed state. Writes are best-effort (ignore quota errors). */
function usePersist(key, initial) {
  const [v, setV] = useState(() => {
    try { const s = localStorage.getItem("ttk:" + key); return s != null ? JSON.parse(s) : initial; }
    catch { return initial; }
  });
  useEffect(() => {
    try { localStorage.setItem("ttk:" + key, JSON.stringify(v)); } catch (_) {}
  }, [v]);
  return [v, setV];
}

/* --------------------------------------------------------------- tiny UI */
function Spinner() { return html`<span className="spinner"></span>`; }

function Status({ s }) {
  if (!s || !s.text) return html`<div className="status"></div>`;
  return html`<div className=${"status" + (s.cls ? " " + s.cls : "")}>
    ${s.busy ? html`<${Spinner}/>` : null}${s.text}
  </div>`;
}

function Metric({ k, val, sub, color, accent }) {
  return html`<div className=${"metric" + (accent ? " accent" : "")}>
    <div className="k">${color ? html`<span className="dot" style=${{ background: color }}></span>` : null}${k}</div>
    <div className="v" style=${accent ? { color: "var(--pink2)" } : null}>${val}</div>
    <div className="sub">${sub || ""}</div>
  </div>`;
}

function SecTitle({ children, style }) {
  return html`<div className="sec-title" style=${style}>${children}</div>`;
}

/* frequency cloud — items: [{key,label,count}] sorted desc; font scales 13→30px */
function Cloud({ items, selected, onPick, accent }) {
  if (!items || !items.length) return html`<div className="empty">—</div>`;
  const counts = items.map((i) => i.count);
  const lo = Math.min(...counts), hi = Math.max(...counts);
  const size = (c) => (hi === lo ? 17 : 13 + (c - lo) / (hi - lo) * 17);
  return html`<div className=${"cloud" + (accent ? " " + accent : "")}>
    ${items.map((it) => html`<span key=${it.key}
        className=${"cw" + (selected === it.key ? " sel" : "")}
        style=${{ fontSize: size(it.count).toFixed(1) + "px" }}
        title=${it.label + " · " + it.count}
        onClick=${() => onPick(it)}>
      ${it.label}<span className="cn">${it.count}</span>
    </span>`)}
  </div>`;
}

/* centered popup modal — closes on overlay click, ✕, or Escape */
function Modal({ title, badge, onClose, children }) {
  useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => { document.removeEventListener("keydown", onKey); document.body.style.overflow = ""; };
  }, []);
  return html`<div className="modal-overlay" onClick=${onClose}>
    <div className="modal" onClick=${(e) => e.stopPropagation()}>
      <div className="modal-head">
        <div className="modal-title">${title}${badge != null
          ? html`<span className="count-pill"><b>${fmtNum(badge)}</b> video</span>` : null}</div>
        <button className="modal-x" onClick=${onClose}>✕ Đóng</button>
      </div>
      <div className="modal-body">${children}</div>
    </div>
  </div>`;
}

/* --------------------------------------------------------------- profile */
function ProfileCard({ p, duration, videoCount }) {
  if (!p) return null;
  const name = p.nickname || p.username || "";
  const initials = name.trim().split(/\s+/).map((w) => w[0]).slice(0, 2).join("").toUpperCase() || "?";
  const url = p.profile_url || ("https://www.tiktok.com/@" + p.username);
  return html`<section>
    <div className="panel">
      <div className="profile">
        ${p.avatar_url
          ? html`<img className="pf-av pf-avimg" src=${p.avatar_url} referrerPolicy="no-referrer" alt=${name}/>`
          : html`<div className="pf-av">${initials}</div>`}
        <div className="pf-main">
          <div className="pf-name">
            <h2>${name}</h2>
            ${p.is_verified ? html`<svg className="verified" viewBox="0 0 24 24" fill="none">
              <circle cx="12" cy="12" r="11" fill="var(--blue)"/>
              <path d="M7 12.5l3.2 3.2L17 9" stroke="#fff" strokeWidth="2.4"
                    strokeLinecap="round" strokeLinejoin="round"/></svg>` : null}
          </div>
          <div className="pf-user"><a href=${url} target="_blank">@${p.username}</a></div>
          <div className="pf-stats">
            <div className="pf-stat"><b>${fmtCompact(p.following_count)}</b><span>Đang follow</span></div>
            <div className="pf-stat"><b>${fmtCompact(p.followers_count)}</b><span>Người theo dõi</span></div>
            <div className="pf-stat"><b>${fmtCompact(p.total_likes)}</b><span>Lượt thích</span></div>
          </div>
          ${p.signature ? html`<div className="pf-bio">${p.signature}</div>` : null}
        </div>
        <div className="pf-side">
          <a className="btn-ghost" href=${url} target="_blank">Mở TikTok ↗</a>
          <div className="pf-meta">${duration ? "quét " + duration + " · " : ""}${fmtNum(videoCount || 0)} video</div>
        </div>
      </div>
    </div>
  </section>`;
}

/* --------------------------------------------------------------- overview */
function Overview({ videos, total, profile }) {
  const n = videos.length;
  const sum = (k) => videos.reduce((a, v) => a + (v[k] || 0), 0);
  const totViews = sum("views");
  const avgViews = n ? totViews / n : 0;
  const avgER = n ? videos.reduce((a, v) => a + er(v), 0) / n : 0;
  // avg videos/week — span between earliest and latest post
  const times = videos.map((v) => new Date(v.posted_at).getTime()).filter((t) => !isNaN(t));
  const weeks = times.length > 1 ? Math.max(1, (Math.max(...times) - Math.min(...times)) / (7 * 864e5)) : 1;
  const perWeek = n / weeks;
  return html`<section>
    <div className="cards">
      <${Metric} k="Tổng lượt xem" val=${fmtCompact(totViews)} sub=${fmtNum(totViews)}/>
      <${Metric} k="Tỷ lệ tương tác TB" val=${avgER.toFixed(2) + "%"} sub="(like+cmt+share)/view" accent=${true}/>
      <${Metric} k="View TB / video" val=${fmtCompact(avgViews)} sub=${"trên " + fmtNum(n) + " video"}/>
      <${Metric} k="Tổng số video" val=${fmtNum(n)} sub=${n !== total ? "trên " + fmtNum(total) : "đã thu thập"}/>
      <${Metric} k="Trung bình video/tuần" val=${perWeek.toFixed(1)} sub="theo lịch đăng"/>
    </div>
  </section>`;
}

/* ----------------------------------------------------------------- charts */
function Charts({ videos, chart, onPick }) {
  const sel = chart || {};
  const byMonth = {};
  videos.forEach((v) => {
    const m = (v.posted_at || "").slice(0, 7);
    if (!m) return;
    (byMonth[m] = byMonth[m] || { views: 0, count: 0, erSum: 0 });
    byMonth[m].views += v.views || 0; byMonth[m].count += 1; byMonth[m].erSum += er(v);
  });
  // Continuous month axis: fill every month between first and last post,
  // including empty ones, so gaps in the posting schedule are visible.
  const keys = Object.keys(byMonth).sort();
  const months = [];
  if (keys.length) {
    let [y, mo] = keys[0].split("-").map(Number);
    const [ly, lm] = keys[keys.length - 1].split("-").map(Number);
    while ((y < ly || (y === ly && mo <= lm)) && months.length < 240) {
      months.push(y + "-" + String(mo).padStart(2, "0"));
      if (++mo > 12) { mo = 1; y++; }
    }
  }
  const cell = (m) => byMonth[m] || { views: 0, count: 0, erSum: 0 };
  const maxV = Math.max(1, ...months.map((m) => cell(m).views));
  const erOfMonth = (m) => { const o = cell(m); return o.count ? o.erSum / o.count : 0; };
  const maxER = Math.max(...months.map(erOfMonth), 0.0001);
  // ER curve points over the bar area (viewBox 0..100), smoothed for readability
  const erPath = smoothPath(months.map((m, i) =>
    [+((i + 0.5) / months.length * 100).toFixed(2), +((1 - erOfMonth(m) / maxER) * 100).toFixed(2)]
  ));

  const wd = [[], [], [], [], [], [], []];
  videos.forEach((v) => { const i = weekdayIdx(v.posted_at); if (i >= 0) wd[i].push(v.views || 0); });
  const avgs = wd.map((a) => (a.length ? a.reduce((x, y) => x + y, 0) / a.length : 0));
  const maxA = Math.max(1, ...avgs);

  const pick = (type, value, label) => onPick(
    sel.type === type && sel.value === value ? null : { type, value, label }
  );

  return html`<section>
    <${SecTitle}>Biểu đồ <span className="chart-hint">(bấm vào cột / hashtag để xem danh sách video)</span><//>
    <div className="charts">
      <div className="chart">
        <div className="chart-title">Lượt xem theo tháng <span className="chart-hint">· <span style=${{ color: "var(--blue)" }}>━</span> tỉ lệ tương tác</span></div>
        <div className="plot">
          <div className="vbars noscroll">
            ${months.length ? months.map((m) => {
              const o = cell(m), h = o.views / maxV * 100, parts = m.split("-");
              const on = sel.type === "month" && sel.value === m;
              const peak = o.views === maxV && maxV > 0;
              return html`<div className=${"vbar" + (on ? " sel" : "")} key=${m}
                  title=${m + ": " + fmtNum(o.views) + " views · " + o.count + " video · ER " + erOfMonth(m).toFixed(1) + "%"}
                  onClick=${() => pick("month", m, "tháng " + parts[1] + "/" + parts[0])}>
                <div className="fill" style=${{ height: h + "%", background: peak ? "var(--pink2)" : null }}></div>
                <div className="lbl">${parts[1] + "/" + parts[0].slice(2)}</div>
              </div>`;
            }) : html`<div className="empty">—</div>`}
          </div>
          ${months.length > 1 ? html`<svg className="erline" viewBox="0 0 100 100" preserveAspectRatio="none">
            <path d=${erPath} fill="none" stroke="var(--blue)" strokeWidth="1.6"
              vectorEffect="non-scaling-stroke" strokeLinejoin="round" strokeLinecap="round"/>
          </svg>` : null}
        </div>
      </div>
      <div className="chart">
        <div className="chart-title">View trung bình theo thứ (ngày đăng)</div>
        <div className="vbars">
          ${avgs.map((a, i) => {
            const on = sel.type === "weekday" && sel.value === i;
            const peak = a === maxA && maxA > 0;
            return html`<div className=${"vbar" + (on ? " sel" : "")} key=${i}
                title=${WD_NAMES[i] + ": " + fmtNum(a) + " views TB • " + wd[i].length + " video"}
                onClick=${() => pick("weekday", i, "đăng " + WD_NAMES[i])}>
              <div className="fill" style=${{ height: a / maxA * 100 + "%", background: peak ? "var(--pink2)" : null }}></div>
              <div className="lbl">${WD_NAMES[i]}</div>
            </div>`;
          })}
        </div>
      </div>
    </div>
  </section>`;
}

/* ----------------------------------------------------------- hashtag cloud */
/* full hashtag inventory (not just the top-8 chart). Clicking reuses the same
 * accChart drill-down as the bar chart, so it filters the video list below. */
function HashtagCloud({ videos, chart, onPick }) {
  const tags = useMemo(() => {
    const m = {};
    videos.forEach((v) => (v.hashtags || []).forEach((t) => {
      const k = String(t).toLowerCase(); if (k) m[k] = (m[k] || 0) + 1;
    }));
    return Object.entries(m).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
      .map(([k, c]) => ({ key: k, label: "#" + k, count: c }));
  }, [videos]);
  if (!tags.length) return null;
  const sel = chart && chart.type === "hashtag" ? chart.value : null;
  return html`<section>
    <${SecTitle}>Danh sách hashtag <span className="chart-hint">(${tags.length} hashtag • bấm để lọc video)</span><//>
    <div className="panel">
      <${Cloud} items=${tags} accent="kw" selected=${sel}
        onPick=${(it) => onPick(sel === it.key ? null : { type: "hashtag", value: it.key, label: "#" + it.key })}/>
    </div>
  </section>`;
}

/* ------------------------------------------------------- transcript viewer */
function TranscriptViewer({ transcripts, inline }) {
  const [idx, setIdx] = useState(0);
  const [times, setTimes] = useState(false);
  const t = transcripts[idx] || transcripts[0];
  if (!t) return null;
  const copy = async () => { try { await navigator.clipboard.writeText(segText(t, times)); } catch (_) {} };
  return html`<div className=${inline ? "trbox" : "panel"} style=${inline ? null : { marginTop: 14 }}>
    <div style=${{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap", marginBottom: 8 }}>
      <label className="vsub">Ngôn ngữ:</label>
      <select className="tselect" value=${idx} onChange=${(e) => setIdx(+e.target.value)}>
        ${transcripts.map((x, i) => html`<option key=${i} value=${i}>
          ${x.language_name}${x.is_original ? " (gốc)" : ""} · ${x.source}</option>`)}
      </select>
      <button className="ghost small" onClick=${copy}>Sao chép</button>
      <button className="ghost small" onClick=${() => download((t.language || "transcript") + ".txt", segText(t, times), "text/plain;charset=utf-8")}>Tải .txt</button>
      <button className="ghost small" onClick=${() => download((t.language || "transcript") + ".srt", segSrt(t), "text/plain;charset=utf-8")}>Tải .srt</button>
      <label className="check" style=${{ fontSize: 13 }}>
        <input type="checkbox" checked=${times} onChange=${(e) => setTimes(e.target.checked)}/> Hiện mốc thời gian
      </label>
    </div>
    <pre className=${inline ? "trinline" : "transcript"}>${segText(t, times)}</pre>
  </div>`;
}

/* ------------------------------------------------------------- video table */
function VideoTable({ rows, sortKey, sortDir, onSort, trMap, onTranscribe }) {
  const [exp, setExp] = useState({});
  const cols = [
    ["i", "#", "nosort"], ["description", "Mô tả", ""], ["views", "Views", "num"],
    ["likes", "Likes", "num"], ["comments", "Bình luận", "num"], ["shares", "Chia sẻ", "num"],
    ["saves", "Lưu", "num"], ["er", "Tương tác", "num"], ["posted_at", "Ngày đăng", ""],
  ];
  const sorted = useMemo(() => rows.slice().sort((a, b) => {
    let x, y;
    if (sortKey === "er") { x = er(a); y = er(b); }
    else { x = a[sortKey]; y = b[sortKey]; }
    if (typeof x === "string") return (x || "").localeCompare(y || "") * sortDir;
    return ((x || 0) - (y || 0)) * sortDir;
  }), [rows, sortKey, sortDir]);

  return html`<div className="tablewrap">
    <table>
      <thead><tr>
        ${cols.map(([k, label, cls]) => html`<th key=${k}
            className=${(cls.includes("num") ? "num " : "") + (k === "i" || cls.includes("nosort") ? "nosort" : "")}
            onClick=${k === "i" ? null : () => onSort(k)}>
          ${label}${sortKey === k ? html`<span className="arrow">${sortDir < 0 ? " ▼" : " ▲"}</span>` : null}
        </th>`)}
        <th className="nosort">Link</th>
        <th className="nosort">Transcript</th>
      </tr></thead>
      <tbody>
        ${!sorted.length ? html`<tr><td colSpan="11" className="empty">Không có video nào khớp.</td></tr>`
          : sorted.map((v, i) => {
            const e = er(v);
            const tr = trMap[v.video_id];
            const open = exp[v.video_id];
            const tags = (v.hashtags || []).slice(0, 6);
            return html`<${React.Fragment} key=${v.video_id || i}>
              <tr>
                <td className="idx">${i + 1}</td>
                <td className="desc">
                  <div className="vrow">
                    <a className="vthumb" href=${v.video_url} target="_blank" title="Mở video">
                      <svg viewBox="0 0 24 24" fill="currentColor"><path d="M9 7.5l8 4.5-8 4.5z"/></svg>
                    </a>
                    <div className="vrowtxt">
                      ${v.description || html`<span style=${{ color: "var(--muted2)" }}>(không mô tả)</span>`}
                      ${tags.length ? html`<div className="tags">${tags.map((t, j) => html`<span className="tag" key=${j}>#${t}</span>`)}</div>` : null}
                      ${tr && tr.state === "done" && tr.preview ? html`<div className="trprev" title=${tr.preview}>📝 ${tr.preview.slice(0, 140)}${tr.preview.length > 140 ? "…" : ""}</div>` : null}
                    </div>
                  </div>
                </td>
                <td className="num">${fmtNum(v.views)}</td>
                <td className="num">${fmtNum(v.likes)}</td>
                <td className="num">${fmtNum(v.comments)}</td>
                <td className="num">${fmtNum(v.shares)}</td>
                <td className="num">${fmtNum(v.saves)}</td>
                <td className="num"><span className="er" style=${{ color: erColor(e) }}>${e.toFixed(1)}%</span></td>
                <td>${fmtDate(v.posted_at)}</td>
                <td><a className="open" href=${v.video_url} target="_blank">mở ↗</a></td>
                <td>
                  <div className="rowbtns">
                    ${(!tr || tr.state === "idle") ? html`<button className="ghost small" onClick=${() => onTranscribe(v)}>Lấy</button>` : null}
                    ${tr && tr.state === "loading" ? html`<button className="ghost small" disabled><${Spinner}/></button>` : null}
                    ${tr && tr.state === "done" ? html`<button className="ghost small" onClick=${() => setExp((s) => ({ ...s, [v.video_id]: !s[v.video_id] }))}>${open ? "Ẩn" : "Xem"}</button>` : null}
                    ${tr && tr.state === "none" ? html`<span className="vsub" style=${{ color: "var(--muted2)" }}>không có</span>` : null}
                    ${tr && tr.state === "error" ? html`<button className="ghost small" onClick=${() => onTranscribe(v)}>Thử lại</button>` : null}
                  </div>
                </td>
              </tr>
              ${tr && tr.state === "done" && open ? html`<tr><td colSpan="11" style=${{ background: "var(--card2)" }}>
                <div className="vsub" style=${{ marginBottom: 4 }}>${tr.method === "whisper" ? "Tự trích xuất (Whisper)" : "Phụ đề TikTok"}</div>
                <${TranscriptViewer} transcripts=${tr.transcripts} inline=${true}/>
              </td></tr>` : null}
            <//>`;
          })}
      </tbody>
    </table>
  </div>`;
}

/* keyword cloud built from comment text — top 50 frequent words. */
function KeywordCloud({ comments, selected, onPick }) {
  const words = useMemo(() => {
    const m = {};
    const tally = (txt) => tokenize(txt).forEach((w) => { m[w] = (m[w] || 0) + 1; });
    comments.forEach((c) => { tally(c.text); (c.reply_list || []).forEach((r) => tally(r.text)); });
    let e = Object.entries(m).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
    const multi = e.filter(([, c]) => c > 1);
    e = (multi.length >= 8 ? multi : e).slice(0, 50);
    return e.map(([k, c]) => ({ key: k, label: k, count: c }));
  }, [comments]);
  if (!words.length) return null;
  return html`<div className="panel" style=${{ marginBottom: 14 }}>
    <${SecTitle} style=${{ marginBottom: 10 }}>Từ khoá nổi bật trong bình luận
      <span className="chart-hint">(bấm để lọc)</span><//>
    <${Cloud} items=${words} accent="kw" selected=${selected} onPick=${(it) => onPick(it.key)}/>
  </div>`;
}

/* ------------------------------------------------------------- comments */
function CommentsTable({ comments }) {
  const [q, setQ] = useState("");
  const [exp, setExp] = useState({});
  const rows = !q ? comments : comments.filter((c) =>
    (c.text || "").toLowerCase().includes(q.toLowerCase()) ||
    (c.user || "").toLowerCase().includes(q.toLowerCase()) ||
    (c.nickname || "").toLowerCase().includes(q.toLowerCase()));
  const kwSel = q.trim().toLowerCase();
  const pickKw = (w) => setQ((cur) => (cur.trim().toLowerCase() === w ? "" : w));
  // exp = set of COLLAPSED comment ids; default {} → replies expanded.
  const toggle = (id) => setExp((s) => ({ ...s, [id]: !s[id] }));
  const withReplies = comments.filter((c) => (c.reply_list || []).length);
  const someOpen = withReplies.some((c) => !exp[c.cid]);
  const collapseAll = () => setExp(Object.fromEntries(withReplies.map((c) => [c.cid, true])));
  const expandAll = () => setExp({});
  const userCell = (u, nick) => html`<td><a className="open" href=${"https://www.tiktok.com/@" + u} target="_blank">@${u}</a>
    <div style=${{ color: "var(--muted2)", fontSize: "11.5px" }}>${nick}</div></td>`;
  return html`<section>
    <${KeywordCloud} comments=${comments} selected=${kwSel} onPick=${pickKw}/>
    <div className="toolbar">
      <div className="left">
        <${SecTitle} style=${{ margin: 0 }}>Bình luận<//>
        <span className="count-pill"><b>${fmtNum(rows.length)}</b> bình luận</span>
      </div>
      <div className="left">
        <div className="filter"><span className="ic">⌕</span>
          <input type="text" value=${q} onChange=${(e) => setQ(e.target.value)} placeholder="Lọc bình luận / người dùng…"/></div>
        ${withReplies.length ? html`<button className="ghost" onClick=${someOpen ? collapseAll : expandAll}>
          ${someOpen ? "Thu gọn trả lời" : "Mở tất cả trả lời"}</button>` : null}
        <button className="ghost" onClick=${() => download("binh_luan.json", JSON.stringify(rows, null, 2), "application/json")}>Tải JSON</button>
        <button className="ghost" onClick=${() => download("binh_luan.csv", "﻿" + csvOf(rows, ["cid", "user", "nickname", "text", "likes", "replies", "created_at"]), "text/csv;charset=utf-8")}>Tải CSV</button>
      </div>
    </div>
    <div className="tablewrap">
      <table>
        <thead><tr><th className="nosort">#</th><th className="nosort">Người dùng</th>
          <th className="nosort">Bình luận</th><th className="num nosort">Likes</th>
          <th className="num nosort">Trả lời</th><th className="nosort">Ngày</th></tr></thead>
        <tbody>
          ${rows.length ? rows.flatMap((c, i) => {
            const reps = c.reply_list || [];
            const open = !exp[c.cid];
            const main = html`<tr key=${c.cid || i}>
              <td className="idx">${i + 1}</td>
              ${userCell(c.user, c.nickname)}
              <td className="ctext">${c.text}
                ${reps.length ? html`<div className="reply-toggle" onClick=${() => toggle(c.cid)}>
                  ${open ? "▾" : "▸"} ${open ? "Ẩn" : "Xem"} ${fmtNum(reps.length)} trả lời</div>` : null}</td>
              <td className="num">${fmtNum(c.likes)}</td>
              <td className="num">${fmtNum(c.replies)}</td>
              <td>${fmtDate(c.created_at)}</td>
            </tr>`;
            if (!open || !reps.length) return [main];
            const replyRows = reps.map((r, j) => html`<tr className="replyrow" key=${(c.cid || i) + "-" + (r.cid || j)}>
              <td className="idx"></td>
              <td className="reply-user"><span className="arr">↳</span><a className="open"
                href=${"https://www.tiktok.com/@" + r.user} target="_blank">@${r.user}</a>
                <div style=${{ color: "var(--muted2)", fontSize: "11.5px", paddingLeft: 20 }}>${r.nickname}</div></td>
              <td className="ctext">${r.text}</td>
              <td className="num">${fmtNum(r.likes)}</td>
              <td className="num"></td>
              <td>${fmtDate(r.created_at)}</td>
            </tr>`);
            return [main, ...replyRows];
          }) : html`<tr><td colSpan="6" className="empty">Không có bình luận nào khớp.</td></tr>`}
        </tbody>
      </table>
    </div>
  </section>`;
}

/* =====================================================  ACCOUNT MODE  ===== */
function AccountMode({ st, tabs }) {
  const {
    accUser, setAccUser, accWithVideos, setAccWithVideos, accAutoTr, setAccAutoTr,
    accFrom, setAccFrom, accTo, setAccTo, accVideos, accProfile, accDuration,
    accFilter, setAccFilter, accSortKey, accSortDir, setSort,
    accChart, setAccChart, accTr, runAccount, transcribeOne, transcribeAll, stopBulk, busy, accTrBusy,
  } = st;

  // The date range (from/to) doubles as a client-side filter on already-scraped
  // videos AND, on the next "Phân tích", drives the backend early-stop scrape.
  const base = useMemo(() => accVideos.filter((v) => {
    const dt = (v.posted_at || "").slice(0, 10);
    if (accFrom && (!dt || dt < accFrom)) return false;
    if (accTo && (!dt || dt > accTo)) return false;
    if (accFilter) {
      const q = accFilter.toLowerCase();
      if (!(v.description || "").toLowerCase().includes(q) &&
          !(v.hashtags || []).join(" ").toLowerCase().includes(q)) return false;
    }
    return true;
  }), [accVideos, accFrom, accTo, accFilter]);

  const drill = useMemo(() => {
    if (!accChart) return null;
    return base.filter((v) => {
      if (accChart.type === "month") return (v.posted_at || "").slice(0, 7) === accChart.value;
      if (accChart.type === "weekday") return weekdayIdx(v.posted_at) === accChart.value;
      if (accChart.type === "hashtag") return (v.hashtags || []).map((t) => t.toLowerCase()).includes(accChart.value);
      return true;
    });
  }, [base, accChart]);

  const fileName = (ext) => {
    const suffix = (accFrom || accTo) ? "_" + (accFrom || "start") + "_den_" + (accTo || "now") : "";
    return (accUser || "videos") + "_videos" + suffix + "." + ext;
  };

  return html`<div>
    <div className="panel">
    ${tabs}
    <div id="singleInputs">
      <div className="searchrow">
        <div className="field" style=${{ flex: "0 1 440px" }}><span className="at">@</span>
          <input type="text" value=${accUser} onChange=${(e) => setAccUser(e.target.value)}
            onKeyDown=${(e) => e.key === "Enter" && runAccount()}
            placeholder="vd: trungvu.ttv" autoComplete="off" spellCheck="false"/></div>
        <button onClick=${runAccount} disabled=${busy}>Phân tích</button>
      </div>
      <div className="opts">
        <label className="check"><input type="checkbox" checked=${accWithVideos}
          onChange=${(e) => setAccWithVideos(e.target.checked)}/>
          Lấy luôn toàn bộ video <span style=${{ color: "var(--muted2)" }}>(chậm hơn ~20–40s)</span></label>
        <label className="check"><input type="checkbox" checked=${accAutoTr}
          onChange=${(e) => setAccAutoTr(e.target.checked)}/>
          Tự lấy transcript mọi video sau khi quét <span style=${{ color: "var(--muted2)" }}>(rất chậm — nên lọc Giai đoạn)</span></label>
        <div className="daterange"><span>Giai đoạn:</span>
          <input type="date" value=${accFrom} onChange=${(e) => setAccFrom(e.target.value)} title="Từ ngày"/>
          <span>→</span>
          <input type="date" value=${accTo} onChange=${(e) => setAccTo(e.target.value)} title="Đến ngày"/>
          <button className="ghost small" onClick=${() => { setAccFrom(""); setAccTo(""); }}>Đặt lại</button>
        </div>
      </div>
      <div className="note">
        Chọn <b>Giai đoạn</b> (từ → đến) rồi bấm <b>Phân tích</b> để chỉ lấy đúng khoảng đó —
        nhẹ và nhanh hơn nhiều (bỏ qua, dừng sớm khi vượt mốc). Để trống = lấy tất cả.
        Mỗi video có nút <b>Transcript</b> riêng; hoặc tick ô tự lấy transcript hàng loạt.
      </div>
    </div>
    <${Status} s=${st.status}/>
    </div>

    <${ProfileCard} p=${accProfile} duration=${accDuration} videoCount=${accVideos.length}/>

    ${accVideos.length ? html`<div>
      <${Overview} videos=${base} total=${accVideos.length} profile=${accProfile}/>
      <${Charts} videos=${base} chart=${accChart} onPick=${setAccChart}/>
      <${HashtagCloud} videos=${base} chart=${accChart} onPick=${setAccChart}/>

      ${drill ? html`<${Modal} title=${"Video " + accChart.label} badge=${drill.length}
          onClose=${() => setAccChart(null)}>
        <${VideoTable} rows=${drill} sortKey=${accSortKey} sortDir=${accSortDir}
          onSort=${setSort} trMap=${accTr} onTranscribe=${transcribeOne}/>
      <//>` : null}

      <section>
        <div className="toolbar">
          <div className="left">
            <${SecTitle} style=${{ margin: 0 }}>Danh sách video<//>
            <span className="count-pill"><b>${fmtNum(base.length)}</b> video${base.length !== accVideos.length ? " / " + fmtNum(accVideos.length) : ""}</span>
            ${accDuration ? html`<span style=${{ color: "var(--muted2)", fontSize: "12.5px" }}>• quét trong ${accDuration}</span>` : null}
          </div>
          <div className="left">
            <div className="filter"><span className="ic">⌕</span>
              <input type="text" value=${accFilter} onChange=${(e) => setAccFilter(e.target.value)} placeholder="Tìm mô tả hoặc #hashtag…"/></div>
            <div className="daterange">
              <input type="date" value=${accFrom} onChange=${(e) => setAccFrom(e.target.value)} title="Lọc từ ngày"/>
              <span>→</span>
              <input type="date" value=${accTo} onChange=${(e) => setAccTo(e.target.value)} title="Lọc đến ngày"/>
              ${(accFrom || accTo) ? html`<button className="ghost small" onClick=${() => { setAccFrom(""); setAccTo(""); }}>Xoá</button>` : null}
            </div>
            ${accTrBusy
              ? html`<button className="ghost" onClick=${stopBulk}>Dừng lấy transcript</button>`
              : html`<button className="ghost" onClick=${() => transcribeAll(base)}>Lấy transcript tất cả</button>`}
            <button className="ghost" onClick=${() => download(fileName("json"), JSON.stringify(base.map((v) => ({ ...v, transcript: (accTr[v.video_id] || {}).preview || "" })), null, 2), "application/json")}>Tải JSON</button>
            <button className="ghost" onClick=${() => download(fileName("csv"), "﻿" + csvOf(base.map((v) => ({ ...v, transcript: (accTr[v.video_id] || {}).preview || "" })), ["video_id", "description", "views", "likes", "comments", "shares", "saves", "posted_at", "hashtags", "video_url", "transcript"]), "text/csv;charset=utf-8")}>Tải CSV</button>
          </div>
        </div>
        <${VideoTable} rows=${base} sortKey=${accSortKey} sortDir=${accSortDir}
          onSort=${setSort} trMap=${accTr} onTranscribe=${transcribeOne}/>
      </section>
    </div>` : null}
  </div>`;
}

/* =====================================================  VIDEO MODE  ======= */
function VideoMode({ st, tabs }) {
  const { vidUrl, setVidUrl, vidWithTr, setVidWithTr, vidVideo, vidComments,
    vidCommentTotal, vidTr, runVideo, busy } = st;
  const v = vidVideo;
  const eRate = v && v.views ? (v.likes + v.comments + v.shares + v.saves) / v.views * 100 : 0;
  return html`<div>
    <div className="panel">
    ${tabs}
    <div className="searchrow">
      <div className="field"><input type="text" className="nopad" value=${vidUrl}
        onChange=${(e) => setVidUrl(e.target.value)} onKeyDown=${(e) => e.key === "Enter" && runVideo()}
        placeholder="vd: https://www.tiktok.com/@user/video/123..." autoComplete="off" spellCheck="false"/></div>
      <button onClick=${runVideo} disabled=${busy}>Lấy dữ liệu</button>
    </div>
    <div className="opts">
      <label className="check"><input type="checkbox" checked=${vidWithTr}
        onChange=${(e) => setVidWithTr(e.target.checked)}/>
        Lấy luôn transcript của video <span style=${{ color: "var(--muted2)" }}>(thêm ~10–60s)</span></label>
    </div>
    <div className="note">Dán <b>link 1 video</b> để lấy đầy đủ chỉ số (view, like, share, lưu…) và bình luận
      <span style=${{ color: "var(--muted2)" }}>(tối đa 500 bình luận nhiều like nhất).</span></div>
    <${Status} s=${st.status}/>
    </div>

    ${v ? html`<div>
      <section>
        <${SecTitle}>Thông tin video<//>
        <div className="panel"><div className="vhead">
          ${v.cover ? html`<img className="vcover" src=${v.cover} referrerPolicy="no-referrer"/>` : null}
          <div className="vmeta">
            <div className="vdesc">${v.description || "(không mô tả)"}</div>
            <div className="vsub">👤 <b>${v.author_nickname || v.author}</b> @${v.author}${v.author_verified ? " ✓" : ""}</div>
            <div className="vsub">🎵 ${v.music_title || "—"}${v.music_author ? " · " + v.music_author : ""}</div>
            <div className="vsub">⏱ ${v.duration ? Math.floor(v.duration / 60) + ":" + String(v.duration % 60).padStart(2, "0") : "—"}   •   📅 ${fmtDate(v.posted_at)}</div>
            <div className="tags">${(v.hashtags || []).map((t, i) => html`<span className="tag" key=${i}>#${t}</span>`)}</div>
            <a className="open" href=${v.video_url || "#"} target="_blank">Mở video ↗</a>
          </div>
        </div></div>
        <${SecTitle} style=${{ marginTop: 22 }}>Hiệu suất<//>
        <div className="cards">
          <${Metric} k="Lượt xem" val=${fmtCompact(v.views)} sub=${fmtNum(v.views)} color="var(--cyan)"/>
          <${Metric} k="Lượt thích" val=${fmtCompact(v.likes)} sub=${fmtNum(v.likes)} color="var(--pink)"/>
          <${Metric} k="Bình luận" val=${fmtCompact(v.comments)} sub=${fmtNum(v.comments)} color="var(--amber)"/>
          <${Metric} k="Chia sẻ" val=${fmtCompact(v.shares)} sub=${fmtNum(v.shares)} color="var(--green)"/>
          <${Metric} k="Lượt lưu" val=${fmtCompact(v.saves)} sub=${fmtNum(v.saves)} color="var(--cyan)"/>
          <${Metric} k="Tương tác" val=${eRate.toFixed(1) + "%"} sub="(like+cmt+share+lưu)/view" color="var(--pink)"/>
        </div>
      </section>

      ${vidTr && vidTr.transcripts ? html`<section>
        <${SecTitle}>Transcript <span className="chart-hint">(${vidTr.method === "whisper" ? "tự trích xuất Whisper" : "phụ đề TikTok"})</span><//>
        <${TranscriptViewer} transcripts=${vidTr.transcripts}/>
      </section>` : null}

      <${CommentsTable} comments=${vidComments}/>
    </div>` : null}
  </div>`;
}

/* =====================================================  TRANSCRIPT MODE  == */
function BulkTable({ results }) {
  const [view, setView] = useState(-1);
  const ok = results.filter((r) => !r.error);
  const toTxt = () => ok.map((r) => {
    const v = r.video || {}, t = (r.transcripts || [])[0] || {};
    return "### @" + (v.author || "") + " — " + (v.description || "").slice(0, 80) + "\n" +
      (v.video_url || r.url) + "\n\n" + (t.text || "");
  }).join("\n\n──────────────────────────\n\n");
  const cur = view >= 0 ? results[view] : null;
  return html`<section>
    <div className="toolbar">
      <div className="left">
        <${SecTitle} style=${{ margin: 0 }}>Kết quả transcript hàng loạt<//>
        <span className="count-pill"><b>${fmtNum(results.length)}</b> video</span>
      </div>
      <div className="left">
        <button className="ghost" onClick=${() => download("transcripts.txt", toTxt(), "text/plain;charset=utf-8")}>Tải tất cả .txt</button>
        <button className="ghost" onClick=${() => download("transcripts.json", JSON.stringify(results, null, 2), "application/json")}>Tải tất cả .json</button>
      </div>
    </div>
    <div className="tablewrap"><table>
      <thead><tr><th className="nosort">#</th><th className="nosort">Video</th><th className="nosort">Nguồn</th>
        <th className="nosort">Ngôn ngữ</th><th className="num nosort">Đoạn</th><th className="nosort"></th></tr></thead>
      <tbody>
        ${results.map((r, i) => {
          if (r.error) return html`<tr key=${i}><td className="idx">${i + 1}</td>
            <td colSpan="5" style=${{ color: "#ff8298" }}>${r.url} — ${r.error}</td></tr>`;
          const v = r.video || {}, t = (r.transcripts || [])[0] || {};
          return html`<tr key=${i}>
            <td className="idx">${i + 1}</td>
            <td className="desc"><a className="open" href=${v.video_url || r.url} target="_blank">@${v.author || ""}</a>
              <div style=${{ color: "var(--muted2)", fontSize: 12 }}>${(v.description || "").slice(0, 70)}</div></td>
            <td>${r.method === "whisper" ? "Whisper" : "TikTok"}</td>
            <td>${t.language_name || ""}${t.is_original ? " (gốc)" : ""}</td>
            <td className="num">${fmtNum(t.segment_count || 0)}</td>
            <td><button className="ghost small" onClick=${() => setView(view === i ? -1 : i)}>${view === i ? "Ẩn" : "Xem"}</button></td>
          </tr>`;
        })}
      </tbody>
    </table></div>
    ${cur && !cur.error ? html`<div style=${{ marginTop: 14 }}>
      <${TranscriptViewer} transcripts=${cur.transcripts}/>
    </div>` : null}
  </section>`;
}

function TranscriptMode({ st, tabs }) {
  const { trSub, setTrSub, trUrl, setTrUrl, trSingle, trUrls, setTrUrls,
    trBulk, runTrSingle, runTrBulk, stopBulk, busy, trBulkBusy } = st;
  return html`<div>
    <div className="panel">
    <div className="card-head" style=${{ marginBottom: 14 }}>
      <div><div className="card-h">Lấy phụ đề / transcript</div>
        <div className="card-sub">${trSub === "single"
          ? "Dán 1 link video — ưu tiên phụ đề có sẵn của TikTok, nếu không có sẽ tự trích bằng Whisper."
          : "Dán nhiều link (mỗi dòng 1, tối đa 50). Xử lý lần lượt, kết quả hiện dần."}</div></div>
      <div className="modes" style=${{ margin: 0 }}>
        <button className=${"trmode" + (trSub === "single" ? " active" : "")} onClick=${() => setTrSub("single")}>1 video</button>
        <button className=${"trmode" + (trSub === "bulk" ? " active" : "")} onClick=${() => setTrSub("bulk")}>Nhiều link</button>
      </div>
    </div>

    ${trSub === "single" ? html`<div>
      <div className="searchrow">
        <div className="field"><input type="text" className="nopad" value=${trUrl}
          onChange=${(e) => setTrUrl(e.target.value)} onKeyDown=${(e) => e.key === "Enter" && runTrSingle()}
          placeholder="vd: https://www.tiktok.com/@user/video/123..." autoComplete="off" spellCheck="false"/></div>
        <button onClick=${runTrSingle} disabled=${busy}>Lấy transcript</button>
      </div>
      <div className="note"><b>Luồng 1:</b> phụ đề tự động của TikTok (nhanh). <b>Luồng 2:</b> nếu video không có phụ đề,
        tự trích xuất từ audio bằng Whisper <span style=${{ color: "var(--muted2)" }}>(~1 phút).</span></div>
    </div>` : html`<div>
      <textarea className="bulkarea" value=${trUrls} onChange=${(e) => setTrUrls(e.target.value)}
        placeholder=${"Mỗi dòng 1 link video:\nhttps://www.tiktok.com/@user/video/111...\nhttps://www.tiktok.com/@user/video/222..."}></textarea>
      <div className="searchrow" style=${{ marginTop: 10 }}>
        ${trBulkBusy ? html`<button className="ghost" onClick=${stopBulk}>Dừng</button>`
          : html`<button onClick=${runTrBulk} disabled=${busy}>Lấy transcript hàng loạt</button>`}
      </div>
      <div className="note">Dán nhiều link (mỗi dòng 1, tối đa <b>50</b>). Xử lý lần lượt, kết quả hiện dần, có thể <b>Dừng</b>.</div>
    </div>`}
    <${Status} s=${st.status}/>
    </div>

    ${trSub === "single" && trSingle && trSingle.transcripts ? html`<section>
      <${SecTitle}>Transcript <span className="chart-hint">(${trSingle.method === "whisper" ? "tự trích xuất Whisper" : "phụ đề TikTok"})</span><//>
      ${trSingle.video ? html`<div className="panel"><div className="vhead">
        ${trSingle.video.cover ? html`<img className="vcover" src=${trSingle.video.cover} referrerPolicy="no-referrer"/>` : null}
        <div className="vmeta">
          <div className="vdesc">${trSingle.video.description || "(không mô tả)"}</div>
          <div className="vsub">👤 <b>${trSingle.video.author_nickname || trSingle.video.author || ""}</b> @${trSingle.video.author || ""}</div>
          <div className="vsub">📅 ${fmtDate(trSingle.video.posted_at)}</div>
        </div></div></div>` : null}
      <${TranscriptViewer} transcripts=${trSingle.transcripts}/>
    </section>` : null}

    ${trSub === "bulk" && trBulk.length ? html`<${BulkTable} results=${trBulk}/>` : null}
  </div>`;
}

/* =====================================================  SOCIAL MODE  ====== */
function PlatBadge({ platform }) {
  const p = PLATFORMS[platform] || { label: platform || "—", color: "var(--muted)" };
  return html`<span className="plat" style=${{ background: p.color }}>${p.label}</span>`;
}

function SocialMode({ st, tabs }) {
  const { socUrl, setSocUrl, socMedia, socComments, runSocial, busy } = st;
  const det = detectPlatform(socUrl);
  const m = socMedia;
  const x = m && m.extra ? m.extra : {};
  return html`<div>
    <div className="panel">
    ${tabs}
    <div className="searchrow">
      <div className="field"><input type="text" className="nopad" value=${socUrl}
        onChange=${(e) => setSocUrl(e.target.value)} onKeyDown=${(e) => e.key === "Enter" && runSocial()}
        placeholder="Dán link YouTube / TikTok / Facebook / Instagram…" autoComplete="off" spellCheck="false"/></div>
      <button onClick=${runSocial} disabled=${busy}>Lấy dữ liệu</button>
    </div>
    <div className="opts">
      <span>Nền tảng:</span>
      ${det ? html`<${PlatBadge} platform=${det}/>`
        : html`<span style=${{ color: "var(--muted2)" }}>tự động nhận diện khi dán link</span>`}
    </div>
    <div className="note">Tự động nhận diện nền tảng và lấy dữ liệu công khai (tiêu đề, tác giả, chỉ số, mô tả).
      <b>TikTok</b> lấy đầy đủ cả bình luận như tab “Video theo link”.
      <span style=${{ color: "var(--muted2)" }}> YouTube / Facebook / Instagram: chỉ thông tin công khai, có thể thiếu vài chỉ số.</span></div>
    <${Status} s=${st.status}/>
    </div>

    ${m ? html`<div>
      <section>
        <${SecTitle}>Thông tin nội dung<//>
        <div className="panel"><div className="vhead">
          ${m.thumbnail ? html`<img className="vcover" src=${m.thumbnail} referrerPolicy="no-referrer"/>` : null}
          <div className="vmeta">
            <div style=${{ marginBottom: 8 }}><${PlatBadge} platform=${m.platform}/></div>
            <div className="vdesc">${m.title || "(không tiêu đề)"}</div>
            ${m.author ? html`<div className="vsub">👤 <b>${m.author}</b>${x.author_handle ? " @" + x.author_handle : ""}${x.verified ? " ✓" : ""}${x.subscribers ? "  •  " + fmtCompact(x.subscribers) + " người đăng ký" : ""}</div>` : null}
            ${(m.duration || m.posted_at) ? html`<div className="vsub">${m.duration ? "⏱ " + fmtSec(m.duration) : ""}${m.duration && m.posted_at ? "   •   " : ""}${m.posted_at ? "📅 " + fmtDate(m.posted_at) : ""}</div>` : null}
            ${x.music_title ? html`<div className="vsub">🎵 ${x.music_title}</div>` : null}
            ${x.category ? html`<div className="vsub">🏷 ${x.category}</div>` : null}
            ${(() => {
              const tg = (x.hashtags && x.hashtags.length) ? x.hashtags.map((t) => "#" + t) : (x.keywords || []);
              return tg.length ? html`<div className="tags">${tg.slice(0, 12).map((t, i) => html`<span className="tag" key=${i}>${t}</span>`)}</div>` : null;
            })()}
            <a className="open" href=${m.url} target="_blank">Mở nội dung ↗</a>
          </div>
        </div></div>

        <${SecTitle} style=${{ marginTop: 22 }}>Chỉ số<//>
        <div className="cards">
          <${Metric} k="Lượt xem" val=${m.views ? fmtCompact(m.views) : "—"} sub=${m.views ? fmtNum(m.views) : "không có"} color="var(--cyan)"/>
          <${Metric} k="Lượt thích" val=${m.likes ? fmtCompact(m.likes) : "—"} sub=${m.likes ? fmtNum(m.likes) : "không có"} color="var(--pink)"/>
          <${Metric} k="Bình luận" val=${m.comments ? fmtCompact(m.comments) : "—"} sub=${m.comments ? fmtNum(m.comments) : "không có"} color="var(--amber)"/>
          <${Metric} k="Chia sẻ" val=${m.shares ? fmtCompact(m.shares) : "—"} sub=${m.shares ? fmtNum(m.shares) : "không có"} color="var(--green)"/>
          ${x.saves ? html`<${Metric} k="Lượt lưu" val=${fmtCompact(x.saves)} sub=${fmtNum(x.saves)} color="var(--cyan)"/>` : null}
        </div>

        ${m.description ? html`<${SecTitle} style=${{ marginTop: 22 }}>Mô tả<//>
          <div className="panel"><pre className="trinline" style=${{ maxHeight: "30vh" }}>${m.description}</pre></div>` : null}
      </section>

      ${socComments && socComments.length ? html`<${CommentsTable} comments=${socComments}/>` : null}
    </div>` : null}
  </div>`;
}

/* =====================================================  LINK MODE  ======== */
/* engagement breakdown — horizontal bars (like / comment / share / save).
 * bar width is relative to the largest part; the pill shows % of total. */
function EngagementBreakdown({ likes, comments, shares, saves }) {
  const parts = [
    { k: "Lượt thích", v: likes || 0, c: "var(--pink2)" },
    { k: "Bình luận", v: comments || 0, c: "var(--blue)" },
    { k: "Chia sẻ", v: shares || 0, c: "var(--green)" },
    { k: "Lưu", v: saves || 0, c: "var(--amber)" },
  ].filter((p) => p.v > 0);
  if (!parts.length) return null;
  const total = parts.reduce((a, p) => a + p.v, 0);
  const max = Math.max(...parts.map((p) => p.v));
  return html`<${SecTitle} style=${{ marginTop: 22 }}>Chi tiết tương tác<//>
    <div className="panel"><div className="breakdown">
      ${parts.map((p) => html`<div className="bd-row" key=${p.k}>
        <div className="bd-name">${p.k}</div>
        <div className="bd-track"><div className="bd-fill" style=${{ width: p.v / max * 100 + "%", background: p.c }}></div></div>
        <div className="bd-val">${fmtCompact(p.v)} · ${(p.v / total * 100).toFixed(0)}%</div>
      </div>`)}
    </div></div>`;
}

/* Unified "paste any link" screen — TikTok video links go through the rich
 * video_detail scraper; everything else uses the multi-platform social scraper. */
function LinkMode({ st }) {
  const { linkUrl, setLinkUrl, linkMedia, linkComments, linkTr, linkTab, setLinkTab,
    runLink, fetchLinkTr, busy, linkTrBusy } = st;
  const det = detectPlatform(linkUrl);
  const m = linkMedia;
  const eRate = m && m.views ? ((m.likes || 0) + (m.comments || 0) + (m.shares || 0) + (m.saves || 0)) / m.views * 100 : 0;
  const nComments = (linkComments || []).length;
  const tabs = [["metrics", "Chỉ số"], ["comments", "Bình luận" + (nComments ? " " + fmtCompact(nComments) : "")], ["transcript", "Transcript"]];
  return html`<div>
    <div className="panel">
      <div className="card-head">
        <div><div className="card-h">Dán link để phân tích</div>
          <div className="card-sub">Hỗ trợ TikTok · YouTube · Facebook · Instagram. TikTok lấy đầy đủ cả bình luận lồng nhau.</div></div>
        <span className="card-hint">Tự nhận diện nền tảng</span>
      </div>
      <div className="searchrow" style=${{ marginTop: 14 }}>
        ${det ? html`<${PlatBadge} platform=${det}/>` : null}
        <div className="field"><input type="text" className="nopad" value=${linkUrl}
          onChange=${(e) => setLinkUrl(e.target.value)} onKeyDown=${(e) => e.key === "Enter" && runLink()}
          placeholder="Dán link YouTube / TikTok / Facebook / Instagram…" autoComplete="off" spellCheck="false"/></div>
        <button onClick=${runLink} disabled=${busy}>▶ Quét</button>
      </div>
      <div className="chips" style=${{ marginTop: 12 }}>
        ${["TikTok video", "YouTube", "Instagram Reel", "Facebook"].map((c) => html`<span className="exchip" key=${c}>${c}</span>`)}
      </div>
      <${Status} s=${st.status}/>
    </div>

    ${m ? html`<div>
      <section><div className="panel">
        <div className="vhead">
          ${m.thumbnail ? html`<img className="vcover" src=${m.thumbnail} referrerPolicy="no-referrer"/>` : null}
          <div className="vmeta">
            <div style=${{ marginBottom: 8 }}><${PlatBadge} platform=${m.platform}/></div>
            <div className="vdesc">${m.title || m.description || "(không tiêu đề)"}</div>
            ${m.author ? html`<div className="vsub">👤 <b>${m.author}</b>${m.author_handle ? " @" + m.author_handle : ""}${m.verified ? " ✓" : ""}${m.subscribers ? "  •  " + fmtCompact(m.subscribers) + " người đăng ký" : ""}</div>` : null}
            ${(m.duration || m.posted_at) ? html`<div className="vsub">${m.duration ? "⏱ " + fmtSec(m.duration) : ""}${m.duration && m.posted_at ? "   •   " : ""}${m.posted_at ? "📅 " + fmtDate(m.posted_at) : ""}</div>` : null}
            ${(m.hashtags && m.hashtags.length) ? html`<div className="tags">${m.hashtags.slice(0, 12).map((t, i) => html`<span className="tag" key=${i}>#${t}</span>`)}</div>` : null}
            <a className="open" href=${m.url} target="_blank">Mở nội dung ↗</a>
          </div>
        </div>

        <div className="modes" style=${{ marginTop: 18, marginBottom: 0 }}>
          ${tabs.map(([id, label]) => html`<button key=${id} className=${"mode" + (linkTab === id ? " active" : "")}
            onClick=${() => setLinkTab(id)}>${label}</button>`)}
        </div>
      </div></section>

      ${linkTab === "metrics" ? html`<section>
        <div className="cards">
          <${Metric} k="Lượt xem" val=${m.views ? fmtCompact(m.views) : "—"} sub=${m.views ? fmtNum(m.views) : "không có"}/>
          <${Metric} k="Lượt thích" val=${m.likes ? fmtCompact(m.likes) : "—"} sub=${m.likes ? fmtNum(m.likes) : "không có"}/>
          <${Metric} k="Bình luận" val=${m.comments ? fmtCompact(m.comments) : "—"} sub=${m.comments ? fmtNum(m.comments) : "không có"}/>
          <${Metric} k="Chia sẻ" val=${m.shares ? fmtCompact(m.shares) : "—"} sub=${m.shares ? fmtNum(m.shares) : "không có"}/>
          ${m.saves ? html`<${Metric} k="Lượt lưu" val=${fmtCompact(m.saves)} sub=${fmtNum(m.saves)}/>` : null}
          ${eRate ? html`<${Metric} k="Tương tác" val=${eRate.toFixed(2) + "%"} sub="trên lượt xem" accent=${true}/>` : null}
        </div>
        <${EngagementBreakdown} likes=${m.likes} comments=${m.comments} shares=${m.shares} saves=${m.saves}/>
        ${m.description ? html`<${SecTitle} style=${{ marginTop: 22 }}>Mô tả<//>
          <div className="panel"><pre className="trinline" style=${{ maxHeight: "30vh" }}>${m.description}</pre></div>` : null}
      </section>` : null}

      ${linkTab === "comments" ? (nComments
        ? html`<${CommentsTable} comments=${linkComments}/>`
        : html`<section><div className="panel"><div className="empty">
            ${m.platform === "tiktok" ? "Video này chưa lấy được bình luận." : "Nền tảng này không cung cấp bình luận công khai."}
          </div></div></section>`) : null}

      ${linkTab === "transcript" ? html`<section>
        ${linkTr && linkTr.transcripts ? html`<div>
          <${SecTitle}>Transcript <span className="chart-hint">(${linkTr.method === "whisper" ? "tự trích xuất Whisper" : "phụ đề TikTok"})</span><//>
          <${TranscriptViewer} transcripts=${linkTr.transcripts}/>
        </div>` : html`<div className="panel"><div style=${{ textAlign: "center", padding: "10px" }}>
          ${m.platform === "tiktok"
            ? html`<div><p className="note" style=${{ marginBottom: 14 }}>Lấy phụ đề TikTok; nếu video không có sẽ tự trích xuất bằng Whisper (~1 phút).</p>
                ${linkTrBusy ? html`<button disabled><${Spinner}/>Đang lấy…</button>` : html`<button onClick=${fetchLinkTr}>Lấy transcript</button>`}</div>`
            : html`<div className="empty">Transcript chỉ hỗ trợ video TikTok.</div>`}
        </div></div>`}
      </section>` : null}
    </div>` : null}
  </div>`;
}

/* ============================================================  SHELL  ===== */
/* inline line-icons (stroke = currentColor) */
const ICON = {
  grid: html`<svg viewBox="0 0 24 24" fill="none" strokeWidth="1.8"><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/></svg>`,
  user: html`<svg viewBox="0 0 24 24" fill="none" strokeWidth="1.8"><circle cx="12" cy="8" r="4"/><path d="M4.5 20c.5-4 4-6 7.5-6s7 2 7.5 6"/></svg>`,
  link: html`<svg viewBox="0 0 24 24" fill="none" strokeWidth="1.8" strokeLinecap="round"><path d="M9 15l6-6M10.5 6.5l1-1a4 4 0 0 1 6 6l-1 1M13.5 17.5l-1 1a4 4 0 0 1-6-6l1-1"/></svg>`,
  text: html`<svg viewBox="0 0 24 24" fill="none" strokeWidth="1.8" strokeLinecap="round"><path d="M4 6h16M4 11h16M4 16h10"/></svg>`,
  history: html`<svg viewBox="0 0 24 24" fill="none" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 4v4h4"/><path d="M12 8v4l3 2"/></svg>`,
};

function Sidebar({ mode, setMode, histCount }) {
  const tools = [
    { id: "overview", label: "Tổng quan", icon: ICON.grid },
    { id: "single", label: "Tài khoản", icon: ICON.user, hint: "@" },
    { id: "link", label: "Link social", icon: ICON.link },
    { id: "transcript", label: "Transcript", icon: ICON.text },
  ];
  const item = (it) => html`<button key=${it.id}
      className=${"nav-item" + (mode === it.id ? " active" : "")} onClick=${() => setMode(it.id)}>
    ${it.icon}<span className="grow">${it.label}</span>
    ${it.badge ? html`<span className="badge">${it.badge}</span>` : null}
    ${it.hint ? html`<span className="hint">${it.hint}</span>` : null}
  </button>`;
  return html`<aside className="sidebar">
    <div className="brand">
      <div className="logo">
        <svg viewBox="0 0 24 24" fill="#fff"><path d="M9 7.5l8 4.5-8 4.5z"/></svg>
        <span className="pip"></span>
      </div>
      <div><div className="bn">TikTok Scraper</div><div className="bs">v2 · workspace</div></div>
    </div>
    <nav className="nav scrl">
      <div className="nav-label">CÔNG CỤ</div>
      ${tools.map(item)}
      <div className="nav-label">DỮ LIỆU</div>
      ${item({ id: "history", label: "Lịch sử", icon: ICON.history, hint: histCount ? String(histCount) : "" })}
    </nav>
    <div className="side-foot">
      <div className="row"><span className="dotlive"></span>Hệ thống sẵn sàng</div>
      <div className="meta">dữ liệu trực tiếp từ tiktok.com</div>
    </div>
  </aside>`;
}

function Topbar({ title, subtitle, onQuick }) {
  return html`<div className="topbar">
    <div><div className="tb-title">${title}</div><div className="tb-sub">${subtitle}</div></div>
    <div className="tb-right">
      <span className="tb-badge"><span className="dotlive"></span>Dữ liệu trực tiếp</span>
      <button className="btn-dark" onClick=${onQuick}>+ Quét nhanh</button>
    </div>
  </div>`;
}

/* small job row — shared by overview "Job gần đây" and history table */
function JobRow({ r, onOpen }) {
  const t = JOB_TYPES[r.type] || { label: r.type, icon: "?" };
  return html`<div className="jobrow" onClick=${() => onOpen(r)}>
    <span className=${"hicon t-" + r.type}>${t.icon}</span>
    <div className="jobrow-main">
      <div className="htarget">${(r.label || r.target || "").slice(0, 64)}</div>
      <div className="hsub">${t.label} · ${fmtAgo(r.ts)}</div>
    </div>
    <span className=${"jobdot " + (r.status === "success" ? "ok" : "err")}></span>
  </div>`;
}

/* overview home — entry cards + last saved account result + recent jobs */
function HomeOverview({ setMode, onOpen, history, profile, videos }) {
  const cards = [
    { id: "single", label: "Phân tích tài khoản", icon: ICON.user, accent: "var(--pink2)", bg: "#fff0f3",
      desc: "Nhập @username — hồ sơ, chỉ số, biểu đồ view, hashtag & toàn bộ video." },
    { id: "link", label: "Quét theo link", icon: ICON.link, accent: "var(--blue)", bg: "#eef3ff",
      desc: "Dán bất kỳ link — tự nhận TikTok / YouTube / FB / IG, lấy chỉ số + bình luận + transcript." },
    { id: "transcript", label: "Lấy transcript", icon: ICON.text, accent: "var(--green)", bg: "#eafaf1",
      desc: "Phụ đề TikTok hoặc Whisper. 1 video hoặc dán hàng loạt tối đa 50 link." },
  ];
  const vids = videos || [];
  const totViews = vids.reduce((a, v) => a + (v.views || 0), 0);
  const avgER = vids.length ? vids.reduce((a, v) => a + er(v), 0) / vids.length : 0;
  const lastAcc = (history || []).find((r) => r.type === "account");
  const name = profile ? (profile.nickname || profile.username) : "";
  const initials = name ? name.trim().split(/\s+/).map((w) => w[0]).slice(0, 2).join("").toUpperCase() : "";
  const recent = (history || []).slice(0, 5);
  return html`<div style=${{ animation: "fadeIn .3s ease both" }}>
    <div className="entry-grid">
      ${cards.map((c) => html`<button key=${c.id} className="entry" onClick=${() => setMode(c.id)}>
        <div className="eic" style=${{ background: c.bg, color: c.accent }}>${c.icon}</div>
        <h3>${c.label}</h3>
        <p>${c.desc}</p>
        <div className="more" style=${{ color: c.accent }}>Mở →</div>
      </button>`)}
    </div>

    <div className="ov-grid">
      <div className="panel">
        <div className="card-head" style=${{ marginBottom: 16 }}>
          <div className="card-h">Kết quả đã lưu gần nhất</div>
          ${profile ? html`<button className="linkbtn" onClick=${() => setMode("single")}>Mở chi tiết →</button>` : null}
        </div>
        ${profile ? html`<div>
          <div className="profile" style=${{ alignItems: "center" }}>
            <div className="pf-av">${initials}</div>
            <div className="pf-main">
              <h2 style=${{ fontSize: 18 }}>${name}</h2>
              <div className="pf-user"><span>@${profile.username}</span>${lastAcc ? html`<span style=${{ color: "var(--muted2)" }}> · lưu ${fmtAgo(lastAcc.ts)}</span>` : null}</div>
            </div>
          </div>
          <div className="cards" style=${{ marginTop: 16, gridTemplateColumns: "repeat(4,1fr)" }}>
            <${Metric} k="Theo dõi" val=${fmtCompact(profile.followers_count)}/>
            <${Metric} k="Tổng view" val=${fmtCompact(totViews)}/>
            <${Metric} k="Video" val=${fmtNum(vids.length || profile.video_count || 0)}/>
            <${Metric} k="ER TB" val=${avgER.toFixed(2) + "%"} accent=${true}/>
          </div>
        </div>` : html`<div className="empty">Chưa có dữ liệu — chạy <b>Phân tích tài khoản</b> để xem ở đây.</div>`}
      </div>

      <div className="panel">
        <div className="card-head" style=${{ marginBottom: 12 }}>
          <div className="card-h">Job gần đây</div>
          <button className="linkbtn" onClick=${() => setMode("history")}>Lịch sử →</button>
        </div>
        ${recent.length ? html`<div className="joblist">
          ${recent.map((r) => html`<${JobRow} key=${r.id} r=${r} onOpen=${onOpen}/>`)}
        </div>` : html`<div className="empty">Chưa có job nào.</div>`}
      </div>
    </div>
  </div>`;
}

/* history screen — filterable job log (metadata-only) */
function HistoryScreen({ history, onOpen }) {
  const [filter, setFilter] = useState("all");
  const counts = useMemo(() => {
    const c = { all: history.length, account: 0, link: 0, social: 0, transcript: 0 };
    history.forEach((r) => { c[r.type] = (c[r.type] || 0) + 1; });
    return c;
  }, [history]);
  if (!history.length) return html`<div className="panel"><div className="empty">
    Chưa có lần thu thập nào. Bắt đầu từ một công cụ ở thanh bên.
  </div></div>`;
  const rows = filter === "all" ? history : history.filter((r) => r.type === filter);
  const tabs = [["all", "Tất cả"], ["account", "Tài khoản"], ["link", "Link"], ["transcript", "Transcript"], ["social", "Social"]];
  return html`<div style=${{ animation: "fadeIn .3s ease both" }}>
    <div className="modes" style=${{ marginBottom: 16 }}>
      ${tabs.map(([id, l]) => html`<button key=${id} className=${"mode" + (filter === id ? " active" : "")}
        onClick=${() => setFilter(id)}>${l} ${counts[id] || 0}</button>`)}
    </div>
    <div className="tablewrap" style=${{ maxHeight: "none" }}><table>
      <thead><tr><th className="nosort">Mục tiêu</th><th className="nosort">Loại</th>
        <th className="num nosort">Bản ghi</th><th className="nosort">Thời gian</th><th className="nosort">Trạng thái</th></tr></thead>
      <tbody>
        ${rows.map((r) => {
          const t = JOB_TYPES[r.type] || { label: r.type, icon: "?" };
          return html`<tr key=${r.id} style=${{ cursor: "pointer" }} onClick=${() => onOpen(r)}>
            <td><div className="hrow"><span className=${"hicon t-" + r.type}>${t.icon}</span>
              <div className="jobrow-main"><div className="htarget">${(r.target || "").slice(0, 70)}</div>
                ${r.label && r.label !== r.target ? html`<div className="hsub">${r.label.slice(0, 70)}</div>` : null}</div></div></td>
            <td>${t.label}</td>
            <td className="num">${fmtNum(r.count)}</td>
            <td className="hsub">${fmtAgo(r.ts)}</td>
            <td>${r.status === "success"
              ? html`<span className="badge-ok">success</span>`
              : html`<span className="badge-err">lỗi</span>`}</td>
          </tr>`;
        })}
      </tbody>
    </table></div>
  </div>`;
}

/* ===========================================================  APP  ======= */
function App() {
  const [mode, setMode] = usePersist("mode", "overview");
  const [status, setStatus] = useState({ text: "", cls: "", busy: false });
  const stopRef = useRef(false);

  // account state
  const [accUser, setAccUser] = usePersist("accUser", "");
  const [accWithVideos, setAccWithVideos] = usePersist("accWithVideos2", false);
  const [accAutoTr, setAccAutoTr] = usePersist("accAutoTr", false);
  const [accFrom, setAccFrom] = usePersist("accFrom", "");
  const [accTo, setAccTo] = usePersist("accTo", "");
  const [accVideos, setAccVideos] = usePersist("accVideos", []);
  const [accProfile, setAccProfile] = usePersist("accProfile", null);
  const [accDuration, setAccDuration] = usePersist("accDuration", "");
  const [accFilter, setAccFilter] = usePersist("accFilter", "");
  const [accSortKey, setAccSortKey] = usePersist("accSortKey", "views");
  const [accSortDir, setAccSortDir] = usePersist("accSortDir", -1);
  const [accChart, setAccChart] = usePersist("accChart", null);
  const [accTr, setAccTr] = usePersist("accTr", {});
  const [accTrBusy, setAccTrBusy] = useState(false);
  const [accBusy, setAccBusy] = useState(false);

  // video state
  const [vidUrl, setVidUrl] = usePersist("vidUrl", "");
  const [vidWithTr, setVidWithTr] = usePersist("vidWithTr", false);
  const [vidVideo, setVidVideo] = usePersist("vidVideo", null);
  const [vidComments, setVidComments] = usePersist("vidComments", []);
  const [vidCommentTotal, setVidCommentTotal] = usePersist("vidCommentTotal", 0);
  const [vidTr, setVidTr] = usePersist("vidTr", null);
  const [vidBusy, setVidBusy] = useState(false);

  // transcript state
  const [trSub, setTrSub] = usePersist("trSub", "single");
  const [trUrl, setTrUrl] = usePersist("trUrl", "");
  const [trSingle, setTrSingle] = usePersist("trSingle", null);
  const [trUrls, setTrUrls] = usePersist("trUrls", "");
  const [trBulk, setTrBulk] = usePersist("trBulk", []);
  const [trBusy, setTrBusy] = useState(false);
  const [trBulkBusy, setTrBulkBusy] = useState(false);

  // social state
  const [socUrl, setSocUrl] = usePersist("socUrl", "");
  const [socMedia, setSocMedia] = usePersist("socMedia", null);
  const [socComments, setSocComments] = usePersist("socComments", []);
  const [socBusy, setSocBusy] = useState(false);

  // link state (merged video + social)
  const [linkUrl, setLinkUrl] = usePersist("linkUrl", "");
  const [linkMedia, setLinkMedia] = usePersist("linkMedia", null);
  const [linkComments, setLinkComments] = usePersist("linkComments", []);
  const [linkTr, setLinkTr] = usePersist("linkTr", null);
  const [linkTab, setLinkTab] = usePersist("linkTab", "metrics");
  const [linkBusy, setLinkBusy] = useState(false);
  const [linkTrBusy, setLinkTrBusy] = useState(false);

  // job history (metadata-only, newest first, capped)
  const [history, setHistory] = usePersist("history", []);
  const pushJob = (rec) => setHistory((h) => [
    { id: Date.now() + "-" + Math.random().toString(36).slice(2, 6), ts: Date.now(), ...rec },
    ...h,
  ].slice(0, 50));

  const setSort = (k) => {
    if (k === accSortKey) setAccSortDir((d) => -d);
    else { setAccSortKey(k); setAccSortDir(-1); }
  };
  const stopBulk = () => { stopRef.current = true; setStatus({ text: "Đang dừng sau khi xong video hiện tại…", cls: "" }); };

  // -- one transcript fetch, normalised --
  async function fetchTr(url) {
    const d = await api("transcript", url);
    if (d.status === "success" && d.has_transcript && (d.transcripts || []).length) {
      return { ok: true, transcripts: d.transcripts, video: d.video || {}, method: d.method };
    }
    return { ok: false, error: d.message || (d.error && d.error.message) || "không có transcript" };
  }

  // -- account --
  async function runAccount() {
    const u = (accUser || "").trim().replace(/^@/, "");
    if (!u) { setStatus({ text: "Hãy nhập username.", cls: "err" }); return; }
    setAccUser(u);
    const hasPeriod = !!(accFrom || accTo);
    const withVideos = accWithVideos || hasPeriod;
    if (hasPeriod) setAccWithVideos(true);
    setAccBusy(true);
    setAccChart(null);
    try {
      if (!withVideos) {
        setStatus({ text: "Đang lấy thông tin tài khoản…", busy: true });
        const p = await api("profile", u);
        if (p.status !== "success" || !p.profile || !p.profile.profile_url)
          throw new Error(p.message || "Không tìm thấy tài khoản.");
        setAccProfile(p.profile); setAccVideos([]);
        setStatus({ text: "Hoàn tất ✓ (chỉ thông tin tài khoản)", cls: "ok" });
        pushJob({ type: "account", target: "@" + u, label: p.profile.nickname || u, count: 1, status: "success" });
      } else {
        setStatus({ text: hasPeriod
          ? "Đang lấy video trong giai đoạn đã chọn… (dừng sớm khi vượt mốc, thường nhanh)"
          : "Đang lấy thông tin & toàn bộ video… (~20–40 giây)", busy: true });
        const d = await api("profile_videos", u, { date_from: accFrom, date_to: accTo });
        if (d.status !== "success" || !d.videos)
          throw new Error(d.message || (d.error && d.error.message) || "Không lấy được video.");
        if (d.profile) setAccProfile(d.profile);
        setAccVideos(d.videos); setAccDuration(d.duration || ""); setAccTr({});
        setStatus({ text: "Hoàn tất ✓ — đã lấy " + fmtNum(d.videos.length) + " video", cls: "ok" });
        pushJob({ type: "account", target: "@" + u, label: (d.profile && d.profile.nickname) || u, count: d.videos.length, status: "success" });
        if (accAutoTr) await transcribeAll(d.videos);
      }
    } catch (e) {
      setStatus({ text: "Thất bại: " + e.message + " — nếu phiên đăng nhập hết hạn, chạy lại file lấy cookie (Run as administrator).", cls: "err" });
      pushJob({ type: "account", target: "@" + u, count: 0, status: "error" });
    } finally { setAccBusy(false); }
  }

  function markTr(id, val) { setAccTr((m) => ({ ...m, [id]: val })); }

  async function transcribeOne(v) {
    const id = v.video_id;
    markTr(id, { state: "loading" });
    try {
      const r = await fetchTr(v.video_url);
      if (r.ok) {
        const t = r.transcripts[0] || {};
        markTr(id, { state: "done", transcripts: r.transcripts, method: r.method, preview: t.text || "" });
        return true;
      }
      markTr(id, { state: "none" });
    } catch (e) { markTr(id, { state: "error" }); }
    return false;
  }

  async function transcribeAll(list) {
    const targets = list.filter((v) => v.video_url);
    if (!targets.length) return;
    if (targets.length > 20 && !confirm(
      "Sắp lấy transcript cho " + targets.length + " video — có thể rất lâu (~" +
      Math.ceil(targets.length * 20 / 60) + " phút trở lên).\nMẹo: lọc Giai đoạn trước.\n\nTiếp tục?")) return;
    stopRef.current = false; setAccTrBusy(true);
    let done = 0, ok = 0;
    for (const v of targets) {
      if (stopRef.current) break;
      done++;
      setStatus({ text: "Đang lấy transcript " + done + "/" + targets.length + " video…", busy: true });
      if (await transcribeOne(v)) ok++;
    }
    setAccTrBusy(false);
    setStatus({ text: (stopRef.current ? "Đã dừng — " : "Hoàn tất ✓ — ") + "lấy transcript " + ok + "/" + targets.length + " video", cls: "ok" });
  }

  // -- video --
  async function runVideo() {
    const url = (vidUrl || "").trim();
    if (!isVideoUrl(url)) { setStatus({ text: "Dán link 1 video TikTok hợp lệ (…/video/123…).", cls: "err" }); return; }
    setVidBusy(true); setVidTr(null);
    setStatus({ text: "Đang lấy dữ liệu video & bình luận… (~30–60 giây)", busy: true });
    try {
      const d = await api("video_detail", url);
      if (d.status !== "success" || !d.video || !d.video.video_id)
        throw new Error(d.message || (d.error && d.error.message) || "Không lấy được dữ liệu video.");
      setVidVideo(d.video); setVidComments(d.comments || []); setVidCommentTotal(d.comment_total || 0);
      setStatus({ text: "Hoàn tất ✓ — " + fmtNum((d.comments || []).length) + " bình luận", cls: "ok" });
      if (vidWithTr) {
        setStatus({ text: "Đã lấy video & bình luận ✓ — đang lấy transcript…", busy: true });
        try {
          const r = await fetchTr(url);
          if (r.ok) {
            setVidTr({ transcripts: r.transcripts, method: r.method });
            setStatus({ text: "Hoàn tất ✓ — video, bình luận và transcript (" + (r.method === "whisper" ? "Whisper" : "phụ đề TikTok") + ")", cls: "ok" });
          } else {
            setStatus({ text: "Hoàn tất ✓ — video & bình luận. Video này không có transcript.", cls: "ok" });
          }
        } catch (e) { setStatus({ text: "Hoàn tất ✓ — video & bình luận. (Transcript lỗi: " + e.message + ")", cls: "ok" }); }
      }
    } catch (e) {
      setStatus({ text: "Thất bại: " + e.message, cls: "err" });
    } finally { setVidBusy(false); }
  }

  // -- transcript single / bulk --
  async function runTrSingle() {
    const url = (trUrl || "").trim();
    if (!isVideoUrl(url)) { setStatus({ text: "Dán link 1 video TikTok hợp lệ (…/video/123…).", cls: "err" }); return; }
    setTrBusy(true); setTrSingle(null);
    setStatus({ text: "Đang lấy transcript… ưu tiên phụ đề TikTok; nếu không có sẽ tự trích xuất bằng Whisper (~1 phút).", busy: true });
    try {
      const r = await fetchTr(url);
      if (!r.ok) throw new Error(r.error);
      setTrSingle({ transcripts: r.transcripts, video: r.video, method: r.method });
      setStatus({ text: "Hoàn tất ✓ — " + (r.method === "whisper" ? "tự trích xuất Whisper" : "phụ đề TikTok") + " · " + r.transcripts.length + " ngôn ngữ", cls: "ok" });
      pushJob({ type: "transcript", target: url, label: "1 video · " + (r.method === "whisper" ? "Whisper" : "phụ đề TikTok"), count: 1, status: "success" });
    } catch (e) {
      setStatus({ text: "Thất bại: " + e.message, cls: "err" });
      pushJob({ type: "transcript", target: url, label: "1 video", count: 0, status: "error" });
    }
    finally { setTrBusy(false); }
  }

  async function runTrBulk() {
    const urls = [...new Set((trUrls || "").split(/[\n,]/).map((s) => s.trim()).filter(isVideoUrl))];
    if (!urls.length) { setStatus({ text: "Dán ít nhất 1 link video hợp lệ (mỗi dòng 1 link).", cls: "err" }); return; }
    if (urls.length > 50) { setStatus({ text: "Tối đa 50 link một lần.", cls: "err" }); return; }
    stopRef.current = false; setTrBulkBusy(true);
    const out = [];
    setTrBulk([]);
    for (let i = 0; i < urls.length; i++) {
      if (stopRef.current) break;
      setStatus({ text: "Đang xử lý " + (i + 1) + "/" + urls.length + "… (có thể Dừng)", busy: true });
      try {
        const r = await fetchTr(urls[i]);
        out.push(r.ok ? { url: urls[i], video: r.video, transcripts: r.transcripts, method: r.method } : { url: urls[i], error: r.error });
      } catch (e) { out.push({ url: urls[i], error: e.message }); }
      setTrBulk(out.slice());
    }
    setTrBulkBusy(false);
    const ok = out.filter((r) => !r.error).length;
    setStatus({ text: (stopRef.current ? "Đã dừng — " : "Hoàn tất ✓ — ") + ok + "/" + out.length + " video có transcript", cls: ok ? "ok" : "err" });
    if (out.length) pushJob({ type: "transcript", target: out.length + " video", label: out.length + " video · batch", count: ok, status: ok ? "success" : "error" });
  }

  // -- social (multi-platform by link) --
  async function runSocial() {
    const url = (socUrl || "").trim();
    if (!/^https?:\/\//i.test(url)) { setStatus({ text: "Dán link hợp lệ (YouTube, TikTok, Facebook, Instagram).", cls: "err" }); return; }
    if (!detectPlatform(url)) { setStatus({ text: "Link không thuộc nền tảng hỗ trợ (YouTube, TikTok, Facebook, Instagram).", cls: "err" }); return; }
    setSocBusy(true); setSocMedia(null); setSocComments([]);
    setStatus({ text: "Đang nhận diện nền tảng & lấy dữ liệu… (TikTok ~30–60s)", busy: true });
    try {
      const d = await api("social", url);
      if (d.status !== "success" || !d.media) throw new Error(d.message || (d.error && d.error.message) || "Không lấy được dữ liệu.");
      setSocMedia(d.media); setSocComments(d.comments || []);
      const pl = (PLATFORMS[d.media.platform] || {}).label || d.media.platform;
      const nc = (d.comments || []).length;
      setStatus({ text: "Hoàn tất ✓ — " + pl + (nc ? " · " + fmtNum(nc) + " bình luận" : ""), cls: "ok" });
    } catch (e) { setStatus({ text: "Thất bại: " + e.message, cls: "err" }); }
    finally { setSocBusy(false); }
  }

  // -- link (merged): TikTok video → rich video_detail; else → social --
  async function runLink() {
    const url = (linkUrl || "").trim();
    if (!/^https?:\/\//i.test(url)) { setStatus({ text: "Dán link hợp lệ (YouTube, TikTok, Facebook, Instagram).", cls: "err" }); return; }
    const plat = detectPlatform(url);
    if (!plat) { setStatus({ text: "Link không thuộc nền tảng hỗ trợ (YouTube, TikTok, Facebook, Instagram).", cls: "err" }); return; }
    setLinkBusy(true); setLinkMedia(null); setLinkComments([]); setLinkTr(null); setLinkTab("metrics");
    const isTikVid = plat === "tiktok" && isVideoUrl(url);
    setStatus({ text: "Đang nhận diện nền tảng & lấy dữ liệu… (" + (isTikVid ? "TikTok video ~30–60s" : "thường nhanh") + ")", busy: true });
    try {
      if (isTikVid) {
        const d = await api("video_detail", url);
        if (d.status !== "success" || !d.video || !d.video.video_id)
          throw new Error(d.message || (d.error && d.error.message) || "Không lấy được dữ liệu video.");
        const v = d.video;
        setLinkMedia({
          platform: "tiktok", title: v.description, description: v.description,
          author: v.author_nickname || v.author, author_handle: v.author, verified: v.author_verified,
          thumbnail: v.cover, duration: v.duration, posted_at: v.posted_at, hashtags: v.hashtags,
          views: v.views, likes: v.likes, comments: v.comments, shares: v.shares, saves: v.saves,
          url: v.video_url || url, video_id: v.video_id,
        });
        setLinkComments(d.comments || []);
        setStatus({ text: "Hoàn tất ✓ — TikTok · " + fmtNum((d.comments || []).length) + " bình luận", cls: "ok" });
        pushJob({ type: "link", target: url, label: v.description || "TikTok video", platform: "tiktok", count: (d.comments || []).length || 1, status: "success" });
      } else {
        const d = await api("social", url);
        if (d.status !== "success" || !d.media) throw new Error(d.message || (d.error && d.error.message) || "Không lấy được dữ liệu.");
        const x = d.media.extra || {};
        setLinkMedia({
          ...d.media, author_handle: x.author_handle, verified: x.verified, subscribers: x.subscribers,
          saves: x.saves, hashtags: (x.hashtags && x.hashtags.length) ? x.hashtags : (x.keywords || []),
        });
        setLinkComments(d.comments || []);
        const pl = (PLATFORMS[d.media.platform] || {}).label || d.media.platform;
        const nc = (d.comments || []).length;
        setStatus({ text: "Hoàn tất ✓ — " + pl + (nc ? " · " + fmtNum(nc) + " bình luận" : ""), cls: "ok" });
        pushJob({ type: "social", target: url, label: (d.media.title || pl), platform: d.media.platform, count: nc || 1, status: "success" });
      }
    } catch (e) {
      setStatus({ text: "Thất bại: " + e.message, cls: "err" });
      pushJob({ type: plat === "tiktok" && isVideoUrl(url) ? "link" : "social", target: url, platform: plat, count: 0, status: "error" });
    }
    finally { setLinkBusy(false); }
  }

  async function fetchLinkTr() {
    const url = (linkUrl || "").trim();
    setLinkTrBusy(true);
    setStatus({ text: "Đang lấy transcript… ưu tiên phụ đề TikTok; nếu không có sẽ tự trích bằng Whisper (~1 phút).", busy: true });
    try {
      const r = await fetchTr(url);
      if (!r.ok) throw new Error(r.error);
      setLinkTr({ transcripts: r.transcripts, method: r.method });
      setStatus({ text: "Hoàn tất ✓ — transcript (" + (r.method === "whisper" ? "Whisper" : "phụ đề TikTok") + ")", cls: "ok" });
    } catch (e) { setStatus({ text: "Transcript thất bại: " + e.message, cls: "err" }); }
    finally { setLinkTrBusy(false); }
  }

  const busyAny = accBusy || vidBusy || trBusy || trBulkBusy || accTrBusy || socBusy || linkBusy || linkTrBusy;
  const accSt = {
    accUser, setAccUser, accWithVideos, setAccWithVideos, accAutoTr, setAccAutoTr,
    accFrom, setAccFrom, accTo, setAccTo, accVideos, accProfile, accDuration,
    accFilter, setAccFilter, accSortKey, accSortDir, setSort,
    accChart, setAccChart, accTr, runAccount, transcribeOne, transcribeAll, stopBulk,
    busy: busyAny, accTrBusy, status,
  };
  const vidSt = { vidUrl, setVidUrl, vidWithTr, setVidWithTr, vidVideo, vidComments, vidCommentTotal, vidTr, runVideo, busy: busyAny, status };
  const trSt = { trSub, setTrSub, trUrl, setTrUrl, trSingle, trUrls, setTrUrls, trBulk, runTrSingle, runTrBulk, stopBulk, busy: busyAny, trBulkBusy, status };
  const socSt = { socUrl, setSocUrl, socMedia, socComments, runSocial, busy: busyAny, status };
  const linkSt = { linkUrl, setLinkUrl, linkMedia, linkComments, linkTr, linkTab, setLinkTab,
    runLink, fetchLinkTr, busy: busyAny, linkTrBusy, status };

  // Map persisted legacy modes onto the new sidebar routes; "link" merges
  // video + social (the actual merge logic lands in a later phase).
  const SCREENS = {
    overview: ["Tổng quan", "Bảng điều khiển thu thập dữ liệu"],
    single: ["Phân tích tài khoản", "Hồ sơ, chỉ số, biểu đồ & toàn bộ video"],
    link: ["Quét theo link", "Tự nhận diện nền tảng · chỉ số + bình luận + transcript"],
    transcript: ["Transcript", "Phụ đề TikTok hoặc Whisper · 1 video hoặc hàng loạt"],
    history: ["Lịch sử", "Tất cả các lần thu thập đã chạy"],
  };
  let m = mode === "video" || mode === "social" ? "link" : mode;
  if (!SCREENS[m]) m = "overview";
  const [title, subtitle] = SCREENS[m];
  const goQuick = () => setMode("single");

  // "Mở chi tiết" from history/overview — restore the input and jump to its tool.
  const openJob = (r) => {
    if (r.type === "account") { setAccUser((r.target || "").replace(/^@/, "")); setMode("single"); }
    else if (r.type === "transcript") { if (/^https?:/.test(r.target)) setTrUrl(r.target); setMode("transcript"); }
    else { setLinkUrl(r.target); setMode("link"); }
  };

  return html`<div className="app">
    <${Sidebar} mode=${m} setMode=${setMode} histCount=${history.length}/>
    <div className="main">
      <${Topbar} title=${title} subtitle=${subtitle} onQuick=${goQuick}/>
      <div className="content"><div className="content-inner">
        ${m === "overview" ? html`<${HomeOverview} setMode=${setMode} onOpen=${openJob}
          history=${history} profile=${accProfile} videos=${accVideos}/>` : null}
        ${m === "single" ? html`<${AccountMode} st=${accSt} tabs=${null}/>` : null}
        ${m === "link" ? html`<${LinkMode} st=${linkSt}/>` : null}
        ${m === "transcript" ? html`<${TranscriptMode} st=${trSt} tabs=${null}/>` : null}
        ${m === "history" ? html`<${HistoryScreen} history=${history} onOpen=${openJob}/>` : null}
      </div></div>
    </div>
  </div>`;
}

ReactDOM.createRoot(document.getElementById("root")).render(html`<${App}/>`);
