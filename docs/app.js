/* Статический дашборд для GitHub Pages: читает data.json и рисует таблицу. */
const $ = (s) => document.querySelector(s);
const fmt = new Intl.NumberFormat("ru-RU");
const NUMERIC = new Set(["views", "likes", "comments", "shares", "saves",
                         "engagement_rate", "duration_sec"]);
const num = (v) => (v === null || v === undefined || v === "" ? 0 : Number(v) || 0);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) =>
  ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));

let allRows = [];
let sortKey = "views";
let sortDir = -1;

async function boot() {
  let data;
  try {
    const res = await fetch("data.json?v=" + Date.now(), {cache: "no-store"});
    if (!res.ok) throw new Error("HTTP " + res.ok);
    data = await res.json();
  } catch (e) {
    $("#updated").textContent = "данных пока нет";
    $("#empty").style.display = "block";
    $("#empty").innerHTML =
      'Не нашёл <code>data.json</code>. Запусти workflow «Сбор данных для Pages» во вкладке Actions.';
    return;
  }

  allRows = data.rows || [];
  $("#updated").textContent = "обновлено: " + (data.updated_at || "—");
  const m = $("#mode");
  if (data.mode === "demo") {
    m.textContent = "ДЕМО-данные";
    m.className = "pill demo";
  } else {
    m.textContent = "теги: " + ((data.params && data.params.tags || []).map(t => "#" + t).join(" "));
    m.className = "pill live";
  }
  renderKpi(data.stats || {});
  render();
}

function renderKpi(s) {
  if (!s.total) {
    $("#kpi").innerHTML = '<div class="card"><b>0</b><span>видео</span></div>' +
      '<div class="card wide"><span>Пусто. TikTok показал капчу или данных ещё нет — попробуйте запустить сбор ещё раз.</span></div>';
    return;
  }
  const cards = [
    ["видео", fmt.format(s.total)],
    ["авторов", fmt.format(s.authors || 0)],
    ["просмотров всего", fmt.format(s.views_total || 0)],
    ["медиана просмотров", fmt.format(s.views_median || 0)],
    ["максимум", fmt.format(s.views_max || 0)],
    ["медиана ER", (s.er_median || 0).toFixed(2) + "%"],
    ["видео с ER ≥ 5%", fmt.format(s.hot_er || 0)],
  ];
  $("#kpi").innerHTML = cards.map(([label, value]) =>
    `<div class="card"><b>${value}</b><span>${label}</span></div>`).join("");
}

function render() {
  const q = $("#search").value.trim().toLowerCase();
  let rows = allRows;
  if (q) {
    rows = rows.filter((r) =>
      [r.tag, r.author, r.nickname, r.description, r.hashtags, r.music]
        .filter(Boolean).join(" ").toLowerCase().includes(q));
  }
  rows = rows.slice().sort((a, b) => {
    let x, y;
    if (NUMERIC.has(sortKey)) { x = num(a[sortKey]); y = num(b[sortKey]); }
    else { x = String(a[sortKey] || ""); y = String(b[sortKey] || ""); }
    return x < y ? -sortDir : x > y ? sortDir : 0;
  });

  document.querySelectorAll("thead th").forEach((th) =>
    th.classList.toggle("sorted", th.dataset.key === sortKey));

  $("#tbody").innerHTML = rows.map((r) => {
    const er = num(r.engagement_rate);
    return `<tr>
      <td><span class="badge">#${esc(r.tag)}</span></td>
      <td><a href="https://www.tiktok.com/@${esc(r.author)}" target="_blank" rel="noopener">@${esc(r.author)}</a></td>
      <td class="num">${fmt.format(num(r.views))}</td>
      <td class="num">${fmt.format(num(r.likes))}</td>
      <td class="num">${fmt.format(num(r.comments))}</td>
      <td class="num">${fmt.format(num(r.shares))}</td>
      <td class="num">${fmt.format(num(r.saves))}</td>
      <td class="num ${er >= 5 ? "er-hot" : ""}">${er ? er.toFixed(2) : "—"}</td>
      <td class="num">${num(r.duration_sec) || "—"}</td>
      <td>${esc((r.created_at || "").slice(0, 16))}</td>
      <td class="desc"><span title="${esc(r.hashtags)}">${esc(r.hashtags)}</span></td>
      <td class="desc"><span title="${esc(r.description)}">${esc(r.description)}</span></td>
      <td><a href="${esc(r.url)}" target="_blank" rel="noopener">открыть</a></td>
    </tr>`;
  }).join("");

  $("#shown").textContent = rows.length === allRows.length
    ? `строк: ${rows.length}`
    : `показано ${rows.length} из ${allRows.length}`;
  $("#empty").style.display = rows.length ? "none" : "block";
}

$("#search").oninput = render;
document.querySelectorAll("thead th[data-key]").forEach((th) => {
  th.onclick = () => {
    const key = th.dataset.key;
    if (sortKey === key) sortDir = -sortDir;
    else { sortKey = key; sortDir = NUMERIC.has(key) ? -1 : 1; }
    render();
  };
});

boot();
