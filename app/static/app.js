/**
 * Browser client for the pricing exception API.
 *
 * Keeps one state object and redraws the request list and detail pane
 * from it after every change. The "Signed in as" picker sets the
 * X-User-Id header sent with each call. Action buttons appear only when
 * the API would accept the action, and the API enforces every rule
 * itself. Plain JavaScript, with no framework and no build step.
 */

const FILTERS = ["all", "pending", "approved", "declined", "withdrawn"];

const state = {
  users: [],
  applications: [],
  requests: [],
  user: localStorage.getItem("pricing.user") || "arthur",
  filter: "pending",
  selectedId: null,
  formMode: null,
  formIdempotencyKey: null,
};

const byId = (id) => document.getElementById(id);

/**
 * Call the API as the current user.
 * @param {string} path Path under /api, for example "/requests".
 * @param {RequestInit} [options] Fetch options. The body is sent as-is.
 * @returns {Promise<any>} Parsed JSON body.
 * @throws {Error} With the API's error message when the call fails.
 */
async function callApi(path, options = {}) {
  const response = await fetch(`/api${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      "X-User-Id": state.user,
      ...options.headers,
    },
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const fallback = `Request failed (${response.status})`;
    throw new Error(body.error?.message || fallback);
  }
  return body;
}

/**
 * Send a JSON body with POST.
 * @param {string} path Path under /api.
 * @param {object} [body] Body to send.
 * @param {object} [headers] Extra headers.
 * @returns {Promise<any>} Parsed JSON body.
 */
function postJson(path, body, headers = {}) {
  const options = { method: "POST", headers };
  if (body !== undefined) options.body = JSON.stringify(body);
  return callApi(path, options);
}

/**
 * Get the signed-in user.
 * @returns {{id: string, name: string, roles: string[]}} Current user.
 */
function currentUser() {
  return state.users.find((user) => user.id === state.user);
}

/**
 * Check whether the signed-in user has a role.
 * @param {string} role Role name.
 * @returns {boolean} True if the user has the role.
 */
function hasRole(role) {
  return currentUser()?.roles.includes(role) ?? false;
}

/**
 * Look up a user's display name.
 * @param {string} id User id.
 * @returns {string} Display name, or the id if unknown.
 */
function userName(id) {
  return state.users.find((user) => user.id === id)?.name ?? id;
}

/**
 * Find an application by id.
 * @param {string} id Application id.
 * @returns {object|undefined} The application, if loaded.
 */
function findApplication(id) {
  return state.applications.find((application) => application.id === id);
}

/**
 * Format basis points as a percentage.
 * @param {number} bps Value in basis points.
 * @returns {string} For example "4.21%".
 */
function formatPercent(bps) {
  return `${(bps / 100).toFixed(2)}%`;
}

/**
 * Format a timestamp for display.
 * @param {string} iso ISO 8601 timestamp.
 * @returns {string} Short local date and time.
 */
function formatDate(iso) {
  return new Date(iso).toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

/**
 * Capitalise the first letter of a word.
 * @param {string} word Word to change.
 * @returns {string} The word with a capital first letter.
 */
function capitalize(word) {
  return word[0].toUpperCase() + word.slice(1);
}

/**
 * Escape text before putting it into HTML.
 * @param {string} text Untrusted text.
 * @returns {string} HTML-safe text.
 */
function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text ?? "";
  return div.innerHTML;
}

/**
 * Build a status pill.
 * @param {string} status Request status.
 * @returns {string} HTML for the pill.
 */
function statusPill(status) {
  return `<span class="pill pill--${status}">${status}</span>`;
}

/**
 * Show a short message at the bottom of the screen.
 * @param {string} message Text to show.
 * @returns {void}
 */
function showToast(message) {
  const banner = byId("toast");
  banner.textContent = message;
  banner.classList.add("show");
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => banner.classList.remove("show"), 2600);
}

/**
 * Reload applications and requests, then re-render.
 * @returns {Promise<void>}
 */
async function reloadData() {
  [state.applications, state.requests] = await Promise.all([
    callApi("/applications"),
    callApi("/requests"),
  ]);
  renderList();
  await renderDetail();
}

/**
 * Render the status filter buttons.
 * @returns {void}
 */
function renderFilters() {
  byId("filters").innerHTML = FILTERS.map((filter) => {
    const pressed = filter === state.filter;
    return `<button type="button" data-filter="${filter}"
      aria-pressed="${pressed}">${capitalize(filter)}</button>`;
  }).join("");
}

/**
 * Render one row of the request list.
 * @param {object} request Request from the API.
 * @returns {string} HTML list item.
 */
function renderRow(request) {
  const customer =
    findApplication(request.application_id)?.customer_name ?? "";
  const current = request.id === state.selectedId;
  const requester = escapeHtml(userName(request.requested_by));
  return `<li>
    <button class="row" type="button" data-id="${request.id}"
      aria-current="${current}">
      <span>
        <span class="row__title">${escapeHtml(request.application_id)}</span>
        <span class="row__sub">
          ${escapeHtml(customer)} · ${requester}
        </span>
      </span>
      <span class="row__side">
        ${statusPill(request.status)}
        <span class="row__sub">${request.discount_bps} bps</span>
      </span>
    </button>
  </li>`;
}

/**
 * Render the request list for the current filter.
 * @returns {void}
 */
function renderList() {
  renderFilters();
  byId("new-request").hidden = !hasRole("relationship_manager");
  const rows = state.requests.filter(
    (request) => state.filter === "all" || request.status === state.filter,
  );
  const label = state.filter === "all" ? "" : `${state.filter} `;
  byId("request-list").innerHTML = rows.length
    ? rows.map(renderRow).join("")
    : `<li class="empty">No ${label}requests.</li>`;
}

/**
 * Build the decision block of a decided request.
 * @param {object} request Request from the API.
 * @returns {string} HTML, or an empty string while pending.
 */
function renderDecision(request) {
  if (!request.decided_by) return "";
  const verb = request.status === "approved" ? "Approved" : "Declined";
  const note = request.decision_note
    ? `<p class="muted">${escapeHtml(request.decision_note)}</p>`
    : "";
  const reviewer = escapeHtml(userName(request.decided_by));
  const decidedOn = formatDate(request.decided_at);
  return `<div class="block">
    <span class="label">Decision</span>
    <p>${verb} by ${reviewer} on ${decidedOn}</p>
    ${note}
  </div>`;
}

/**
 * Build the action buttons the current user may use on a request.
 * @param {object} request Request from the API.
 * @returns {string} HTML for the actions, or an empty string.
 */
function renderActions(request) {
  if (request.status !== "pending") return "";
  const reviewer = hasRole("reviewer") && request.requested_by !== state.user;
  const owner = request.requested_by === state.user;
  if (!reviewer && !owner) return "";
  const note = `<textarea id="decision-note" rows="2"
    placeholder="Note (required when declining)"></textarea>`;
  const review = `
    <button class="button button--primary" data-action="approve"
      type="button">Approve</button>
    <button class="button button--danger" data-action="decline"
      type="button">Decline</button>`;
  const manage = `
    <button class="button" data-action="revise"
      type="button">Change discount</button>
    <button class="button button--danger" data-action="withdraw"
      type="button">Withdraw</button>`;
  return `<div class="actions">
    ${reviewer ? note : ""}
    <div class="actions__row">
      ${reviewer ? review : ""}${owner ? manage : ""}
    </div>
  </div>`;
}

/**
 * Build one history entry.
 * @param {object} event Event from the API.
 * @returns {string} HTML list item.
 */
function renderEvent(event) {
  const earlier =
    event.request_id === state.selectedId
      ? ""
      : ` <span class="muted">(earlier version)</span>`;
  const note = event.note
    ? `<blockquote>${escapeHtml(event.note)}</blockquote>`
    : "";
  const actor = escapeHtml(userName(event.actor_id));
  return `<li class="is-${event.type}">
    <strong>${capitalize(event.type)}</strong> by ${actor}
    · ${event.discount_bps} bps${earlier}<br />
    <span class="muted">${formatDate(event.at)}</span>
    ${note}
  </li>`;
}

/**
 * Build the rate summary of a request.
 * @param {object} application Application from the API.
 * @param {object} request Request from the API.
 * @returns {string} HTML for the rate figures.
 */
function renderRates(application, request) {
  const discountedRate = application.standard_rate_bps - request.discount_bps;
  const rateTile = (label, value) => `<div>
      <span class="label">${label}</span>
      <span class="stat__value">${value}</span>
    </div>`;
  return `<div class="stats">
    ${rateTile("Standard rate", formatPercent(application.standard_rate_bps))}
    ${rateTile("Discount", `${request.discount_bps} bps`)}
    ${rateTile("Rate after discount", formatPercent(discountedRate))}
  </div>`;
}

/**
 * Render the selected request with its actions and history.
 * @returns {Promise<void>}
 */
async function renderDetail() {
  const pane = byId("detail");
  if (!state.selectedId) {
    pane.innerHTML = `<div class="empty">
      Select a request to see its details and history.
    </div>`;
    return;
  }
  const history = await callApi(`/requests/${state.selectedId}/history`);
  const request = history.request;
  const application = findApplication(request.application_id);
  const events = history.events.map(renderEvent).join("");
  const heading = `${escapeHtml(request.application_id)} ${statusPill(
    request.status,
  )}`;
  const requester = escapeHtml(userName(request.requested_by));
  pane.innerHTML = `<div class="detail">
    <div>
      <h2 class="detail__title">${heading}</h2>
      <span class="muted">
        ${escapeHtml(application.customer_name)} · requested by
        ${requester} on ${formatDate(request.created_at)}
      </span>
    </div>
    ${renderRates(application, request)}
    <div class="block">
      <span class="label">Reason</span>
      <p>${escapeHtml(request.reason)}</p>
    </div>
    ${renderDecision(request)}
    ${renderActions(request)}
    <div class="block">
      <span class="label">History</span>
      <ol class="timeline">${events}</ol>
    </div>
  </div>`;
}

/**
 * Build the options of the application picker.
 * @param {object[]} applications Applications to offer.
 * @returns {string} HTML option elements.
 */
function renderApplicationOptions(applications) {
  return applications
    .map((application) => {
      const text = `${application.id} · ${application.customer_name}`;
      const rate = formatPercent(application.standard_rate_bps);
      const label = `${escapeHtml(text)} · ${rate}`;
      return `<option value="${application.id}">${label}</option>`;
    })
    .join("");
}

/**
 * Open the sheet to create a request or change a pending one.
 * @param {"create"|"revise"} mode Which form to show.
 * @param {object} [request] Request being changed, when revising.
 * @returns {void}
 */
function openRequestForm(mode, request) {
  const creating = mode === "create";
  state.formMode = mode;
  state.formIdempotencyKey = crypto.randomUUID();
  const choices = creating
    ? state.applications.filter((application) => !application.open_request_id)
    : [findApplication(request.application_id)];
  byId("sheet-title").textContent = creating
    ? "New request"
    : "Change discount";
  byId("f-submit").textContent = creating ? "Submit" : "Submit new version";
  byId("f-application").disabled = !creating;
  byId("f-application").innerHTML = renderApplicationOptions(choices);
  byId("f-discount").value = request?.discount_bps ?? "";
  byId("f-reason").value = request?.reason ?? "";
  byId("f-error").textContent = creating
    ? ""
    : "The current request is withdrawn and replaced by a new one.";
  updateRatePreview();
  byId("sheet").showModal();
}

/**
 * Show the resulting rate under the discount field.
 * @returns {void}
 */
function updateRatePreview() {
  const application = findApplication(byId("f-application").value);
  const bps = Number(byId("f-discount").value);
  if (!application || bps <= 0) {
    byId("f-preview").textContent = "";
    return;
  }
  const from = formatPercent(application.standard_rate_bps);
  const to = formatPercent(application.standard_rate_bps - bps);
  byId("f-preview").textContent = `${from} → ${to}`;
}

/**
 * Send the sheet's form to the API.
 * @param {SubmitEvent} event Form submit event.
 * @returns {Promise<void>}
 */
async function submitRequestForm(event) {
  event.preventDefault();
  const creating = state.formMode === "create";
  const body = {
    discount_bps: Number(byId("f-discount").value),
    reason: byId("f-reason").value,
  };
  const headers = { "Idempotency-Key": state.formIdempotencyKey };
  const path = creating
    ? "/requests"
    : `/requests/${state.selectedId}/revisions`;
  if (creating) body.application_id = byId("f-application").value;
  byId("f-submit").disabled = true;
  try {
    const created = await postJson(path, body, headers);
    byId("sheet").close();
    state.selectedId = created.id;
    state.filter = "pending";
    showToast(creating ? "Request submitted" : "New version submitted");
    await reloadData();
  } catch (error) {
    byId("f-error").textContent = error.message;
  } finally {
    byId("f-submit").disabled = false;
  }
}

/**
 * Run an action button from the detail pane.
 * @param {string} action One of approve, decline, withdraw, revise.
 * @returns {Promise<void>}
 */
async function runDetailAction(action) {
  const path = `/requests/${state.selectedId}`;
  try {
    if (action === "revise") {
      openRequestForm("revise", await callApi(path));
      return;
    }
    if (action === "withdraw") {
      await postJson(`${path}/withdraw`);
      showToast("Request withdrawn");
    } else {
      const note = byId("decision-note")?.value || null;
      const outcome = action === "approve" ? "approved" : "declined";
      await postJson(`${path}/decision`, { outcome, note });
      showToast(`Request ${outcome}`);
    }
    await reloadData();
  } catch (error) {
    showToast(error.message);
  }
}

/**
 * Fill the "Signed in as" picker.
 * @returns {void}
 */
function renderUserPicker() {
  byId("user-select").innerHTML = state.users
    .map((user) => {
      const roles = user.roles
        .map((role) => role.replace("_", " "))
        .join(", ");
      const label = `${escapeHtml(user.name)} · ${roles}`;
      return `<option value="${user.id}">${label}</option>`;
    })
    .join("");
  byId("user-select").value = state.user;
}

/**
 * Connect the page's controls to their handlers.
 * @returns {void}
 */
function bindEvents() {
  byId("user-select").addEventListener("change", async (event) => {
    state.user = event.target.value;
    localStorage.setItem("pricing.user", state.user);
    await reloadData();
  });
  byId("filters").addEventListener("click", (event) => {
    const filter = event.target.closest("[data-filter]")?.dataset.filter;
    if (!filter) return;
    state.filter = filter;
    renderList();
  });
  byId("request-list").addEventListener("click", async (event) => {
    const id = event.target.closest("[data-id]")?.dataset.id;
    if (!id) return;
    state.selectedId = id;
    renderList();
    await renderDetail();
  });
  byId("detail").addEventListener("click", (event) => {
    const action = event.target.closest("[data-action]")?.dataset.action;
    if (action) runDetailAction(action);
  });
  byId("new-request").addEventListener("click", () =>
    openRequestForm("create"),
  );
  byId("f-cancel").addEventListener("click", () => byId("sheet").close());
  byId("f-discount").addEventListener("input", updateRatePreview);
  byId("f-application").addEventListener("change", updateRatePreview);
  byId("sheet-form").addEventListener("submit", submitRequestForm);
}

/**
 * Load users, wire up events and draw the first screen.
 * @returns {Promise<void>}
 */
async function startApp() {
  state.users = await callApi("/users");
  if (!currentUser()) state.user = state.users[0].id;
  renderUserPicker();
  bindEvents();
  await reloadData();
}

startApp().catch((error) => showToast(error.message));
