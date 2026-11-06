(function () {
  "use strict";

  var ORDERS_KEY = "campusCafe.orderIds";   // suffixed per user so accounts never share a list
  var SCOPES = "orders:create orders:read orders:cancel";
  var auth0 = null;        // SDK client once created (the SDK namespace is window.auth0); tokens stay in SDK memory
  var user = null;         // {sub, name?} for display only; authorization is never decided here
  var API_KEY = "campusCafe.apiUrl";
  var products = [];
  var cart = {};          // productId -> quantity
  // One key per submission attempt: reused on retries while the cart is unchanged,
  // discarded once the order succeeds or the cart changes.
  var pending = null;     // {key, fingerprint}
  var submitting = false;

  var $ = function (id) { return document.getElementById(id); };

  function store(fn, fallback) { try { return fn(); } catch (e) { return fallback; } }
  function apiBase() {
    var saved = store(function () { return localStorage.getItem(API_KEY); }, null);
    return (saved || window.CAMPUS_CAFE_API || "").replace(/\/+$/, "");
  }
  function money(cents) { return "$" + (cents / 100).toFixed(2); }
  function el(tag, props, children) {
    var n = document.createElement(tag);
    Object.keys(props || {}).forEach(function (k) { n[k] = props[k]; });
    (children || []).forEach(function (c) { n.appendChild(typeof c === "string" ? document.createTextNode(c) : c); });
    return n;
  }
  function banner(msg, ok) {
    var b = $("banner");
    b.textContent = msg || "";
    b.className = ok ? "ok" : "";
    b.hidden = !msg;
  }
  function newKey() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
    return "k-" + Date.now() + "-" + Math.random().toString(16).slice(2);
  }

  // ---------- Identity (Auth0 SPA SDK: Authorization Code Flow + PKCE) ----------
  // The access token is requested for the custom API audience and sent ONLY in the
  // Authorization header. We never parse it, log it, put it in a URL or persist it ourselves.
  function authConfigured() {
    var c = window.CAMPUS_CAFE_AUTH || {};
    return !!(c.domain && c.clientId && c.audience && window.auth0);
  }
  function initAuth() {
    if (!authConfigured()) { renderAuth(); return Promise.resolve(); }
    var c = window.CAMPUS_CAFE_AUTH;
    return window.auth0.createAuth0Client({
      domain: c.domain,
      clientId: c.clientId,
      authorizationParams: { redirect_uri: window.location.origin, audience: c.audience, scope: "openid profile " + SCOPES }
    }).then(function (client) {
      auth0 = client;
      var q = window.location.search;
      var cb = /[?&]code=/.test(q) && /[?&]state=/.test(q)
        ? auth0.handleRedirectCallback().then(function () { history.replaceState({}, document.title, window.location.pathname); })
        : Promise.resolve();
      return cb.catch(function () { banner("No se pudo completar el inicio de sesión."); });
    }).then(function () { return auth0.isAuthenticated(); })
      .then(function (ok) { return ok ? auth0.getUser() : null; })
      .then(function (u) { user = u; renderAuth(); })
      .catch(function () { banner("No se pudo inicializar la autenticación."); renderAuth(); });
  }
  function loggedIn() { return !!(user && typeof auth0.getTokenSilently === "function"); }
  function renderAuth() {
    $("auth-state").textContent = !authConfigured() ? "Login no configurado (solo menú público)"
      : loggedIn() ? "Sesión: " + (user.name || user.email || "usuario") : "No has iniciado sesión";
    $("login").hidden = !authConfigured() || loggedIn();
    $("logout").hidden = !loggedIn();
    $("submit").title = loggedIn() ? "" : "Inicia sesión para enviar pedidos";
    renderCart();
  }
  function login() {
    auth0.loginWithRedirect().catch(function () { banner("No se pudo iniciar sesión."); });
  }
  function logout() {
    user = null;
    auth0.logout({ logoutParams: { returnTo: window.location.origin } });
  }
  function accessToken() {
    if (!loggedIn()) return Promise.resolve(null);
    return auth0.getTokenSilently({ authorizationParams: { audience: window.CAMPUS_CAFE_AUTH.audience, scope: SCOPES } });
  }
  function ordersKey() { return ORDERS_KEY + ":" + (user ? user.sub : "anon"); }

  // ---------- API ----------
  // opts.auth = true  -> protected Orders route: attach the API access token (never the ID token).
  function api(method, path, opts) {
    opts = opts || {};
    var tokenP = opts.auth ? accessToken() : Promise.resolve(null);
    return tokenP.then(function (token) {
      if (opts.auth && !token) return { status: 401, data: { message: "Inicia sesión para usar los pedidos." } };
      var headers = {};
      if (token) headers["Authorization"] = "Bearer " + token;
      if (opts.body) headers["content-type"] = "application/json";
      if (opts.key) headers["Idempotency-Key"] = opts.key;
      return fetch(apiBase() + path, {
        method: method,
        headers: headers,
        body: opts.body ? JSON.stringify(opts.body) : undefined
      }).then(function (res) {
        return res.json().catch(function () { return null; }).then(function (data) {
          return { status: res.status, data: data };
        });
      });
    });
  }
  function errText(r) {
    var d = r.data || {};
    return (d.message || "Error " + r.status) + (d.code ? " (" + d.code + ")" : "") +
      (d.requestId ? " [requestId " + d.requestId + "]" : "");
  }

  // ---------- Menu ----------
  function loadMenu() {
    $("menu-state").textContent = "Cargando…";
    $("menu").textContent = "";
    api("GET", "/products").then(function (r) {
      if (r.status !== 200) { $("menu-state").textContent = "No se pudo cargar el menú: " + errText(r); return; }
      products = r.data.items;
      $("menu-state").textContent = "";
      products.forEach(function (p) {
        var btn = el("button", { type: "button", textContent: p.available ? "Agregar" : "No disponible", disabled: !p.available });
        btn.addEventListener("click", function () { changeQty(p.productId, 1); });
        $("menu").appendChild(el("li", { className: p.available ? "" : "unavailable" }, [
          el("strong", {}, [p.name]), el("div", { className: "price" }, [money(p.priceCents)]), btn
        ]));
      });
    }).catch(function () {
      $("menu-state").textContent = "No se pudo conectar con la API. Revisa la URL y tu conexión.";
    });
  }

  // ---------- Cart ----------
  function changeQty(id, delta) {
    var q = (cart[id] || 0) + delta;
    if (q <= 0) delete cart[id]; else cart[id] = Math.min(q, 10);
    renderCart();
  }
  function cartItems() {
    return Object.keys(cart).sort().map(function (id) { return { productId: id, quantity: cart[id] }; });
  }
  function renderCart() {
    var items = cartItems(), total = 0, ul = $("cart");
    ul.textContent = "";
    items.forEach(function (it) {
      var p = products.find(function (x) { return x.productId === it.productId; }) || { name: it.productId, priceCents: 0 };
      total += p.priceCents * it.quantity;
      var minus = el("button", { type: "button", className: "secondary", textContent: "−" });
      var plus = el("button", { type: "button", className: "secondary", textContent: "+" });
      minus.addEventListener("click", function () { changeQty(it.productId, -1); });
      plus.addEventListener("click", function () { changeQty(it.productId, 1); });
      ul.appendChild(el("li", {}, [
        el("span", {}, [p.name + " × " + it.quantity + " — " + money(p.priceCents * it.quantity)]),
        el("span", {}, [minus, " ", plus])
      ]));
    });
    $("cart-empty").hidden = items.length > 0;
    $("cart-total").textContent = money(total);
    $("submit").disabled = items.length === 0 || submitting || !loggedIn();
  }

  function submitOrder() {
    var items = cartItems();
    if (!items.length || submitting) return;
    var fingerprint = JSON.stringify(items);
    if (!pending || pending.fingerprint !== fingerprint) pending = { key: newKey(), fingerprint: fingerprint };
    submitting = true; renderCart(); banner("");
    api("POST", "/orders", { body: { items: items }, key: pending.key, auth: true }).then(function (r) {
      if (r.status === 201 || r.status === 200) {
        rememberOrder(r.data.orderId);
        cart = {}; pending = null;
        banner((r.status === 200 ? "Pedido ya registrado: " : "Pedido creado: ") + r.data.orderId, true);
        renderOrders();
      } else if (r.status === 503) {
        banner("Servicio no disponible, no se creó ningún pedido. Pulsa Enviar para reintentar (misma clave de idempotencia). " + errText(r));
      } else {
        if (r.status === 400 || r.status === 409) pending = null;   // request is wrong: a retry needs a new key
        banner("No se pudo crear el pedido: " + errText(r));
      }
    }).catch(function () {
      banner("Sin respuesta de la API. El pedido pudo o no crearse: reintenta, la misma clave evita duplicados.");
    }).then(function () { submitting = false; renderCart(); });
  }

  // ---------- Orders ----------
  function savedIds() {
    if (!loggedIn()) return [];
    return store(function () { return JSON.parse(localStorage.getItem(ordersKey()) || "[]"); }, []);
  }
  function rememberOrder(id) {
    var ids = savedIds().filter(function (x) { return x !== id; });
    ids.unshift(id);
    store(function () { localStorage.setItem(ordersKey(), JSON.stringify(ids.slice(0, 50))); });
  }
  function renderOrder(o) {
    var li = el("li", { id: "order-" + o.orderId }, []);
    var lines = o.items.map(function (i) { return i.name + " × " + i.quantity; }).join(", ");
    li.appendChild(el("span", {}, [
      el("span", { className: "status " + o.status, textContent: o.status }), " · " + money(o.totalCents) + " · " + lines,
      el("br"), el("code", {}, [o.orderId])
    ]));
    if (o.status === "CREATED") {
      var c = el("button", { type: "button", className: "secondary", textContent: "Cancelar" });
      c.addEventListener("click", function () { cancelOrder(o.orderId); });
      li.appendChild(c);
    }
    return li;
  }
  function renderOrders() {
    var ul = $("orders");
    ul.textContent = "";
    savedIds().forEach(function (id) {
      var ph = el("li", {}, ["Cargando " + id + "…"]);
      ul.appendChild(ph);
      api("GET", "/orders/" + encodeURIComponent(id), { auth: true }).then(function (r) {
        if (r.status === 200) ul.replaceChild(renderOrder(r.data), ph);
        else ph.textContent = id + ": " + (r.status === 404 ? "no encontrado" : errText(r));
      }).catch(function () { ph.textContent = id + ": sin conexión con la API"; });
    });
  }
  var lookedUp = null;    // orderId currently shown in the search result box
  function renderLookup() {
    var box = $("lookup-result");
    box.textContent = "";
    if (!lookedUp) return;
    var id = lookedUp;
    api("GET", "/orders/" + encodeURIComponent(id), { auth: true }).then(function (r) {
      if (id !== lookedUp) return;
      if (r.status === 200) {
        var close = el("button", { type: "button", className: "secondary", textContent: "Cerrar resultado" });
        close.addEventListener("click", function () { lookedUp = null; renderLookup(); });
        var item = renderOrder(r.data);
        item.appendChild(close);
        box.appendChild(el("ul", {}, [item]));
      } else {
        banner(r.status === 404 ? "Pedido no encontrado." : "Error al buscar: " + errText(r));
        lookedUp = null;
      }
    }).catch(function () { banner("Sin respuesta de la API."); });
  }
  function cancelOrder(id) {
    api("POST", "/orders/" + encodeURIComponent(id) + "/cancel", { auth: true }).then(function (r) {
      if (r.status === 200) { banner("Pedido cancelado.", true); renderOrders(); renderLookup(); }
      else banner("No se pudo cancelar: " + errText(r));
    }).catch(function () { banner("Sin respuesta de la API al cancelar."); });
  }
  function lookup(ev) {
    ev.preventDefault();
    var id = $("lookup-id").value.trim();
    if (!id) return;
    banner("");
    lookedUp = id;          // search shows ONLY this order; "Mis pedidos" is not changed
    renderLookup();
  }

  // ---------- Init ----------
  $("api-url").value = apiBase();
  $("api-save").addEventListener("click", function () {
    store(function () { localStorage.setItem(API_KEY, $("api-url").value.trim()); });
    loadMenu(); renderOrders();
  });
  $("submit").addEventListener("click", submitOrder);
  $("clear").addEventListener("click", function () { cart = {}; renderCart(); });
  $("lookup").addEventListener("submit", lookup);
  $("login").addEventListener("click", login);
  $("logout").addEventListener("click", logout);
  renderCart(); loadMenu();
  initAuth().then(renderOrders);
})();
