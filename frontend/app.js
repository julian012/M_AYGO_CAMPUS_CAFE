(function () {
  "use strict";

  var ORDERS_KEY = "campusCafe.orderIds";
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

  // ---------- API ----------
  function api(method, path, opts) {
    opts = opts || {};
    var headers = {};
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
    $("submit").disabled = items.length === 0 || submitting;
  }

  function submitOrder() {
    var items = cartItems();
    if (!items.length || submitting) return;
    var fingerprint = JSON.stringify(items);
    if (!pending || pending.fingerprint !== fingerprint) pending = { key: newKey(), fingerprint: fingerprint };
    submitting = true; renderCart(); banner("");
    api("POST", "/orders", { body: { items: items }, key: pending.key }).then(function (r) {
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
    return store(function () { return JSON.parse(localStorage.getItem(ORDERS_KEY) || "[]"); }, []);
  }
  function rememberOrder(id) {
    var ids = savedIds().filter(function (x) { return x !== id; });
    ids.unshift(id);
    store(function () { localStorage.setItem(ORDERS_KEY, JSON.stringify(ids.slice(0, 50))); });
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
      api("GET", "/orders/" + encodeURIComponent(id)).then(function (r) {
        if (r.status === 200) ul.replaceChild(renderOrder(r.data), ph);
        else ph.textContent = id + ": " + (r.status === 404 ? "no encontrado" : errText(r));
      }).catch(function () { ph.textContent = id + ": sin conexión con la API"; });
    });
  }
  function cancelOrder(id) {
    api("POST", "/orders/" + encodeURIComponent(id) + "/cancel").then(function (r) {
      if (r.status === 200) { banner("Pedido cancelado.", true); renderOrders(); }
      else banner("No se pudo cancelar: " + errText(r));
    }).catch(function () { banner("Sin respuesta de la API al cancelar."); });
  }
  function lookup(ev) {
    ev.preventDefault();
    var id = $("lookup-id").value.trim();
    if (!id) return;
    api("GET", "/orders/" + encodeURIComponent(id)).then(function (r) {
      if (r.status === 200) { rememberOrder(id); $("lookup-id").value = ""; banner(""); renderOrders(); }
      else banner(r.status === 404 ? "Pedido no encontrado." : "Error al buscar: " + errText(r));
    }).catch(function () { banner("Sin respuesta de la API."); });
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
  renderCart(); loadMenu(); renderOrders();
})();
