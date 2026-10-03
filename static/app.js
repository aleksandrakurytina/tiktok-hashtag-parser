/* Парсер TikTok — фронтенд: живой опрос /api/status, сортировка, поиск. */
const $ = (s) => document.querySelector(s);

let allRows = [];
let since = 0;
let sortKey = "views";
let sortDir = -1;
let polling = false;
let logLen = 0;
let lastJobId = null;

const NUMERIC = new Set(["views", "likes", "comments", "shares", "saves",
                         "engagement_rate", "duration_sec"]);
const fmt = new Intl.NumberFormat("ru-RU");
const num = (v) => (v === null || v === undefined || v === "" ? 0 : Number(v) || 0);

/* ───────────── управление ───────────── */
$("#start").onclick = async () => {
  const body = {
    tags: $("#tags").value,
    limit: $("#limit").value,
    scrolls: $("#scrolls").value,
    delay: $("#delay").value,
    proxy: $("#proxy").value,
    min_views: $("#min_views").value,
    demo: $("#demo").checked,
    no_details: $("#no_details").checked,
    headless: $("#headless").checked,
    session: $("#session").value,
  };
  const res = await fetch("/api/start", {
    method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body),
  });
  const data = await res.json();
  if (!res.ok) { setState("err", data.error || "ошибка запуска"); return; }

  // новая задача — чистим представление
  allRows = []; since = 0; logLen = 0; lastJobId = data.id;
  $("#log").textContent = "";
  setState("run", "сбор…");
  $("#start").disabled = true;
  $("#stop").disabled = false;
  $("#export").innerHTML = "";
  if (!polling) { polling = true; poll(); }
};

$("#stop").onclick = async () => {
  await fetch("/api/stop", {method: "POST"});
  $("#stop").disabled = true;
};

$("#clear").onclick = () => {
  allRows = []; since = 0;
  render();
  $("#search").value = "";
  setState("idle", "ожидание");
  $("#export").innerHTML = "";
};

/* ───────────── опрос состояния ───────────── */
async function poll() {
  try {
    const res = await fetch("/api/status?since=" + since);
    const data = await res.json();

    if (data.id && data.id !== lastJobId) { allRows = []; since = 0; lastJobId = data.id; }

    if (data.rows && data.rows.length) {
      const minViews = parseInt($("#min_views").value || "0", 10);
      for (const r of data.rows) {
        if (minViews && num(r.views) < minViews) continue;
        allRows.push(r);
      }
      since = data.total_rows;
      render();
    }

    if (data.log && data.log.length !== logLen) {
      logLen = data.log.length;
      const box = $("#log");
      box.textContent = data.log.join("\n");
      box.scrollTop = box.scrollHeight;
    }

    if (data.progress && Object.keys(data.progress).length) {
      const p = data.progress;
      const pct = p.total ? Math.round((p.done / p.total) * 100) : 0;
      $("#bar").style.width = p.done ? pct + "%" : "0%";
      $("#counters").textContent =
        (p.tag ? `тег: #${p.tag} · ` : "") +
        (p.total ? `${p.done}/${p.total} · ` : "") +
        `строк: ${data.total_rows || 0}`;
    }

    if (data.running) {
      setState("run", "сбор…");
    } else {
      if (data.error) setState("err", "ошибка: " + data.error);
      else if (data.total_rows) setState("done", "готово");
      else setState("idle", "ожидание");
      $("#start").disabled = false;
      $("#stop").disabled = true;
      if (data.total_rows) {
        $("#export").innerHTML =
          '<a href="/api/export?format=xlsx">⬇ xlsx</a>' +
          '<a href="/api/export?format=csv">csv</a>';
      }
      polling = false;
      return;
    }
  } catch (e) {
    setState("err", "сервер недоступен");
    $("#start").disabled = false;
    $("#stop").disabled = true;
    polling = false;
    return;
  }
  setTimeout(poll, 800);
}

function setState(cls, text) {
  const el = $("#state");
  el.className = "state " + cls;
  el.textContent = text;
}

/* ───────────── таблица ───────────── */
$("#search").oninput = render;
document.querySelectorAll("thead th[data-key]").forEach((th) => {
  th.onclick = () => {
    const key = th.dataset.key;
    if (sortKey === key) sortDir = -sortDir;
    else { sortKey = key; sortDir = NUMERIC.has(key) ? -1 : 1; }
    render();
  };
});

function render() {
  const q = $("#search").value.trim().toLowerCase();
  let rows = allRows;
  if (q) {
    rows = rows.filter((r) =>
      [r.tag, r.author, r.nickname, r.description, r.hashtags, r.music, r.video_id]
        .filter(Boolean).join(" ").toLowerCase().includes(q));
  }
  rows = rows.slice().sort((a, b) => {
    let x, y;
    if (NUMERIC.has(sortKey)) { x = num(a[sortKey]); y = num(b[sortKey]); }
    else { x = String(a[sortKey] || ""); y = String(b[sortKey] || ""); }
    if (x < y) return -sortDir;
    if (x > y) return sortDir;
    return 0;
  });

  document.querySelectorAll("thead th").forEach((th) => {
    th.classList.toggle("sorted", th.dataset.key === sortKey);
  });

  const wrap = document.querySelector(".scroll");
  const keepScroll = wrap ? wrap.scrollTop : 0;
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) =>
    ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));

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

  if (wrap) wrap.scrollTop = keepScroll;
  $("#empty").style.display = rows.length ? "none" : "block";
  $("#shown").textContent = rows.length === allRows.length
    ? `строк: ${rows.length}`
    : `показано ${rows.length} из ${allRows.length}`;
}

/* ───────────── сессии ───────────── */
async function loadSessions() {
  try {
    const data = await (await fetch("/api/sessions")).json();
    const sel = $("#session");
    const current = sel.value;
    sel.innerHTML = '<option value="">— без сессии —</option>' +
      (data.sessions || []).map((s) => `<option value="${s}">${s}</option>`).join("");
    sel.value = current;
  } catch (e) { /* сервер не поднят — не страшно */ }
}

$("#session_file").onchange = async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  const form = new FormData();
  form.append("file", file);
  await fetch("/api/session", {method: "POST", body: form});
  await loadSessions();
  $("#session").value = file.name;
};

loadSessions();
render();
