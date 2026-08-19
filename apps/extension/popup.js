// Popup: pick a user, list their approved matches, fill the current tab's form.
// No auth (project decision — CLAUDE.md §3.2/§7.1) so "which user" is a local
// dropdown here too, same as the dashboard's, and not shared with it — there is
// no session to share.

const API_BASE = "http://localhost:8000";
const STORAGE_KEY = "applyloop.userId";

const userSelect = document.getElementById("user");
const list = document.getElementById("list");

async function api(path, init) {
  const res = await fetch(`${API_BASE}${path}`, init);
  if (!res.ok) throw new Error(`${res.status} ${await res.text().catch(() => "")}`);
  return res.json();
}

async function init() {
  const { items: users } = await api("/users?limit=100");
  const stored = (await chrome.storage.local.get(STORAGE_KEY))[STORAGE_KEY];
  userSelect.innerHTML = users.map((u) => `<option value="${u.id}">${u.email}</option>`).join("");
  if (stored && users.some((u) => u.id === stored)) userSelect.value = stored;

  userSelect.onchange = () => {
    chrome.storage.local.set({ [STORAGE_KEY]: userSelect.value });
    loadMatches();
  };

  if (users.length) await loadMatches();
  else list.innerHTML = `<p class="empty">no users found</p>`;
}

async function loadMatches() {
  chrome.storage.local.set({ [STORAGE_KEY]: userSelect.value });
  list.innerHTML = `<p class="status">loading…</p>`;
  const userId = userSelect.value;
  const { items } = await api(`/users/${userId}/pipeline?status=approved&limit=50`);

  if (items.length === 0) {
    list.innerHTML = `<p class="empty">approve a match in the dashboard first</p>`;
    return;
  }

  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const tabHost = tab?.url ? new URL(tab.url).hostname : null;

  list.innerHTML = "";
  for (const row of items) {
    const jobHost = new URL(row.job.url).hostname;
    const onPage = tabHost === jobHost;
    const el = document.createElement("div");
    el.className = "row";
    el.innerHTML = `
      <div class="title">${row.job.title}</div>
      <div class="sub">${row.job.company}${onPage ? " · this tab" : ""}</div>
      <button ${onPage ? "" : "disabled"}>${onPage ? "Fill this form" : "Open the posting first"}</button>
    `;
    el.querySelector("button").onclick = (e) => fill(userId, row, tab.id, e.target);
    list.appendChild(el);
  }
}

async function fill(userId, row, tabId, button) {
  button.disabled = true;
  button.textContent = "filling…";

  try {
    const profile = await findProfile(userId);
    const documents = await loadDocuments(row.documents);
    const basics = profile?.parsed_json?.basics ?? {};
    const [first, ...rest] = (basics.name ?? "").split(" ");

    const payload = {
      match_id: row.match_id,
      fields: {
        first_name: first || null,
        last_name: rest.join(" ") || null,
        email: basics.email ?? null,
        phone: basics.phone ?? null,
        linkedin_url: basics.url && /linkedin\.com/i.test(basics.url) ? basics.url : null,
      },
      documents,
    };

    const result = await chrome.tabs.sendMessage(tabId, {
      type: "APPLYLOOP_FILL",
      payload,
    });

    if (result.error) {
      button.textContent = "not a Greenhouse form";
      console.error(result.error);
      return;
    }

    await recordApplication(row.match_id, result.holds);
    button.textContent = result.holds.length
      ? `filled — ${result.holds.length} hold(s)`
      : "filled";
  } catch (err) {
    button.textContent = "error — see console";
    console.error(err);
  }
}

async function findProfile(userId) {
  const { items } = await api("/profiles?limit=200");
  return items.find((p) => p.user_id === userId) ?? null;
}

// Documents are fetched here (the popup, not the content script) because MV3
// content-script fetches are still subject to the page's CORS, while an
// extension page's fetch is not — it only needs `host_permissions`, already
// declared for localhost:8000 and localhost:9000 (the presigned-URL host).
async function loadDocuments(docs) {
  const out = {};
  for (const doc of docs) {
    const res = await fetch(`${API_BASE}/documents/${doc.id}/download`);
    if (!res.ok) continue;
    const blob = await res.blob();
    out[doc.type] = {
      dataUrl: await blobToDataUrl(blob),
      filename: `${doc.type}-v${doc.version}.pdf`,
    };
  }
  return out;
}

function blobToDataUrl(blob) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = reject;
    reader.readAsDataURL(blob);
  });
}

// Status stays "pending" — extensions never submit (§1.2's hard rules), so
// "filled" would be a lie about what actually happened. The HOLD list is what
// DEMO_PLAN's beat 7 needs on the record, and `error` is the field that already
// exists for it (ApplicationUpdate.error) — no schema change needed.
async function recordApplication(matchId, holds) {
  const errorText = holds.length
    ? holds.map((h) => `${h.field}: ${h.reason}`).join("\n")
    : null;

  let applicationId = null;
  const res = await fetch(`${API_BASE}/applications`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ match_id: matchId, method: "extension", status: "pending" }),
  });

  if (res.status === 201) {
    applicationId = (await res.json()).id;
  } else if (res.status === 409) {
    // Already recorded for this match+method (§3.4's duplicate guard) — a re-fill is
    // expected, not exceptional. Find the existing row so a fresh HOLD list still
    // lands somewhere.
    const { items } = await api("/applications?limit=100");
    applicationId =
      items.find((a) => a.match_id === matchId && a.method === "extension")?.id ?? null;
  } else {
    console.error("could not record the application", res.status, await res.text());
    return;
  }

  if (applicationId && errorText) {
    await api(`/applications/${applicationId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ error: errorText }),
    });
  }
}

init();
