document.getElementById("login").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const err = document.getElementById("err");
  const btn = ev.target.querySelector("button");
  err.textContent = ""; btn.disabled = true;
  try {
    const res = await fetch("/api/login", {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-JobRadar": "1" },
      body: JSON.stringify({ password: document.getElementById("password").value }),
    });
    if (res.ok) { window.location.href = "/"; return; }
    let msg = `Error ${res.status}`;
    try { msg = (await res.json()).detail || msg; } catch (_) { /* ignore */ }
    err.textContent = msg;
  } catch (_) {
    err.textContent = "No se pudo conectar. Si el servidor estaba dormido, espera unos segundos y reintenta.";
  } finally { btn.disabled = false; }
});
