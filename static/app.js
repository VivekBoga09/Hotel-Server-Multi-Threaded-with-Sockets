const $ = (id) => document.getElementById(id);
const POLL_MS = 3000;

const state = { mode: "login", rooms: [], selected: new Set(), poll: null };

function say(text, isError = false) {
  const el = $("message");
  el.textContent = text;
  el.className = isError ? "err" : "";
}

async function api(path, method = "GET", body = null) {
  const res = await fetch(path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : null,
  });
  const data = await res.json();
  const isAuthAttempt = path === "/api/login" || path === "/api/register";
  if (res.status === 401 && !isAuthAttempt) showLoggedOut("Session ended. Please log in.");
  if (!data.ok) throw new Error(data.message);
  return data;
}

function showLoggedIn(username) {
  $("authCard").classList.add("hidden");
  $("roomsCard").classList.remove("hidden");
  $("actionsCard").classList.remove("hidden");
  $("who").classList.remove("hidden");
  $("whoName").textContent = `Signed in as ${username}`;
  clearInterval(state.poll);
  state.poll = setInterval(() => refreshRooms().catch(() => {}), POLL_MS);
}

function showLoggedOut(text) {
  clearInterval(state.poll);
  state.selected.clear();
  $("authCard").classList.remove("hidden");
  $("roomsCard").classList.add("hidden");
  $("actionsCard").classList.add("hidden");
  $("who").classList.add("hidden");
  $("password").value = "";
  say(text);
}

function roomState(room) {
  return room.mine ? "mine" : room.is_occupied ? "taken" : "free";
}

function renderRooms() {
  const buttons = state.rooms.map((room) => {
    const kind = roomState(room);
    const el = document.createElement("button");
    el.type = "button";
    el.className = `room ${kind} ${room.type}`;
    el.disabled = kind === "taken";
    el.setAttribute("aria-pressed", String(state.selected.has(room.room_number)));
    el.append(`Room ${room.room_number}`);
    const lines = [
      [room.type, "type"],
      [`$${room.price}/night`, ""],
      [kind === "mine" ? "yours" : kind, ""],
    ];
    for (const [text, cls] of lines) {
      const line = document.createElement("small");
      line.textContent = text;
      line.className = cls;
      el.append(line);
    }
    el.addEventListener("click", () => toggleRoom(room.room_number));
    return el;
  });
  $("grid").replaceChildren(...buttons);
  updateSelectionUi();
}

function selectedRooms(kind) {
  return state.rooms.filter((r) => state.selected.has(r.room_number) && roomState(r) === kind);
}

function toggleRoom(number) {
  if (state.selected.has(number)) state.selected.delete(number);
  else state.selected.add(number);
  renderRooms();
}

function nights() {
  return Number.parseInt($("nights").value, 10);
}

function updateSelectionUi() {
  const toBook = selectedRooms("free");
  const toRelease = selectedRooms("mine");
  const n = Number.isInteger(nights()) ? nights() : 0;
  const total = toBook.reduce((sum, r) => sum + r.price * n, 0);
  $("estimate").textContent = toBook.length
    ? `${toBook.length} room(s) selected to book: $${total} for ${n} night(s)`
    : "Click free rooms to book them, or your own rooms to check out.";
  $("bookBtn").disabled = toBook.length === 0;
  $("checkoutSelectedBtn").disabled = toRelease.length === 0;
}

async function refreshRooms() {
  const data = await api("/api/rooms");
  state.rooms = data.rooms;
  const valid = new Set(data.rooms.filter((r) => roomState(r) !== "taken").map((r) => r.room_number));
  state.selected = new Set([...state.selected].filter((n) => valid.has(n)));
  renderRooms();
}

async function run(button, work) {
  button.disabled = true;
  try { await work(); }
  catch (err) { say(err.message, true); }
  finally {
    button.disabled = false;
    if (!$("roomsCard").classList.contains("hidden")) updateSelectionUi();
  }
}

function setMode(mode) {
  state.mode = mode;
  for (const tab of document.querySelectorAll(".tab")) tab.setAttribute("aria-selected", String(tab.dataset.mode === mode));
  $("authSubmit").textContent = mode === "login" ? "Log in" : "Create account";
  $("password").autocomplete = mode === "login" ? "current-password" : "new-password";
}

for (const tab of document.querySelectorAll(".tab")) tab.addEventListener("click", () => setMode(tab.dataset.mode));

$("authForm").addEventListener("submit", (event) => {
  event.preventDefault();
  run($("authSubmit"), async () => {
    const data = await api(`/api/${state.mode}`, "POST", {
      username: $("username").value.trim(), password: $("password").value,
    });
    $("password").value = "";
    showLoggedIn(data.username);
    say(data.message);
    await refreshRooms();
  });
});

$("nights").addEventListener("input", updateSelectionUi);

$("bookBtn").addEventListener("click", (e) => run(e.currentTarget, async () => {
  const rooms = selectedRooms("free").map((r) => r.room_number);
  const data = await api("/api/book", "POST", { rooms, nights: nights() });
  state.selected.clear();
  say(data.message);
  await refreshRooms();
}));

async function checkout(rooms) {
  const data = await api("/api/checkout", "POST", { rooms });
  state.selected.clear();
  say(data.message);
  await refreshRooms();
}

$("checkoutSelectedBtn").addEventListener("click", (e) =>
  run(e.currentTarget, () => checkout(selectedRooms("mine").map((r) => r.room_number))));
$("checkoutAllBtn").addEventListener("click", (e) => run(e.currentTarget, () => checkout(null)));
$("mineBtn").addEventListener("click", (e) => run(e.currentTarget, async () => say((await api("/api/mine")).message)));

$("logoutBtn").addEventListener("click", (e) => run(e.currentTarget, async () => {
  await api("/api/logout", "POST", {});
  showLoggedOut("Logged out.");
}));

setMode("login");
