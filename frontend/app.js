"use strict";

/* ===== Cấu hình ===== */
const MAX_SIZE = 10 * 1024 * 1024; // 10 MB
const ALLOWED = ["txt", "html", "docx", "pptx", "xlsx"];
const LANGS = {
  auto: "Tự động nhận diện", vi: "Tiếng Việt", en: "Tiếng Anh", ja: "Tiếng Nhật",
  ko: "Tiếng Hàn", zh: "Tiếng Trung", fr: "Tiếng Pháp", de: "Tiếng Đức", es: "Tiếng Tây Ban Nha"
};
const STATUS = {
  done: ["Hoàn tất", "done"], run: ["Đang dịch", "run"],
  wait: ["Chờ dịch", "wait"], fail: ["Lỗi", "fail"]
};

/* ===== Dữ liệu mẫu lịch sử ===== */
const history = [
  { time: "15/11/2026 09:12", type: "Tài liệu", name: "bao-cao-quy-3.docx", src: "vi", tgt: "en", words: 4820, status: "done" },
  { time: "14/11/2026 16:40", type: "Văn bản", name: "Xin chào, tôi muốn đặt phòng cho hai người...", src: "vi", tgt: "ja", words: 36, status: "done" },
  { time: "14/11/2026 10:05", type: "Tài liệu", name: "user-guide.pptx", src: "en", tgt: "vi", words: 2310, status: "done" },
  { time: "12/11/2026 21:30", type: "Tài liệu", name: "hop-dong.html", src: "vi", tgt: "fr", words: 1500, status: "fail" },
  { time: "10/11/2026 08:15", type: "Văn bản", name: "Thank you for your quick response...", src: "en", tgt: "vi", words: 52, status: "done" }
];

/* ===== Tiện ích ===== */
const $ = (s) => document.querySelector(s);
const $$ = (s) => document.querySelectorAll(s);
const countWords = (t) => (t.trim() ? t.trim().split(/\s+/).length : 0);
const fmtNum = (n) => n.toLocaleString("vi-VN");
const fmtSize = (b) => (b < 1024 ? b + " B" : b < 1048576 ? (b / 1024).toFixed(1) + " KB" : (b / 1048576).toFixed(2) + " MB");
const nowStr = () => new Date().toLocaleString("vi-VN", { hour12: false, day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" });
const esc = (s) => s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
let toastTimer;
function toast(msg) {
  const el = $("#toast");
  el.textContent = msg; el.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), 2200);
}
function download(filename, content) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([content], { type: "text/plain;charset=utf-8" }));
  a.download = filename; a.click();
  URL.revokeObjectURL(a.href);
}

/* ===== Thống kê ===== */
function renderStats() {
  const ok = history.filter((h) => h.status === "done");
  $("#statWords").textContent = fmtNum(ok.reduce((s, h) => s + h.words, 0));
  $("#statDocs").textContent = fmtNum(ok.filter((h) => h.type === "Tài liệu").length);
}

/* ===== Tabs (data-tab) ===== */
$$(".tab").forEach((btn) => btn.addEventListener("click", () => {
  const name = btn.dataset.tab;
  $$(".tab").forEach((t) => t.classList.toggle("active", t === btn));
  $$(".panel").forEach((p) => p.classList.toggle("active", p.dataset.panel === name));
}));

/* ===== Ngôn ngữ ===== */
function fillLang(sel, withAuto, value) {
  sel.innerHTML = Object.entries(LANGS)
    .filter(([k]) => withAuto || k !== "auto")
    .map(([k, v]) => `<option value="${k}">${v}</option>`).join("");
  sel.value = value;
}
fillLang($("#textSrc"), true, "auto");
fillLang($("#textTgt"), false, "en");
fillLang($("#docSrc"), true, "auto");
fillLang($("#docTgt"), false, "en");

$("#swapLang").addEventListener("click", () => {
  const s = $("#textSrc"), t = $("#textTgt");
  if (s.value === "auto") return toast("Chọn ngôn ngữ nguồn cụ thể để đổi chiều");
  [s.value, t.value] = [t.value, s.value];
});

/* ===== Dịch văn bản ===== */
const src = $("#srcText"), result = $("#result");

src.addEventListener("input", () => {
  $("#charCount").textContent = `${src.value.length} / 5000 ký tự`;
});
$("#clearBtn").addEventListener("click", () => {
  src.value = ""; src.dispatchEvent(new Event("input"));
  result.textContent = "Bản dịch sẽ hiển thị ở đây.";
  result.classList.add("placeholder");
  $("#copyBtn").disabled = true; $("#textStatus").textContent = "";
  src.focus();
});
$("#pasteBtn").addEventListener("click", async () => {
  try {
    const t = await navigator.clipboard.readText();
    src.value = (src.value + t).slice(0, 5000);
    src.dispatchEvent(new Event("input"));
  } catch { toast("Không thể đọc bộ nhớ tạm. Hãy dán bằng Ctrl+V."); }
});
$("#copyBtn").addEventListener("click", async () => {
  try { await navigator.clipboard.writeText(result.textContent); toast("Đã sao chép bản dịch"); }
  catch { toast("Không thể sao chép. Hãy bôi đen và sao chép thủ công."); }
});

$("#translateTextBtn").addEventListener("click", () => {
  const text = src.value.trim();
  const sl = $("#textSrc").value, tl = $("#textTgt").value;
  if (!text) return toast("Hãy nhập văn bản cần dịch");
  if (sl === tl) return toast("Ngôn ngữ nguồn và đích đang trùng nhau");
  const btn = $("#translateTextBtn");
  btn.disabled = true; $("#textStatus").textContent = "Đang dịch...";
  // TODO: gọi API thật (API Gateway → Lambda → Amazon Translate), ví dụ:
  // const res = await fetch(API_URL + "/translate-text", { method: "POST", body: JSON.stringify({ text, sl, tl }) });
  setTimeout(() => {
    const out = `[${LANGS[tl]}] ${text}`; // kết quả giả lập
    result.textContent = out; result.classList.remove("placeholder");
    $("#copyBtn").disabled = false; $("#textStatus").textContent = "Dịch xong";
    btn.disabled = false;
    history.unshift({ time: nowStr(), type: "Văn bản", name: text.slice(0, 60) + (text.length > 60 ? "..." : ""), src: sl, tgt: tl, words: countWords(text), status: "done" });
    renderStats(); renderHistory();
  }, 900);
});

/* ===== Dịch tài liệu ===== */
let file = null;
const drop = $("#dropZone"), input = $("#fileInput");

drop.addEventListener("click", () => input.click());
drop.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.click(); } });
["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
drop.addEventListener("drop", (e) => pick(e.dataTransfer.files[0]));
input.addEventListener("change", () => pick(input.files[0]));
$("#removeFile").addEventListener("click", resetFile);

function pick(f) {
  if (!f) return;
  const ext = f.name.split(".").pop().toLowerCase();
  if (!ALLOWED.includes(ext)) return reject(`Định dạng .${ext} chưa được hỗ trợ. Hãy chọn ${ALLOWED.map((e) => "." + e).join(", ")}.`);
  if (f.size > MAX_SIZE) return reject(`Tệp nặng ${fmtSize(f.size)}, vượt giới hạn 10 MB.`);
  if (f.size === 0) return reject("Tệp rỗng. Hãy chọn tệp có nội dung.");
  file = f;
  $("#fileError").textContent = "";
  $("#fileName").textContent = f.name;
  $("#fileSize").textContent = fmtSize(f.size);
  $("#fileInfo").hidden = false;
  $("#uploadBtn").disabled = false;
}
function reject(msg) { resetFile(); $("#fileError").textContent = msg; }
function resetFile() {
  file = null; input.value = "";
  $("#fileInfo").hidden = true; $("#uploadBtn").disabled = true; $("#fileError").textContent = "";
}

const queue = [];
$("#uploadBtn").addEventListener("click", () => {
  const sl = $("#docSrc").value, tl = $("#docTgt").value;
  if (sl === tl) return ($("#fileError").textContent = "Ngôn ngữ nguồn và đích đang trùng nhau.");
  const job = { id: Date.now(), name: file.name, size: file.size, src: sl, tgt: tl, progress: 0, status: "wait" };
  queue.unshift(job); resetFile(); renderQueue();
  runJob(job);
});

function runJob(job) {
  // TODO: upload lên S3 (presigned URL) và poll trạng thái job từ API.
  job.status = "run";
  const timer = setInterval(() => {
    job.progress = Math.min(100, job.progress + Math.round(8 + Math.random() * 14));
    if (job.progress >= 100) {
      clearInterval(timer);
      job.status = "done";
      history.unshift({ time: nowStr(), type: "Tài liệu", name: job.name, src: job.src, tgt: job.tgt, words: Math.round(job.size / 6), status: "done" });
      renderStats(); renderHistory(); toast(`Đã dịch xong ${job.name}`);
    }
    renderQueue();
  }, 600);
  renderQueue();
}

function renderQueue() {
  const ul = $("#queue");
  if (!queue.length) return;
  ul.innerHTML = queue.map((j) => {
    const [label, cls] = STATUS[j.status];
    const action = j.status === "done" ? `<button class="dl" data-dl="${j.id}">Tải bản dịch</button>` : "";
    const bar = j.status === "run" ? `<div class="bar"><i style="width:${j.progress}%"></i></div>` : "";
    return `<li>
      <span class="q-name">${esc(j.name)}</span>
      <div class="q-side"><span class="badge ${cls}">${label}${j.status === "run" ? " " + j.progress + "%" : ""}</span>${action}</div>
      <span class="q-meta">${fmtSize(j.size)} · ${LANGS[j.src]} → ${LANGS[j.tgt]}</span>${bar}
    </li>`;
  }).join("");
}
$("#queue").addEventListener("click", (e) => {
  const id = e.target.dataset.dl; if (!id) return;
  const j = queue.find((q) => String(q.id) === id);
  download(`translated-${j.name}.txt`, `Bản dịch giả lập của ${j.name} (${LANGS[j.tgt]})`);
});

/* ===== Lịch sử ===== */
function renderHistory() {
  $("#historyBody").innerHTML = history.map((h, i) => {
    const [label, cls] = STATUS[h.status];
    const action = h.status === "done" ? `<button class="dl" data-h="${i}">Tải xuống</button>` : "";
    return `<tr><td>${h.time}</td><td>${h.type}</td><td>${esc(h.name)}</td>
      <td>${LANGS[h.src]} → ${LANGS[h.tgt]}</td><td><span class="badge ${cls}">${label}</span></td><td>${action}</td></tr>`;
  }).join("");
}
$("#historyBody").addEventListener("click", (e) => {
  const i = e.target.dataset.h; if (i === undefined) return;
  const h = history[i];
  download(`translated-${h.type === "Tài liệu" ? h.name : "van-ban"}.txt`, `Bản dịch giả lập (${LANGS[h.tgt]}): ${h.name}`);
});

renderStats();
renderHistory();
