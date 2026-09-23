(function () {
  'use strict';

  var STORAGE_KEY = 'sos_profile';
  var LOCK_SECONDS = 30;
  var MAX_RETRIES = 3;

  var $ = function (id) { return document.getElementById(id); };
  var setup = $('setup'), main = $('main'), form = $('setup-form');
  var sosBtn = $('sos-btn'), statusEl = $('status');
  var editing = false;

  function loadProfile() {
    try { return JSON.parse(localStorage.getItem(STORAGE_KEY)) || null; } catch (e) { return null; }
  }
  function saveProfile(p) {
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(p)); } catch (e) { /* storage blocked */ }
  }

  function showMain() {
    var p = loadProfile();
    $('who').textContent = p.name;
    setup.hidden = true;
    main.hidden = false;
  }

  function showSetup(profile) {
    editing = !!profile;
    $('setup-title').textContent = editing ? 'Editar mis datos' : 'Tus datos de emergencia';
    $('setup-help').hidden = editing;
    $('setup-cancel').hidden = !editing;
    $('setup-error').hidden = true;
    $('f-files').value = '';
    $('f-name').value = profile ? profile.name : '';
    $('f-notes').value = profile ? profile.notes : '';
    var list = $('file-list');
    list.innerHTML = '';
    var files = (profile && profile.files) || [];
    files.forEach(function (name) {
      var li = document.createElement('li');
      var label = document.createElement('label');
      var cb = document.createElement('input');
      cb.type = 'checkbox'; cb.name = 'delete'; cb.value = name;
      label.appendChild(cb);
      label.appendChild(document.createTextNode(name));
      li.appendChild(label);
      list.appendChild(li);
    });
    $('current-files').hidden = files.length === 0;
    main.hidden = true;
    setup.hidden = false;
  }

  function showFormError(msg) {
    var el = $('setup-error');
    el.textContent = msg;
    el.hidden = false;
  }

  form.addEventListener('submit', function (ev) {
    ev.preventDefault();
    var p = loadProfile();
    var fd = new FormData(form);
    var url = '/api/register';
    if (editing && p) {
      url = '/api/profile/' + encodeURIComponent(p.user_id);
      fd.append('edit_key', p.edit_key);
    }
    var btn = $('setup-submit');
    btn.disabled = true;
    btn.textContent = 'Guardando…';
    fetch(url, { method: 'POST', body: fd })
      .then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (body) {
          if (!r.ok) throw new Error(body.error || ('Error ' + r.status));
          return body;
        });
      })
      .then(function (body) {
        saveProfile({
          user_id: body.user_id,
          edit_key: body.edit_key || (p && p.edit_key),
          name: body.name
        });
        showMain();
      })
      .catch(function (err) { showFormError(err.message || 'No se pudo guardar. Revisa tu conexión.'); })
      .finally(function () { btn.disabled = false; btn.textContent = 'Guardar'; });
  });

  $('setup-cancel').addEventListener('click', showMain);

  $('edit-btn').addEventListener('click', function () {
    var p = loadProfile();
    fetch('/api/profile/' + encodeURIComponent(p.user_id) + '?edit_key=' + encodeURIComponent(p.edit_key))
      .then(function (r) {
        if (r.status === 403 || r.status === 404) {
          statusEl.textContent = 'No se encontraron tus datos en el servidor. Regístrate de nuevo.';
          try { localStorage.removeItem(STORAGE_KEY); } catch (e) { /* ignore */ }
          showSetup(null);
          return null;
        }
        if (!r.ok) throw new Error();
        return r.json();
      })
      .then(function (profile) { if (profile) showSetup(profile); })
      .catch(function () { statusEl.textContent = 'No se pudo cargar tus datos. Revisa tu conexión.'; });
  });

  // ---------- SOS ----------

  var LOCATION_REASONS = { 1: 'permiso de ubicación denegado', 2: 'ubicación no disponible', 3: 'tiempo de espera agotado' };

  function tryPosition(options) {
    return new Promise(function (resolve, reject) {
      navigator.geolocation.getCurrentPosition(function (pos) {
        resolve({ lat: pos.coords.latitude, lng: pos.coords.longitude, accuracy: pos.coords.accuracy });
      }, reject, options);
    });
  }

  // First try a precise fix; if it fails (except permission denied), accept an
  // approximate or recent position so the alert rarely leaves without coordinates.
  function getLocation() {
    if (!navigator.geolocation) {
      return Promise.resolve({ location_error: 'El navegador no soporta ubicación' });
    }
    return tryPosition({ enableHighAccuracy: true, timeout: 10000, maximumAge: 0 })
      .catch(function (err) {
        if (err.code === 1) throw err;
        statusEl.textContent = 'Buscando ubicación aproximada…';
        return tryPosition({ enableHighAccuracy: false, timeout: 8000, maximumAge: 300000 });
      })
      .catch(function (err) {
        return { location_error: LOCATION_REASONS[err.code] || err.message };
      });
  }

  function postSos(payload, attempt) {
    return fetch('/api/sos', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    }).then(function (r) {
      if (r.ok) return r.json();
      // 403/404: the profile is gone or the key is wrong; retrying will not help.
      if (r.status === 403 || r.status === 404) {
        var fatal = new Error('No se encontraron tus datos en el servidor. Usa "Editar mis datos".');
        fatal.fatal = true;
        throw fatal;
      }
      throw new Error('Error ' + r.status);
    }).catch(function (err) {
      if (err.fatal || attempt >= MAX_RETRIES) throw err;
      statusEl.textContent = 'No se pudo enviar, reintentando (' + (attempt + 1) + '/' + MAX_RETRIES + ')…';
      return new Promise(function (res) { setTimeout(res, 2000); })
        .then(function () { return postSos(payload, attempt + 1); });
    });
  }

  function lockButton() {
    var left = LOCK_SECONDS;
    sosBtn.disabled = true;
    var timer = setInterval(function () {
      left -= 1;
      if (left <= 0) {
        clearInterval(timer);
        sosBtn.disabled = false;
        sosBtn.classList.remove('sent');
        sosBtn.textContent = 'SOS';
      }
    }, 1000);
  }

  sosBtn.addEventListener('click', function () {
    var p = loadProfile();
    if (!p) { showSetup(null); return; }
    sosBtn.disabled = true;
    statusEl.textContent = 'Obteniendo ubicación…';
    getLocation().then(function (loc) {
      statusEl.textContent = 'Enviando alerta…';
      var payload = { user_id: p.user_id, edit_key: p.edit_key };
      Object.keys(loc).forEach(function (k) { payload[k] = loc[k]; });
      return postSos(payload, 0).then(function () {
        sosBtn.classList.add('sent');
        sosBtn.textContent = '✅ Enviada';
        statusEl.textContent = loc.location_error
          ? 'Alerta enviada SIN ubicación (' + loc.location_error + ').'
          : 'Alerta enviada con tu ubicación (±' + Math.round(loc.accuracy) + ' m).';
        lockButton();
      });
    }).catch(function (err) {
      sosBtn.disabled = false;
      statusEl.textContent = '❌ ' + (err.message || 'No se pudo enviar la alerta.') + ' Vuelve a intentarlo.';
    });
  });

  // ---------- boot ----------
  if (loadProfile()) { showMain(); } else { showSetup(null); }
})();
